"""
app/services/camera_registry.py

Source_fps is used to CameraConfig. This is used by VideoStream to calculate
the correct keyframe sampling interval and buffer size. Defaults to 30fps
if not provided (safe assumption for real CCTV cameras).

Expected_round_trip_seconds() to CameraScheduler interface
so the registry can warn when buffer size may be insufficient for the
current camera load.
"""

import json
import threading
from dataclasses import dataclass, asdict, field
from typing import Optional
from pathlib import Path


# Default FPS assumption for cameras where source_fps is not specified.
# 30fps covers most real CCTV cameras. Override per camera for accuracy.
DEFAULT_SOURCE_FPS = 30


@dataclass
class CameraConfig:
    camera_id: str
    stream_url: str
    base_priority: int
    location: dict = field(default_factory=dict)
    # Source FPS of this camera's feed.
    # Used to calculate keyframe sampling interval and buffer size.
    # Set to actual camera FPS for accurate buffer sizing.
    # Defaults to 30fps if unknown.
    source_fps: float = DEFAULT_SOURCE_FPS
    # Seconds to add to system time when producing frame timestamps.
    # Negative values shift sightings into the past — useful for test
    # sources (files) that have no wall-clock metadata, so that a vehicle
    # travelling across the camera network produces a credible route.
    # Set to 0 for real RTSP cameras (default).
    timestamp_offset_seconds: float = 0.0

    def to_dict(self):
        return asdict(self)


class CameraRegistry:
    def __init__(self, config_path: str = "cameras.json"):
        self.config_path = Path(config_path)
        self._cameras: dict[str, CameraConfig] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if not self.config_path.exists():
            return
        with open(self.config_path, "r") as f:
            entries = json.load(f)
        for entry in entries:
            cam = CameraConfig(
                camera_id=entry["camera_id"],
                stream_url=entry["stream_url"],
                base_priority=entry.get("base_priority", 5),
                location=entry.get("location", {}),
                source_fps=entry.get("source_fps", DEFAULT_SOURCE_FPS),
                timestamp_offset_seconds=entry.get("timestamp_offset_seconds", 0.0),
            )
            self._cameras[cam.camera_id] = cam
        print(f"[CameraRegistry] Loaded {len(self._cameras)} cameras")

    def _persist(self):
        data = [cam.to_dict() for cam in self._cameras.values()]
        with open(self.config_path, "w") as f:
            json.dump(data, f, indent=2)

    def register(self, camera_id: str, stream_url: str,
                 base_priority: int = 5,
                 location: Optional[dict] = None,
                 source_fps: float = DEFAULT_SOURCE_FPS) -> CameraConfig:
        with self._lock:
            if camera_id in self._cameras:
                raise ValueError(f"Camera {camera_id} already registered.")
            cam = CameraConfig(
                camera_id=camera_id,
                stream_url=stream_url,
                base_priority=base_priority,
                location=location or {},
                source_fps=source_fps
            )
            self._cameras[camera_id] = cam
            self._persist()
            print(f"[CameraRegistry] Registered {camera_id} "
                  f"(priority={base_priority}, source_fps={source_fps})")
            return cam

    def deregister(self, camera_id: str):
        with self._lock:
            if camera_id not in self._cameras:
                raise ValueError(f"Camera {camera_id} not found.")
            del self._cameras[camera_id]
            self._persist()
            print(f"[CameraRegistry] Deregistered {camera_id}")

    def get(self, camera_id: str) -> Optional[CameraConfig]:
        with self._lock:
            return self._cameras.get(camera_id)

    def all(self) -> list[CameraConfig]:
        with self._lock:
            return list(self._cameras.values())

    def all_ids(self) -> list[str]:
        with self._lock:
            return list(self._cameras.keys())

    def update_priority(self, camera_id: str, new_base_priority: int):
        with self._lock:
            if camera_id not in self._cameras:
                raise ValueError(f"Camera {camera_id} not found.")
            self._cameras[camera_id].base_priority = new_base_priority
            self._persist()