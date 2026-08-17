"""
tests/test_scheduler.py

Unit tests for CameraScheduler and CameraEntry.

Tests cover:
- Priority ordering (base priority determines scheduling order)
- Time-slicing (slot budget before forced re-queue)
- confirm() vs release() semantics
- Boost elevation and fatigue decay
- Generation counter preventing stale heap entries
- Camera add / remove
- reset() clears all runtime state
- Thread safety under concurrent boost + schedule
"""
import time
import threading
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from app.services.camera_scheduler import (
    CameraScheduler,
    CameraEntry,
    FATIGUE_FLOOR,
    FATIGUE_DECAY,
    STARVATION_THRESHOLD_SECONDS,
    STARVATION_INCREMENT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class _FakeCam:
    camera_id: str
    base_priority: int


def _registry(*cameras):
    """Build a MagicMock registry returning the given (id, priority) pairs."""
    reg = MagicMock()
    reg.all.return_value = [_FakeCam(cid, pri) for cid, pri in cameras]
    return reg


def _scheduler(*cameras):
    """Create a CameraScheduler pre-loaded with (camera_id, base_priority) pairs."""
    return CameraScheduler(_registry(*cameras))


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------

class TestPriorityOrdering:
    def test_higher_base_priority_scheduled_first(self):
        sched = _scheduler(("low", 1), ("high", 10))
        assert sched.next() == "high"

    def test_three_cameras_correct_order(self):
        sched = _scheduler(("a", 3), ("b", 7), ("c", 5))
        assert sched.next() == "b"

    def test_equal_priority_both_eventually_scheduled(self):
        sched = _scheduler(("x", 5), ("y", 5))
        seen = set()
        for _ in range(6):
            cam = sched.next()
            seen.add(cam)
            sched.release(cam)
        assert seen == {"x", "y"}


# ---------------------------------------------------------------------------
# Time-slicing
# ---------------------------------------------------------------------------

class TestTimeSlicing:
    def test_confirm_increments_slot_counter(self):
        sched = _scheduler(("cam", 5))
        cam = sched.next()
        sched.confirm(cam)
        assert sched._entries["cam"].frames_this_slot == 1

    def test_slot_resets_after_exhaustion(self):
        # priority=1 → frames_per_slot=1 → first confirm exhausts the slot
        sched = _scheduler(("cam", 1))
        cam = sched.next()
        sched.confirm(cam)
        assert sched._entries["cam"].frames_this_slot == 0

    def test_slot_exhausted_yields_to_other_camera(self):
        # Both cameras priority=1 → slot size 1 → must alternate
        sched = _scheduler(("a", 1), ("b", 1))
        seen = []
        for _ in range(6):
            cam = sched.next()
            seen.append(cam)
            sched.confirm(cam)
        # Both cameras must appear — they cannot monopolise the scheduler
        assert "a" in seen and "b" in seen

    def test_release_does_not_increment_slot_counter(self):
        sched = _scheduler(("cam", 5))
        cam = sched.next()
        sched.release(cam)
        assert sched._entries["cam"].frames_this_slot == 0

    def test_release_re_enqueues_camera(self):
        sched = _scheduler(("cam", 5))
        cam = sched.next()
        sched.release(cam)
        assert sched.next() == "cam"


# ---------------------------------------------------------------------------
# Boost
# ---------------------------------------------------------------------------

class TestBoost:
    def test_boost_elevates_camera_above_higher_base(self):
        sched = _scheduler(("slow", 1), ("boosted", 1))
        sched.boost("boosted", boost_amount=20, ttl_seconds=60)
        assert sched.next() == "boosted"

    def test_boost_ignored_for_unknown_camera(self):
        sched = _scheduler(("cam", 5))
        sched.boost("ghost", boost_amount=10, ttl_seconds=30)  # must not raise

    def test_boost_many_applies_to_all_listed(self):
        sched = _scheduler(("a", 1), ("b", 1), ("c", 1))
        sched.boost_many(["a", "b"], boost_amount=10, ttl_seconds=60)
        assert sched._entries["a"].boost == 10
        assert sched._entries["b"].boost == 10
        assert sched._entries["c"].boost == 0

    def test_expired_boost_falls_back_to_base(self):
        sched = _scheduler(("cam", 5))
        entry = sched._entries["cam"]
        entry.boost = 20
        entry.boost_expiry = time.time() - 1  # already expired
        assert entry.effective_priority == pytest.approx(5.0, abs=0.1)


# ---------------------------------------------------------------------------
# Boost fatigue
# ---------------------------------------------------------------------------

class TestBoostFatigue:
    def test_fatigue_decays_each_application(self):
        sched = _scheduler(("cam", 5))
        entry = sched._entries["cam"]
        entry.fatigue_coefficient = 1.0
        entry.apply_fatigue()
        assert entry.fatigue_coefficient == pytest.approx(FATIGUE_DECAY, abs=0.001)

    def test_fatigue_cannot_drop_below_floor(self):
        sched = _scheduler(("cam", 5))
        entry = sched._entries["cam"]
        entry.fatigue_coefficient = FATIGUE_FLOOR
        entry.apply_fatigue()
        assert entry.fatigue_coefficient == FATIGUE_FLOOR

    def test_fatigue_recovers_after_boost_expires(self):
        sched = _scheduler(("cam", 5))
        entry = sched._entries["cam"]
        entry.fatigue_coefficient = 0.2
        entry.boost_expiry = time.time() - 1   # expired
        entry.last_recovery_tick = time.time() - 100
        entry.recover_fatigue()
        assert entry.fatigue_coefficient > 0.2


# ---------------------------------------------------------------------------
# Generation counter
# ---------------------------------------------------------------------------

class TestGenerationCounter:
    def test_stale_heap_entries_are_skipped(self):
        """
        After boosting a camera, old heap entries for that camera must not win.
        The generation counter ensures only the freshest push is honoured.
        """
        sched = _scheduler(("a", 1), ("b", 10))
        cam = sched.next()   # pops "b" (highest)
        assert cam == "b"
        sched.boost("a", boost_amount=25, ttl_seconds=60)  # pushes new "a" entry
        sched.release("b")
        # "a" now has effective_priority=26 — must win despite stale old entry
        assert sched.next() == "a"


# ---------------------------------------------------------------------------
# Starvation
# ---------------------------------------------------------------------------

class TestStarvation:
    def test_long_wait_increases_starvation_bump(self):
        sched = _scheduler(("cam", 5))
        entry = sched._entries["cam"]
        # Simulate camera waiting 25 seconds (2 × threshold)
        entry.last_processed = time.time() - 25
        sched._apply_starvation_bumps()
        assert entry.starvation_bump > 0

    def test_starvation_bump_clears_on_slot_exhaustion(self):
        # priority=1, bump=1 → effective=2, frames_per_slot=2
        # Bump must survive confirm 1, then clear at confirm 2 (exhaustion)
        sched = _scheduler(("cam", 1))
        entry = sched._entries["cam"]
        entry.starvation_bump = 1
        cam = sched.next()
        sched.confirm(cam)
        assert entry.starvation_bump == 1   # mid-slot: still there
        cam = sched.next()
        sched.confirm(cam)
        assert entry.starvation_bump == 0   # slot exhausted: cleared

    def test_starvation_bump_preserved_within_slot(self):
        # priority=5, bump=8 → effective=13, frames_per_slot=13
        # After one confirm the slot is not exhausted — bump must survive
        sched = _scheduler(("cam", 5))
        sched._entries["cam"].starvation_bump = 8
        cam = sched.next()
        sched.confirm(cam)
        assert sched._entries["cam"].starvation_bump == 8


# ---------------------------------------------------------------------------
# Add / Remove
# ---------------------------------------------------------------------------

class TestAddRemove:
    def test_add_camera_appears_in_next(self):
        sched = _scheduler()   # empty
        sched.add_camera("new", base_priority=5)
        assert sched.next() == "new"

    def test_add_duplicate_does_not_change_existing_priority(self):
        sched = _scheduler(("cam", 5))
        sched.add_camera("cam", base_priority=99)
        assert sched._entries["cam"].base_priority == 5

    def test_removed_camera_never_returned(self):
        sched = _scheduler(("keep", 5), ("remove", 10))
        sched.remove_camera("remove")
        seen = set()
        for _ in range(8):
            cam = sched.next()
            if cam:
                seen.add(cam)
                sched.release(cam)
        assert "remove" not in seen


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

class TestReset:
    def test_reset_clears_boost(self):
        sched = _scheduler(("cam", 5))
        sched.boost("cam", boost_amount=20, ttl_seconds=60)
        sched.reset()
        assert sched._entries["cam"].boost == 0
        assert sched._entries["cam"].boost_expiry is None

    def test_reset_clears_starvation_bump(self):
        sched = _scheduler(("cam", 5))
        sched._entries["cam"].starvation_bump = 8
        sched.reset()
        assert sched._entries["cam"].starvation_bump == 0

    def test_reset_clears_frame_counter(self):
        sched = _scheduler(("cam", 5))
        sched._entries["cam"].frames_this_slot = 4
        sched.reset()
        assert sched._entries["cam"].frames_this_slot == 0

    def test_reset_restores_scheduling_for_all_cameras(self):
        sched = _scheduler(("a", 5), ("b", 5))
        _cam = sched.next()   # leave active without confirming
        sched.reset()
        seen = set()
        for _ in range(6):
            c = sched.next()
            if c:
                seen.add(c)
                sched.release(c)
        assert seen == {"a", "b"}


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_concurrent_boost_and_schedule_no_crash(self):
        sched = _scheduler(("a", 5), ("b", 5), ("c", 5))
        errors = []

        def boost_loop():
            for _ in range(100):
                try:
                    sched.boost("a", boost_amount=10, ttl_seconds=5)
                except Exception as e:
                    errors.append(e)

        def schedule_loop():
            for _ in range(100):
                try:
                    cam = sched.next()
                    if cam:
                        sched.release(cam)
                except Exception as e:
                    errors.append(e)

        threads = [
            threading.Thread(target=boost_loop),
            threading.Thread(target=schedule_loop),
            threading.Thread(target=schedule_loop),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
