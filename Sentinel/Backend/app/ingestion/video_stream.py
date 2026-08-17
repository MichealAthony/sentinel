"""
app/ingestion/video_stream.py

BUFFER DESIGN
-------------
Buffer size is derived from system parameters, not an arbitrary constant:

    buffer_size = target_fps * max_gap_seconds

This guarantees the buffer can hold all processable frames for the entire
duration a camera might be away from the scheduler. Frames beyond this
window have no investigative value so dropping them is a deliberate policy,
not a silent data loss.

KEYFRAME SAMPLING
-----------------
The background thread reads every frame from the source to advance the
capture position correctly, but only queues every Nth frame:

    N = max(1, floor(source_fps / target_fps))

For 42fps source / 5fps target: N=8, queue every 8th frame.
For 30fps source / 5fps target: N=6, queue every 6th frame.

This keeps memory bounded at ~target_fps * max_gap * frame_size
regardless of source FPS. Since processing only happens at target_fps
anyway, no investigatively valuable frames are lost.

DROPPED FRAME TRACKING
-----------------------
When the buffer is full and a keyframe must be dropped, a counter is
incremented. The orchestrator can check this to detect when the scheduler
round trip is exceeding max_gap_seconds and warn accordingly.

VIDEO FILE LOOPING
------------------
File sources loop back to frame 0 on end so the stream stays alive
during testing. Live RTSP sources reconnect on failure.
"""

import cv2
import threading
import time
import math
from queue import Queue, Full
from typing import Optional


# System-wide policy: maximum acceptable gap in seconds before a buffered
# frame is considered too stale to have investigative value.
# Buffer size is derived from this — change here to change everywhere.
MAX_GAP_SECONDS = 60

# Default assumptions for sources where FPS cannot be detected
DEFAULT_SOURCE_FPS = 30
DEFAULT_TARGET_FPS = 5


class VideoStream:
    def __init__(
        self,
        camera_id: str,
        source=0,
        source_fps: Optional[float] = None,
        target_fps: int = DEFAULT_TARGET_FPS,
        width: Optional[int] = None,
        height: Optional[int] = None,
        reconnect_delay: float = 2.0,
        time_offset: float = 0.0,
    ):
        self.camera_id = camera_id
        self.source = source
        self.target_fps = target_fps
        self.width = width
        self.height = height
        self.reconnect_delay = reconnect_delay
        self._time_offset = time_offset

        self._is_file = isinstance(source, str) and not source.startswith("rtsp")

        # Source FPS — detected from stream if not provided
        # Used only to calculate sampling interval, not for timestamps
        self._source_fps = source_fps  # None means detect on connect

        # Derived on connect once source_fps is known
        self._sample_every_n: int = 1
        self._buffer_size: int = target_fps * MAX_GAP_SECONDS

        self.cap = None
        self.queue: Queue = Queue(maxsize=self._buffer_size)
        self.stopped = False
        self.thread: Optional[threading.Thread] = None

        # Diagnostics
        self._frames_read: int = 0
        self._frames_queued: int = 0
        self._frames_dropped: int = 0  # keyframes dropped due to full buffer

    # =========================================================
    # CONNECT
    # =========================================================
    def _connect(self):
        self.cap = cv2.VideoCapture(self.source)
        if not self.cap.isOpened():
            raise RuntimeError(f"[Camera {self.camera_id}] Could not open source")

        if self.width:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        if self.height:
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)

        # Detect source FPS from stream if not provided
        detected_fps = self.cap.get(cv2.CAP_PROP_FPS)
        if self._source_fps is None:
            self._source_fps = detected_fps if detected_fps > 0 else DEFAULT_SOURCE_FPS

        # Recalculate derived values now that source_fps is known
        self._sample_every_n = max(1, math.floor(self._source_fps / self.target_fps))
        self._buffer_size = self.target_fps * MAX_GAP_SECONDS

        # Resize queue if buffer_size changed from default
        if self.queue.maxsize != self._buffer_size:
            self.queue = Queue(maxsize=self._buffer_size)

        print(
            f"[Camera {self.camera_id}] Connected — "
            f"source_fps={self._source_fps:.1f} "
            f"target_fps={self.target_fps} "
            f"sample_every={self._sample_every_n} frames "
            f"buffer={self._buffer_size} keyframes "
            f"(covers {MAX_GAP_SECONDS}s gap)"
        )

    # =========================================================
    # START
    # =========================================================
    def start(self):
        self._connect()
        self.thread = threading.Thread(
            target=self._update, daemon=True, name=f"VideoStream-{self.camera_id}"
        )
        self.thread.start()
        return self

    # =========================================================
    # BACKGROUND READ LOOP
    # =========================================================
    def _update(self):
        frame_index = 0

        while not self.stopped:
            if self.cap is None or not self.cap.isOpened():
                print(f"[Camera {self.camera_id}] Reconnecting...")
                time.sleep(self.reconnect_delay)
                try:
                    self._connect()
                    frame_index = 0
                except Exception as e:
                    print(f"[Camera {self.camera_id}] Reconnect failed: {e}")
                    continue

            ret, frame = self.cap.read()
            self._frames_read += 1

            if not ret:
                if self._is_file:
                    # Release and reopen for reliable looping — cap.set() can
                    # silently fail on some codecs/platforms (especially .mov on macOS).
                    print(f"[Camera {self.camera_id}] End of file — looping.")
                    self.cap.release()
                    try:
                        self._connect()
                    except Exception as e:
                        print(f"[Camera {self.camera_id}] Loop reopen failed: {e}")
                    frame_index = 0
                    continue
                else:
                    print(f"[Camera {self.camera_id}] Frame grab failed — reconnecting.")
                    self.cap.release()
                    self.cap = None
                    frame_index = 0
                    continue

            # Throttle file reads to real-time speed so the queue doesn't fill
            # with future-frames before the orchestrator can consume them.
            if self._is_file:
                time.sleep(1.0 / self._source_fps)

            frame_index += 1

            # Keyframe sampling: only queue every Nth frame
            # All frames are read to advance capture position correctly
            if frame_index % self._sample_every_n != 0:
                continue

            timestamp = self._get_timestamp()

            packet = {
                "camera_id": self.camera_id,
                "timestamp": timestamp,
                "frame": frame,
            }

            self._frames_queued += 1

            try:
                self.queue.put_nowait(packet)
            except Full:
                # Buffer full — scheduler round trip may be exceeding
                # MAX_GAP_SECONDS. Drop oldest frame and log.
                self._frames_dropped += 1
                try:
                    self.queue.get_nowait()
                except Exception:
                    pass
                try:
                    self.queue.put_nowait(packet)
                except Full:
                    pass

    # =========================================================
    # TIMESTAMP
    # =========================================================
    def _get_timestamp(self) -> float:
        """
        System time plus per-camera offset.
        time_offset is 0 for real RTSP cameras. For file-based test sources
        it is set to a negative value per camera so that sightings across
        the network are spread out in time, producing credible routes.
        """
        return time.time() + self._time_offset

    # =========================================================
    # READ (called by orchestrator)
    # =========================================================
    def read(self):
        if self.queue.empty():
            return None
        return self.queue.get()

    # =========================================================
    # STOP
    # =========================================================
    def stop(self):
        self.stopped = True
        if self.cap:
            self.cap.release()

    # =========================================================
    # DIAGNOSTICS
    # =========================================================
    def diagnostics(self) -> dict:
        return {
            "camera_id": self.camera_id,
            "source_fps": self._source_fps,
            "target_fps": self.target_fps,
            "sample_every_n": self._sample_every_n,
            "buffer_size": self._buffer_size,
            "buffer_used": self.queue.qsize(),
            "buffer_utilisation_pct": round(
                self.queue.qsize() / max(1, self._buffer_size) * 100, 1
            ),
            "frames_read": self._frames_read,
            "frames_queued": self._frames_queued,
            "frames_dropped": self._frames_dropped,
            "max_gap_seconds": MAX_GAP_SECONDS,
        }