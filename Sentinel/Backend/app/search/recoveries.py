"""
app/search/recoveries.py

Persistent log of vehicle recoveries — independent of the investigation lifecycle.
A recovery record is created when an operator marks a vehicle as physically located.
Records survive investigation closure and server restarts.
"""

import json
import time
import uuid
import threading
from pathlib import Path
from typing import Optional

RECOVERIES_FILE = Path(__file__).parent / "recoveries.json"


class Recovery:
    def __init__(
        self,
        recovery_id: str,
        investigation_id: Optional[str],
        label: str,
        lat: float,
        lng: float,
        notes: str = "",
        plate: Optional[str] = None,
        vehicle_type: Optional[str] = None,
        colour_label: Optional[str] = None,
        reported_at: Optional[float] = None,
    ):
        self.recovery_id      = recovery_id
        self.investigation_id = investigation_id
        self.label            = label
        self.lat              = lat
        self.lng              = lng
        self.notes            = notes
        self.plate            = plate
        self.vehicle_type     = vehicle_type
        self.colour_label     = colour_label
        self.reported_at      = reported_at  # unix ts when investigation was created
        self.created_at       = time.time()  # unix ts when recovery was marked

    def to_dict(self) -> dict:
        return {
            "recovery_id":      self.recovery_id,
            "investigation_id": self.investigation_id,
            "label":            self.label,
            "lat":              self.lat,
            "lng":              self.lng,
            "notes":            self.notes,
            "plate":            self.plate,
            "vehicle_type":     self.vehicle_type,
            "colour_label":     self.colour_label,
            "reported_at":      self.reported_at,
            "created_at":       self.created_at,
        }


class RecoveryStore:
    def __init__(self):
        self._store: dict[str, Recovery] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if not RECOVERIES_FILE.exists():
            return
        try:
            with open(RECOVERIES_FILE) as f:
                data = json.load(f)
            for d in data:
                r = Recovery(
                    recovery_id=d["recovery_id"],
                    investigation_id=d.get("investigation_id"),
                    label=d.get("label", ""),
                    lat=d["lat"],
                    lng=d["lng"],
                    notes=d.get("notes", ""),
                    plate=d.get("plate"),
                    vehicle_type=d.get("vehicle_type"),
                    colour_label=d.get("colour_label"),
                    reported_at=d.get("reported_at"),
                )
                r.created_at = d.get("created_at", time.time())
                self._store[r.recovery_id] = r
            print(f"[RecoveryStore] Restored {len(self._store)} recoveries")
        except Exception as e:
            print(f"[RecoveryStore] Failed to load: {e}")

    def _persist(self):
        try:
            data = [r.to_dict() for r in self._store.values()]
            with open(RECOVERIES_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[RecoveryStore] Failed to persist: {e}")

    def create(
        self,
        investigation_id: Optional[str],
        label: str,
        lat: float,
        lng: float,
        notes: str = "",
        plate: Optional[str] = None,
        vehicle_type: Optional[str] = None,
        colour_label: Optional[str] = None,
        reported_at: Optional[float] = None,
    ) -> Recovery:
        with self._lock:
            rid = str(uuid.uuid4())
            r = Recovery(rid, investigation_id, label, lat, lng, notes,
                         plate, vehicle_type, colour_label, reported_at)
            self._store[rid] = r
            self._persist()
            return r

    def list_all(self) -> list[Recovery]:
        with self._lock:
            return sorted(self._store.values(), key=lambda r: r.created_at, reverse=True)

    def delete(self, recovery_id: str) -> bool:
        with self._lock:
            if recovery_id not in self._store:
                return False
            del self._store[recovery_id]
            self._persist()
            return True


recovery_store = RecoveryStore()
