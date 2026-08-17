"""
app/pipeline_orchestrator.py

CHANGES FROM PREVIOUS VERSION
-------------------------------
1. Calls scheduler.reset() on every start() so stale state from a
   previous run never affects scheduling order on restart.

2. Uses confirm/release pattern instead of relying on scheduler.next()
   to re-enqueue:
     - confirm(camera_id) after a frame is read and submitted
     - release(camera_id) when no frame available or frame skipped
   This ensures cameras are only penalised for slots they actually used.

3. On None packet (stream stall), calls release() so the camera keeps
   its wait time and other cameras get scheduled during the gap.
"""

import time
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from app.ingestion.video_stream import VideoStream
from app.ingestion.frame_sampler import FrameSampler
from app.services.camera_registry import CameraRegistry, CameraConfig
from app.services.camera_scheduler import CameraScheduler
from app.services.vehicle_worker import VehicleWorker
from app.services.index_publisher import IndexPublisher
from app.services.stream_buffer import StreamBuffer


class PipelineOrchestrator:
    def __init__(self, registry: CameraRegistry, scheduler: CameraScheduler,
                 worker: VehicleWorker, publisher: IndexPublisher,
                 stream_buffer: StreamBuffer, max_workers: int = 4, target_fps: int = 5):
        self.registry = registry
        self.scheduler = scheduler
        self.worker = worker
        self.publisher = publisher
        self.stream_buffer = stream_buffer
        self.sampler = FrameSampler(target_fps=target_fps)
        self._max_workers = max_workers
        self.executor: Optional[ThreadPoolExecutor] = None
        self._streams: dict[str, VideoStream] = {}
        self._processed_frames: dict[str, Any] = {}
        self._frames_lock = threading.Lock()
        self._running = False
        self._loop_thread: Optional[threading.Thread] = None

    def start(self):
        if self._running:
            return
        self._running = True
        self.executor = ThreadPoolExecutor(max_workers=self._max_workers)

        # ── HIGH-PRIORITY COMPUTE LANE (reserved, not yet instantiated) ───────────
        # To activate the visual feature search dedicated compute lane:
        #
        #   from app.services.clip_embedding_service import clip_service
        #   from app.services.active_query_registry import active_query_registry
        #
        #   self.priority_executor = ThreadPoolExecutor(max_workers=2,
        #                                               thread_name_prefix="PriorityWorker")
        #
        # High-priority visual search frames submit directly to self.priority_executor,
        # bypassing the CameraScheduler priority heap entirely. This ensures a visual
        # feature search is never queued behind routine background camera indexing
        # regardless of current system load.
        #
        # The standard self.executor remains unchanged. The two pools share no state.
        # Activation: uncomment the four lines above, then in _loop() route
        # priority frames to self.priority_executor.submit() instead of self.executor.
        # ─────────────────────────────────────────────────────────────────────────────

        # Reset scheduler so stale state from a previous run is cleared
        self.scheduler.reset()

        self.publisher.start()
        for cam in self.registry.all():
            self._open_stream(cam)

        self._loop_thread = threading.Thread(
            target=self._loop, daemon=True, name="PipelineOrchestrator"
        )
        self._loop_thread.start()
        print("[Orchestrator] Pipeline started")

    def stop(self):
        self._running = False
        if self._loop_thread:
            self._loop_thread.join(timeout=10)
            self._loop_thread = None
        for stream in self._streams.values():
            stream.stop()
        self._streams.clear()
        if self.executor:
            self.executor.shutdown(wait=False)
            self.executor = None
        self.publisher.stop()
        print("[Orchestrator] Pipeline stopped")

    def is_running(self) -> bool:
        return self._running

    def stream_diagnostics(self) -> list[dict]:
        """
        Returns buffer health per camera.
        Exposes frames_dropped so the API can warn when the scheduler
        round trip is exceeding MAX_GAP_SECONDS.
        """
        return [
            stream.diagnostics()
            for stream in self._streams.values()
        ]

    def add_camera(self, cam: CameraConfig):
        self._open_stream(cam)
        self.scheduler.add_camera(cam.camera_id, cam.base_priority)
        print(f"[Orchestrator] Camera {cam.camera_id} added")

    def remove_camera(self, camera_id: str):
        stream = self._streams.pop(camera_id, None)
        if stream:
            stream.stop()
        self.scheduler.remove_camera(camera_id)
        self.stream_buffer.remove(camera_id)
        with self._frames_lock:
            self._processed_frames.pop(camera_id, None)
        print(f"[Orchestrator] Camera {camera_id} removed")

    def _open_stream(self, cam: CameraConfig):
        if cam.camera_id in self._streams:
            return
        stream = VideoStream(
            camera_id=cam.camera_id,
            source=cam.stream_url,
            source_fps=cam.source_fps,
            target_fps=self.sampler.target_fps,
            time_offset=cam.timestamp_offset_seconds,
        ).start()
        self._streams[cam.camera_id] = stream
        print(f"[Orchestrator] Stream opened: {cam.camera_id}")

    def _loop(self):
        while self._running:
            camera_id = self.scheduler.next()

            if camera_id is None:
                time.sleep(0.01)
                continue

            stream = self._streams.get(camera_id)
            if stream is None:
                # Camera was removed mid-loop
                self.scheduler.release(camera_id)
                continue

            if not self.sampler.is_due(camera_id):
                # Not yet time to sample this camera — keep UI fresh with
                # last processed frame without consuming a frame from the queue.
                with self._frames_lock:
                    raw = self._processed_frames.get(camera_id)
                if raw is not None:
                    self.stream_buffer.write(camera_id, raw)
                self.scheduler.release(camera_id)
                time.sleep(0.005)
                continue

            packet = stream.read()

            if packet is None:
                # No frame available — stream stalled or queue empty.
                # Release without penalty so this camera keeps its wait
                # time and other cameras get scheduled during the gap.
                self.scheduler.release(camera_id)
                time.sleep(0.005)
                continue

            if self.executor is None:
                self.scheduler.release(camera_id)
                break

            # Commit the sample timestamp now that we have a frame to process.
            self.sampler.mark_sampled(camera_id)

            # Frame confirmed — submit for processing and confirm slot
            future = self.executor.submit(self.worker.process, packet)
            self.scheduler.confirm(camera_id)

            def callback(fut, cam_id=camera_id):
                try:
                    result_frame = fut.result()
                    with self._frames_lock:
                        self._processed_frames[cam_id] = result_frame
                except Exception as e:
                    print(f"[Orchestrator] Worker error on {cam_id}: {e}")

            future.add_done_callback(callback)
            time.sleep(0.001)