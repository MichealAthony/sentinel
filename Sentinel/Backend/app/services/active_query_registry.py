"""
app/services/active_query_registry.py

VehicleWorker holds a reference to this registry. When no queries are
registered, check_crop is a no-op. The registry is inert until register()
is called by the search layer.
"""

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np


@dataclass
class ActiveQuery:
    query_id: str
    text_embedding: np.ndarray        # 512-dim L2-normalised
    threshold: float                  # default 0.50
    on_match: Callable                # called with (vehicle_id, camera_id, score, timestamp)
    registered_at: float              # time.time()
    expires_at: Optional[float]       # None = no expiry


class ActiveQueryRegistry:
    """
    Thread-safe registry of active visual search queries.
    Multiple VehicleWorker threads call check_crop concurrently; the search
    layer calls register/deregister from the FastAPI async thread.
    When no queries are registered, check_crop returns immediately with zero
    computation — this is the normal operating state.
    """

    def __init__(self):
        self._queries: dict[str, ActiveQuery] = {}
        self._lock = threading.Lock()

    def register(
        self,
        query_id: str,
        text_embedding: np.ndarray,
        on_match: Callable,
        threshold: float = 0.50,
        ttl_seconds: Optional[float] = None,
    ) -> None:
        expires_at = time.time() + ttl_seconds if ttl_seconds is not None else None
        query = ActiveQuery(
            query_id=query_id,
            text_embedding=text_embedding,
            threshold=threshold,
            on_match=on_match,
            registered_at=time.time(),
            expires_at=expires_at,
        )
        with self._lock:
            self._queries[query_id] = query

    def deregister(self, query_id: str) -> None:
        with self._lock:
            self._queries.pop(query_id, None)

    def active(self) -> list[ActiveQuery]:
        """Returns non-expired queries. Expired entries are removed lazily."""
        now = time.time()
        with self._lock:
            expired = [
                qid for qid, q in self._queries.items()
                if q.expires_at is not None and q.expires_at <= now
            ]
            for qid in expired:
                del self._queries[qid]
            return list(self._queries.values())

    def check_crop(
        self,
        clip_embedding: np.ndarray,
        vehicle_id: str,
        camera_id: str,
        timestamp: float,
    ) -> list[dict]:
        """
        Cosine similarity check against all active queries (inner product of
        L2-normalised vectors). Calls on_match for each query that meets its
        threshold. Returns immediately with [] when no queries are registered.
        """
        queries = self.active()
        if not queries:
            return []

        matches = []
        for query in queries:
            score = float(np.dot(clip_embedding, query.text_embedding))
            if score >= query.threshold:
                match = {
                    "query_id": query.query_id,
                    "vehicle_id": vehicle_id,
                    "camera_id": camera_id,
                    "score": score,
                    "timestamp": timestamp,
                }
                matches.append(match)
                try:
                    query.on_match(vehicle_id, camera_id, score, timestamp)
                except Exception as e:
                    print(f"[ActiveQueryRegistry] on_match error for {query.query_id}: {e}")

        return matches


# Module-level singleton — imported by VehicleWorker and visual_pipeline.
active_query_registry = ActiveQueryRegistry()
