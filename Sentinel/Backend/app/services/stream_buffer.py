"""
app/services/stream_buffer.py
No app.* imports needed — self contained.
"""

import threading
import cv2
from typing import Optional


class StreamBuffer:
    def __init__(self):
        self._frames: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def write(self, camera_id: str, frame):
        if frame is None:
            return
        _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        with self._lock:
            self._frames[camera_id] = jpeg.tobytes()

    def read(self, camera_id: str) -> Optional[bytes]:
        with self._lock:
            return self._frames.get(camera_id)

    def remove(self, camera_id: str):
        with self._lock:
            self._frames.pop(camera_id, None)

    def active_cameras(self) -> list[str]:
        with self._lock:
            return list(self._frames.keys())
