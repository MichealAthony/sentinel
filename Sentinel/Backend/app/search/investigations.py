"""
app/search/investigations.py

In-memory investigation store with optional JSON persistence.

An investigation is a named search session. The operator creates one
by submitting a search query. The investigation stores the original
parameters, the current result, and metadata for polling and notification.

DESIGN
------
Investigations are stored in memory keyed by investigation_id.
They are also persisted to a JSON file so they survive process restarts
and page refreshes. Embeddings are NOT persisted (too large) — they
are re-fetched on restore if needed.

The poll mechanism is lightweight: it compares the vehicle's
last_known_timestamp in the DB against what was last stored in the
investigation. If the timestamp has advanced, new sightings exist.
"""

import json
import time
import uuid
import threading
from pathlib import Path
from typing import Optional

INVESTIGATIONS_FILE = Path(__file__).parent / "investigations.json"

# Maximum investigations to retain (oldest closed first)
MAX_INVESTIGATIONS = 50


class Investigation:
    def __init__(
        self,
        investigation_id: str,
        label: str,
        params: dict,
        result: Optional[dict] = None,
        status: str = "active",
    ):
        self.investigation_id = investigation_id
        self.label = label
        self.params = params
        self.result = result
        self.status = status  # "pending" | "active" | "closed"
        self.created_at = time.time()
        self.updated_at = time.time()
        self.last_checked_at = time.time()
        self.has_update = False
        self.closed = False
        self.notes: str = ""
        self.pinned: bool = False
        # Live match accumulator — populated by ActiveQueryRegistry.on_match callbacks
        self.live_matches: list[dict] = []
        self._live_lock = threading.Lock()

    def to_dict(self) -> dict:
        return {
            "investigation_id": self.investigation_id,
            "label": self.label,
            "params": self.params,
            "result": self._result_without_embeddings(),
            "status": self.status,
            "notes": self.notes,
            "pinned": self.pinned,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_checked_at": self.last_checked_at,
            "has_update": self.has_update,
            "closed": self.closed,
        }

    def _result_without_embeddings(self) -> Optional[dict]:
        """Strip embeddings before persisting — too large for JSON."""
        if self.result is None:
            return None
        r = dict(self.result)
        r.pop("embeddings", None)
        return r

    def summary(self) -> dict:
        """Lightweight summary for the investigations list endpoint."""
        result = self.result or {}
        sightings = result.get("sightings", [])
        confirmed = [s for s in sightings if not s.get("inferred")]
        return {
            "investigation_id": self.investigation_id,
            "label": self.label,
            "status": self.status,
            "plate": result.get("plate"),
            "vehicle_type": result.get("vehicle_type"),
            "colour_label": result.get("colour_label"),
            "sighting_count": len(confirmed),
            "last_known_camera": result.get("last_known_camera"),
            "last_known_timestamp": result.get("last_known_timestamp"),
            "predicted_next": (result.get("predicted_next") or [])[:1],
            "staleness_warning": result.get("staleness_warning", False),
            "has_update": self.has_update,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class InvestigationStore:
    def __init__(self):
        self._store: dict[str, Investigation] = {}
        self._lock = threading.Lock()
        self._load()

    # =========================================================
    # PERSISTENCE
    # =========================================================
    def _load(self):
        if not INVESTIGATIONS_FILE.exists():
            return
        try:
            with open(INVESTIGATIONS_FILE) as f:
                data = json.load(f)
            for d in data:
                inv = Investigation(
                    investigation_id=d["investigation_id"],
                    label=d["label"],
                    params=d["params"],
                    result=d.get("result"),
                    status=d.get("status", "active"),
                )
                inv.created_at = d.get("created_at", time.time())
                inv.updated_at = d.get("updated_at", time.time())
                inv.last_checked_at = d.get("last_checked_at", time.time())
                inv.has_update = d.get("has_update", False)
                inv.closed = d.get("closed", False)
                inv.notes = d.get("notes", "")
                inv.pinned = d.get("pinned", False)
                if not inv.closed:
                    self._store[inv.investigation_id] = inv
            print(f"[InvestigationStore] Restored {len(self._store)} investigations")
        except Exception as e:
            print(f"[InvestigationStore] Failed to load: {e}")

    def _persist(self):
        try:
            data = [inv.to_dict() for inv in self._store.values()]
            with open(INVESTIGATIONS_FILE, "w") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            print(f"[InvestigationStore] Failed to persist: {e}")

    # =========================================================
    # CRUD
    # =========================================================
    def create(self, label: str, params: dict, result: Optional[dict] = None,
               status: str = "active") -> Investigation:
        with self._lock:
            inv_id = str(uuid.uuid4())
            inv = Investigation(inv_id, label, params, result, status=status)
            self._store[inv_id] = inv
            self._trim()
            self._persist()
            return inv

    def activate(self, investigation_id: str, result: dict) -> bool:
        """Transition a pending investigation to active once a vehicle is found."""
        with self._lock:
            inv = self._store.get(investigation_id)
            if not inv:
                return False
            inv.result = result
            inv.status = "active"
            inv.has_update = True
            inv.updated_at = time.time()
            self._persist()
            return True

    def get(self, investigation_id: str) -> Optional[Investigation]:
        with self._lock:
            return self._store.get(investigation_id)

    def update_result(self, investigation_id: str, result: dict) -> bool:
        with self._lock:
            inv = self._store.get(investigation_id)
            if not inv:
                return False
            old_ts = (inv.result or {}).get("last_known_timestamp")
            new_ts = result.get("last_known_timestamp")
            inv.result = result
            inv.updated_at = time.time()
            # Mark as having an update if the trajectory advanced
            if new_ts and old_ts and new_ts > old_ts:
                inv.has_update = True
            self._persist()
            return True

    def mark_checked(self, investigation_id: str):
        with self._lock:
            inv = self._store.get(investigation_id)
            if inv:
                inv.last_checked_at = time.time()
                inv.has_update = False
                self._persist()

    def update_meta(self, investigation_id: str, label: Optional[str] = None,
                    notes: Optional[str] = None, pinned: Optional[bool] = None) -> bool:
        with self._lock:
            inv = self._store.get(investigation_id)
            if not inv:
                return False
            if label is not None:
                inv.label = label
            if notes is not None:
                inv.notes = notes
            if pinned is not None:
                inv.pinned = pinned
            inv.updated_at = time.time()
            self._persist()
            return True

    def close(self, investigation_id: str) -> bool:
        with self._lock:
            inv = self._store.pop(investigation_id, None)
            if inv:
                self._persist()
                return True
            return False

    def list_all(self) -> list[Investigation]:
        with self._lock:
            return sorted(
                self._store.values(),
                key=lambda i: i.created_at,
                reverse=True
            )

    def list_pending(self) -> list[Investigation]:
        with self._lock:
            return [inv for inv in self._store.values()
                    if inv.status == "pending" and not inv.closed]

    def _trim(self):
        """Keep only the most recent MAX_INVESTIGATIONS."""
        if len(self._store) > MAX_INVESTIGATIONS:
            oldest = sorted(self._store.values(), key=lambda i: i.created_at)
            for inv in oldest[:len(self._store) - MAX_INVESTIGATIONS]:
                del self._store[inv.investigation_id]

    def append_live_match(self, investigation_id: str, match: dict) -> None:
        """
        Called by the on_match callback from ActiveQueryRegistry.
        Thread-safe — VehicleWorker threads may call this concurrently.
        """
        with self._lock:
            inv = self._store.get(investigation_id)
            if not inv:
                return
            with inv._live_lock:
                inv.live_matches.append(match)
                inv.has_update = True
            self._persist()

    # =========================================================
    # POLL
    # =========================================================
    def poll(self, investigation_id: str, since: float) -> dict:
        """
        Lightweight poll — check if new sightings exist since `since` timestamp.
        Does NOT re-run the full search. Just compares stored timestamps.
        Returns enough info for the UI notification badge.
        """
        with self._lock:
            inv = self._store.get(investigation_id)
            if not inv or not inv.result:
                return {"has_update": False, "new_sighting_count": 0, "new_live_match_count": 0, "last_known_timestamp": None}

            result = inv.result
            last_ts = result.get("last_known_timestamp")

            # Count sightings newer than since
            new_sightings = [
                s for s in result.get("sightings", [])
                if not s.get("inferred") and s.get("timestamp") and s["timestamp"] > since
            ]

            with inv._live_lock:
                new_live = [m for m in inv.live_matches if m.get("timestamp", 0) > since]

            has_update = inv.has_update or bool(new_sightings) or bool(new_live) or (last_ts and last_ts > since)

            return {
                "has_update": has_update,
                "new_sighting_count": len(new_sightings),
                "new_live_match_count": len(new_live),
                "last_known_timestamp": last_ts,
            }


# Module-level singleton
investigation_store = InvestigationStore()
