"""
app/services/index_publisher.py

Posts VehicleIndex records to the DB team's endpoint.
Non-blocking — pipeline never waits on DB availability.
"""

import threading
import queue
import time
import logging
from typing import Optional

import requests

from app.services.vehicle_index import VehicleIndex

logger = logging.getLogger(__name__)

MAX_QUEUE_SIZE = 1000
RETRY_INTERVAL = 5


class IndexPublisher:
    def __init__(self, db_endpoint: str = "http://localhost:8001/indexes"):
        self.db_endpoint = db_endpoint
        self._queue: queue.Queue = queue.Queue(maxsize=MAX_QUEUE_SIZE)
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None

    def start(self):
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._publish_loop, daemon=True, name="IndexPublisher"
        )
        self._worker_thread.start()
        logger.info(f"[IndexPublisher] Started → {self.db_endpoint}")

    def stop(self):
        self._running = False
        if self._worker_thread:
            self._worker_thread.join(timeout=10)

    def publish(self, index: VehicleIndex):
        try:
            self._queue.put_nowait(index.to_dict())
        except queue.Full:
            logger.warning(f"[IndexPublisher] Queue full — dropping {index.vehicle_id}")

    def _publish_loop(self):
        while self._running or not self._queue.empty():
            try:
                record = self._queue.get(timeout=1)
            except queue.Empty:
                continue

            if self._post(record):
                self._queue.task_done()
            else:
                try:
                    self._queue.put_nowait(record)
                except queue.Full:
                    pass
                time.sleep(RETRY_INTERVAL)

    def _post(self, record: dict) -> bool:
        try:
            response = requests.post(self.db_endpoint, json=record, timeout=5)
            return response.status_code in (200, 201)
        except requests.exceptions.RequestException as e:
            logger.warning(f"[IndexPublisher] DB unreachable: {e}")
            return False

    def status(self) -> dict:
        return {
            "queue_depth": self._queue.qsize(),
            "db_endpoint": self.db_endpoint,
            "running": self._running
        }
