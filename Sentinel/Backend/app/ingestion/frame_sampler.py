# frame_sampler.py

import time


class FrameSampler:
    def __init__(self, target_fps=None, interval_seconds=None):
        self.target_fps = target_fps
        self.interval_seconds = interval_seconds

        self.last_sample_time = {}

        if target_fps:
            self.min_interval = 1.0 / target_fps
        else:
            self.min_interval = None

    def _interval(self):
        if self.target_fps:
            return self.min_interval
        if self.interval_seconds:
            return self.interval_seconds
        return None

    def is_due(self, camera_id) -> bool:
        """Non-consuming check — does not update last_sample_time."""
        interval = self._interval()
        if interval is None:
            return True
        return time.time() - self.last_sample_time.get(camera_id, 0) >= interval

    def mark_sampled(self, camera_id):
        """Record that a frame was sampled now. Call only after a frame is committed for processing."""
        self.last_sample_time[camera_id] = time.time()

    def should_sample(self, camera_id):
        if self.is_due(camera_id):
            self.mark_sampled(camera_id)
            return True
        return False