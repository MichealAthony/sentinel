"""
app/services/camera_scheduler.py

DESIGN DECISIONS EXPLAINED
--------------------------------

1. Selection separated from re-enqueue.
   next() pops and returns a camera but does NOT re-enqueue it.
   The orchestrator calls one of two methods:
     confirm(camera_id) — frame was read and submitted, re-enqueue normally
     release(camera_id) — no frame or skipped, re-enqueue without penalty

2. Time-slice budget per camera.
   Each camera gets frames_per_slot consecutive confirms before being
   forced to the back of the queue. Priority controls slot size — a
   chokepoint camera gets more frames per turn, not permanent access.
   This guarantees every camera gets a turn on live feeds.

3. Generation counter prevents stale heap entries.
   Every _push() increments generation. On pop, mismatched generation
   means the entry is stale and is skipped.

4. Clean reset on pipeline restart.
   reset() rebuilds all entry state from scratch so stale timestamps
   and heap entries from a previous run don't affect the new run.
"""

import time
import threading
import heapq
from dataclasses import dataclass, field
from typing import Optional

from app.services.camera_registry import CameraRegistry

# --- Starvation ---
STARVATION_THRESHOLD_SECONDS = 10
STARVATION_INCREMENT = 2

# --- Time-slice: base frames per scheduler slot ---
# A camera with base_priority=10 gets 10 frames per slot.
# A camera with base_priority=5 gets 5 frames per slot.
# Boosted cameras get proportionally more frames via effective priority.
BASE_FRAMES_PER_PRIORITY_POINT = 1

# --- Boost fatigue ---
FATIGUE_FLOOR = 0.1
FATIGUE_DECAY = 0.5
RECOVERY_RATE = 0.002


@dataclass
class CameraEntry:
    camera_id: str
    base_priority: int
    boost: int = 0
    boost_expiry: Optional[float] = None
    starvation_bump: int = 0

    # Wait tracking — only updated on confirm(), not on release()
    last_processed: float = field(default_factory=time.time)
    enqueued_at: float = field(default_factory=time.time)

    # Generation counter — prevents stale heap entries winning pops
    generation: int = 0

    # Time-slice — how many consecutive confirms before forced re-enqueue
    frames_this_slot: int = 0

    # Boost fatigue
    fatigue_coefficient: float = 1.0
    last_boost_time: Optional[float] = None
    last_recovery_tick: float = field(default_factory=time.time)

    @property
    def effective_priority(self) -> float:
        now = time.time()
        if self.boost_expiry and now < self.boost_expiry:
            active_boost = self.boost * self.fatigue_coefficient
        else:
            active_boost = 0
        return self.base_priority + active_boost + self.starvation_bump

    @property
    def frames_per_slot(self) -> int:
        """
        How many frames this camera gets per scheduler slot.
        Based on effective priority so boosted cameras get larger slots.
        Minimum of 1 so every camera always gets at least one frame.
        """
        return max(1, int(self.effective_priority) * BASE_FRAMES_PER_PRIORITY_POINT)

    def heap_key(self):
        return (-self.effective_priority, self.enqueued_at)

    def apply_fatigue(self):
        self.fatigue_coefficient = max(FATIGUE_FLOOR, self.fatigue_coefficient * FATIGUE_DECAY)
        self.last_boost_time = time.time()

    def recover_fatigue(self):
        now = time.time()
        if self.boost_expiry and now < self.boost_expiry:
            self.last_recovery_tick = now
            return
        if self.fatigue_coefficient >= 1.0:
            self.last_recovery_tick = now
            return
        elapsed = now - self.last_recovery_tick
        self.last_recovery_tick = now
        depletion = 1.0 - self.fatigue_coefficient
        self.fatigue_coefficient = min(1.0, self.fatigue_coefficient + depletion * RECOVERY_RATE * elapsed)

    def reset(self):
        """Reset runtime state for a clean pipeline restart."""
        self.boost = 0
        self.boost_expiry = None
        self.starvation_bump = 0
        self.last_processed = time.time()
        self.enqueued_at = time.time()
        self.generation = 0
        self.frames_this_slot = 0
        self.fatigue_coefficient = 1.0
        self.last_boost_time = None
        self.last_recovery_tick = time.time()


class CameraScheduler:
    def __init__(self, registry: CameraRegistry):
        self.registry = registry
        self._entries: dict[str, CameraEntry] = {}
        self._heap: list = []
        self._lock = threading.Lock()
        # Tracks which camera is currently selected (popped but not re-enqueued)
        self._active: Optional[str] = None
        self._build_from_registry()

    # =========================================================
    # INITIALISE
    # =========================================================
    def _build_from_registry(self):
        for cam in self.registry.all():
            entry = CameraEntry(camera_id=cam.camera_id, base_priority=cam.base_priority)
            self._entries[cam.camera_id] = entry
            self._push(entry)
        print(f"[CameraScheduler] Initialised with {len(self._entries)} cameras")

    # =========================================================
    # RESET (called on pipeline restart)
    # =========================================================
    def reset(self):
        """
        Rebuild heap from scratch and reset all entry state.
        Called by the orchestrator on every pipeline start so stale state
        from a previous run doesn't affect scheduling order.
        """
        with self._lock:
            self._heap.clear()
            self._active = None
            for entry in self._entries.values():
                entry.reset()
                self._push(entry)
        print("[CameraScheduler] Reset complete")

    # =========================================================
    # PRIVATE: PUSH WITH GENERATION
    # =========================================================
    def _push(self, entry: CameraEntry):
        entry.generation += 1
        heapq.heappush(self._heap, (entry.heap_key(), entry.camera_id, entry.generation))

    # =========================================================
    # ADD / REMOVE
    # =========================================================
    def add_camera(self, camera_id: str, base_priority: int):
        with self._lock:
            if camera_id in self._entries:
                return
            entry = CameraEntry(camera_id=camera_id, base_priority=base_priority)
            self._entries[camera_id] = entry
            self._push(entry)

    def remove_camera(self, camera_id: str):
        with self._lock:
            if camera_id in self._entries:
                del self._entries[camera_id]
            if self._active == camera_id:
                self._active = None

    # =========================================================
    # BOOST
    # =========================================================
    def boost(self, camera_id: str, boost_amount: int, ttl_seconds: float):
        with self._lock:
            if camera_id not in self._entries:
                print(f"[CameraScheduler] Boost ignored — {camera_id} not registered")
                return
            entry = self._entries[camera_id]
            entry.apply_fatigue()
            entry.boost = boost_amount
            entry.boost_expiry = time.time() + ttl_seconds
            # Only push if not currently active — if active, confirm/release will push
            if self._active != camera_id:
                self._push(entry)
            print(
                f"[CameraScheduler] Boosted {camera_id} "
                f"effective={boost_amount * entry.fatigue_coefficient:.1f} "
                f"coefficient={entry.fatigue_coefficient:.3f} "
                f"ttl={ttl_seconds}s"
            )

    def boost_many(self, camera_ids: list[str], boost_amount: int, ttl_seconds: float):
        for camera_id in camera_ids:
            self.boost(camera_id, boost_amount, ttl_seconds)

    # =========================================================
    # NEXT — select only, do NOT re-enqueue
    # =========================================================
    def next(self) -> Optional[str]:
        """
        Pop and return the next camera to process.
        Does NOT re-enqueue. The orchestrator must call either
        confirm() or release() after attempting to read a frame.
        """
        with self._lock:
            # Safety: if previous camera was never confirmed/released, release it now
            if self._active is not None:
                self._release_active(penalise=False)

            self._apply_starvation_bumps()
            self._apply_fatigue_recovery()

            while self._heap:
                key, camera_id, generation = heapq.heappop(self._heap)

                if camera_id not in self._entries:
                    continue  # lazy deletion

                entry = self._entries[camera_id]

                if generation != entry.generation:
                    continue  # stale entry

                self._active = camera_id
                return camera_id

        return None

    # =========================================================
    # CONFIRM — frame was read and submitted
    # =========================================================
    def confirm(self, camera_id: str):
        """
        Called by orchestrator after a frame was successfully read and submitted.
        Updates wait tracking and increments frame slot counter.
        If slot is exhausted, re-enqueues at back to give other cameras a turn.
        If slot has budget remaining, re-enqueues at front so camera continues.
        """
        with self._lock:
            if camera_id not in self._entries:
                self._active = None
                return

            entry = self._entries[camera_id]
            entry.last_processed = time.time()
            entry.frames_this_slot += 1

            if entry.frames_this_slot >= entry.frames_per_slot:
                # Slot exhausted — force to back, give other cameras a turn
                entry.frames_this_slot = 0
                entry.enqueued_at = time.time()
                entry.starvation_bump = 0
            # else: slot still has budget — enqueued_at stays old so this
            # camera wins tie-breaks and continues its slot

            self._push(entry)
            self._active = None

    # =========================================================
    # RELEASE — no frame or frame skipped, no penalty
    # =========================================================
    def release(self, camera_id: str):
        """
        Called by orchestrator when no frame was available or frame was skipped.
        Re-enqueues without updating last_processed so wait time is preserved.
        Does not increment frame slot counter.
        """
        with self._lock:
            self._release_active(penalise=False)

    def _release_active(self, penalise: bool = False):
        """Internal release. Always re-enqueues the active camera."""
        if self._active is None:
            return
        camera_id = self._active
        self._active = None
        if camera_id not in self._entries:
            return
        entry = self._entries[camera_id]
        entry.enqueued_at = time.time()  # always refresh so released camera goes to back of equal-priority queue
        self._push(entry)

    # =========================================================
    # PRIVATE: STARVATION + FATIGUE
    # =========================================================
    def _apply_starvation_bumps(self):
        now = time.time()
        for entry in self._entries.values():
            if entry.camera_id == self._active:
                continue  # active camera is not in the heap, skip
            wait_time = now - entry.last_processed
            if wait_time > STARVATION_THRESHOLD_SECONDS:
                increments = int(wait_time // STARVATION_THRESHOLD_SECONDS)
                new_bump = increments * STARVATION_INCREMENT
                if new_bump > entry.starvation_bump:
                    entry.starvation_bump = new_bump
                    self._push(entry)  # re-push with updated key; old entry invalidated by generation

    def _apply_fatigue_recovery(self):
        for entry in self._entries.values():
            entry.recover_fatigue()

    # =========================================================
    # STATUS
    # =========================================================
    def status(self) -> list[dict]:
        now = time.time()
        with self._lock:
            result = []
            for entry in self._entries.values():
                boost_active = bool(entry.boost_expiry and now < entry.boost_expiry)
                boost_remaining = max(0, entry.boost_expiry - now) if entry.boost_expiry else 0
                result.append({
                    "camera_id": entry.camera_id,
                    "base_priority": entry.base_priority,
                    "boost": entry.boost if boost_active else 0,
                    "effective_boost": round(entry.boost * entry.fatigue_coefficient, 2) if boost_active else 0,
                    "boost_remaining_seconds": round(boost_remaining, 1),
                    "fatigue_coefficient": round(entry.fatigue_coefficient, 3),
                    "starvation_bump": entry.starvation_bump,
                    "effective_priority": round(entry.effective_priority, 2),
                    "frames_this_slot": entry.frames_this_slot,
                    "frames_per_slot": entry.frames_per_slot,
                    "wait_seconds": round(now - entry.last_processed, 1),
                    "active": self._active == entry.camera_id
                })
            return sorted(result, key=lambda x: -x["effective_priority"])