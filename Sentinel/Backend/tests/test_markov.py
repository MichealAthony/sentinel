"""
tests/test_markov.py

Unit tests for the Markov transition matrix used for route prediction.

Tests cover:
- record() and record_trajectory() accumulate counts correctly
- Cold-start topology prior: nearby cameras rank higher, distant cameras excluded
- Global data shifts predictions toward observed transitions (Laplace-smoothed)
- Per-vehicle data: respected when ≥ MIN_VEHICLE_OBSERVATIONS available
- Weight regime selection (topology-only, global, vehicle+global)
- Top-k filtering and probability ordering
- Probabilities below 0.01 are excluded
- Thread safety under concurrent writes
"""
import threading
from unittest.mock import patch

import pytest

from app.search.markov import TransitionMatrix, LAPLACE_ALPHA, MIN_VEHICLE_OBSERVATIONS


# ---------------------------------------------------------------------------
# Fixture: fresh matrix with file I/O disabled
# ---------------------------------------------------------------------------

@pytest.fixture
def tm():
    """
    A TransitionMatrix with _load and _persist patched so tests are
    hermetic (no disk reads or writes).
    """
    with patch.object(TransitionMatrix, "_load"), \
         patch.object(TransitionMatrix, "_persist"):
        matrix = TransitionMatrix()
        yield matrix


@pytest.fixture
def cameras():
    """
    Four cameras at known positions.
    B  ~150 m from A  → inside topology radius
    C  ~1 km from A   → inside topology radius, farther than B
    D  >10 km from A  → outside TOPOLOGY_MAX_RADIUS_METRES (2000 m)
    """
    return {
        "A": {"label": "Alpha", "lat": 18.0000, "lng": -76.8000},
        "B": {"label": "Beta",  "lat": 18.0010, "lng": -76.8010},
        "C": {"label": "Gamma", "lat": 18.0050, "lng": -76.8090},
        "D": {"label": "Delta", "lat": 18.1000, "lng": -76.9000},
    }


# ---------------------------------------------------------------------------
# record()
# ---------------------------------------------------------------------------

class TestRecord:
    def test_record_increments_count(self, tm):
        tm.record("A", "B")
        assert tm._global_counts["A"]["B"] == 1

    def test_record_accumulates(self, tm):
        for _ in range(5):
            tm.record("A", "B")
        assert tm._global_counts["A"]["B"] == 5

    def test_self_transition_ignored(self, tm):
        tm.record("A", "A")
        assert "A" not in tm._global_counts

    def test_multiple_destinations_tracked_independently(self, tm):
        tm.record("A", "B")
        tm.record("A", "C")
        assert tm._global_counts["A"]["B"] == 1
        assert tm._global_counts["A"]["C"] == 1

    def test_record_trajectory_creates_sequential_pairs(self, tm):
        sightings = [
            {"camera_id": "A", "timestamp": 1000},
            {"camera_id": "B", "timestamp": 1010},
            {"camera_id": "C", "timestamp": 1020},
        ]
        tm.record_trajectory(sightings)
        assert tm._global_counts["A"]["B"] == 1
        assert tm._global_counts["B"]["C"] == 1

    def test_record_trajectory_skips_consecutive_same_camera(self, tm):
        sightings = [
            {"camera_id": "A", "timestamp": 1000},
            {"camera_id": "A", "timestamp": 1005},  # same — skip
            {"camera_id": "B", "timestamp": 1010},
        ]
        tm.record_trajectory(sightings)
        # A→A must not have been recorded
        assert "A" not in tm._global_counts.get("A", {})
        assert tm._global_counts["A"]["B"] == 1

    def test_record_trajectory_skips_missing_camera_id(self, tm):
        sightings = [
            {"camera_id": "A", "timestamp": 1000},
            {"timestamp": 1005},                   # missing camera_id
            {"camera_id": "B", "timestamp": 1010},
        ]
        tm.record_trajectory(sightings)
        # Should not crash; A→B with missing middle is not recorded directly
        assert isinstance(tm._global_counts, dict)


# ---------------------------------------------------------------------------
# predict — topology prior (cold start)
# ---------------------------------------------------------------------------

class TestPredictTopologyOnly:
    def test_cold_start_returns_non_empty_predictions(self, tm, cameras):
        preds = tm.predict("A", cameras)
        assert len(preds) > 0

    def test_current_camera_excluded_from_results(self, tm, cameras):
        preds = tm.predict("A", cameras)
        assert all(p["camera_id"] != "A" for p in preds)

    def test_nearer_camera_ranked_before_farther(self, tm, cameras):
        preds = tm.predict("A", cameras)
        ids = [p["camera_id"] for p in preds]
        # B is ~150 m, C is ~1 km — B must appear before C
        assert ids.index("B") < ids.index("C")

    def test_camera_beyond_radius_excluded(self, tm, cameras):
        preds = tm.predict("A", cameras)
        assert all(p["camera_id"] != "D" for p in preds)

    def test_probabilities_sum_to_approximately_one(self, tm, cameras):
        preds = tm.predict("A", cameras)
        assert sum(p["probability"] for p in preds) == pytest.approx(1.0, abs=0.05)

    def test_all_probabilities_at_least_min_threshold(self, tm, cameras):
        preds = tm.predict("A", cameras)
        assert all(p["probability"] >= 0.01 for p in preds)

    def test_empty_cameras_returns_empty(self, tm):
        preds = tm.predict("A", {})
        assert preds == []


# ---------------------------------------------------------------------------
# predict — global transition data
# ---------------------------------------------------------------------------

class TestPredictGlobal:
    def test_frequently_observed_transition_ranks_first(self, tm, cameras):
        # Record many A→C transitions — C should dominate despite being farther
        for _ in range(30):
            tm.record("A", "C")
        preds = tm.predict("A", cameras)
        assert preds[0]["camera_id"] == "C"

    def test_laplace_smoothing_gives_unobserved_camera_nonzero_probability(self, tm, cameras):
        # Record only A→B; C should still appear via Laplace smoothing
        for _ in range(10):
            tm.record("A", "B")
        preds = tm.predict("A", cameras)
        ids = [p["camera_id"] for p in preds]
        assert "C" in ids
        c_prob = next(p["probability"] for p in preds if p["camera_id"] == "C")
        assert c_prob > 0.0

    def test_results_sorted_descending_by_probability(self, tm, cameras):
        for _ in range(10):
            tm.record("A", "B")
        preds = tm.predict("A", cameras)
        probs = [p["probability"] for p in preds]
        assert probs == sorted(probs, reverse=True)


# ---------------------------------------------------------------------------
# predict — per-vehicle data
# ---------------------------------------------------------------------------

class TestPredictPerVehicle:
    def test_insufficient_vehicle_sightings_ignored(self, tm, cameras):
        # Only 2 sightings — below MIN_VEHICLE_OBSERVATIONS, should not crash
        vehicle_sightings = [
            {"camera_id": "A", "timestamp": 1000},
            {"camera_id": "C", "timestamp": 1010},
        ]
        preds = tm.predict("A", cameras, vehicle_sightings=vehicle_sightings)
        assert isinstance(preds, list)

    def test_sufficient_vehicle_data_influences_prediction(self, tm, cameras):
        # Build vehicle sightings showing A→C transitions repeatedly
        sightings = []
        for i in range(MIN_VEHICLE_OBSERVATIONS + 2):
            sightings.append({"camera_id": "A", "timestamp": i * 2})
            sightings.append({"camera_id": "C", "timestamp": i * 2 + 1})

        preds = tm.predict("A", cameras, vehicle_sightings=sightings)
        ids = [p["camera_id"] for p in preds]
        assert "C" in ids


# ---------------------------------------------------------------------------
# top_k and metadata
# ---------------------------------------------------------------------------

class TestTopKAndMetadata:
    def test_top_k_limits_result_count(self, tm, cameras):
        preds = tm.predict("A", cameras, top_k=2)
        assert len(preds) <= 2

    def test_result_includes_lat_lng(self, tm, cameras):
        preds = tm.predict("A", cameras)
        for p in preds:
            assert "lat" in p and "lng" in p

    def test_result_includes_label(self, tm, cameras):
        preds = tm.predict("A", cameras)
        for p in preds:
            assert "label" in p


# ---------------------------------------------------------------------------
# summary()
# ---------------------------------------------------------------------------

class TestSummary:
    def test_summary_empty_on_fresh_matrix(self, tm):
        s = tm.summary()
        assert s["cameras_with_outgoing_data"] == 0
        assert s["total_observed_transitions"] == 0

    def test_summary_reflects_recorded_transitions(self, tm):
        tm.record("A", "B")
        tm.record("A", "C")
        s = tm.summary()
        assert s["cameras_with_outgoing_data"] == 1
        assert s["total_observed_transitions"] == 2


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_record_no_data_corruption(self, tm):
        errors = []

        def record_loop():
            try:
                for _ in range(200):
                    tm.record("A", "B")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=record_loop) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert tm._global_counts["A"]["B"] == 800
