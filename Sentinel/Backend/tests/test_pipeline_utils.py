"""
tests/test_pipeline_utils.py

Unit tests for the pure utility functions in the search pipeline.
All functions tested here are stateless and have no external dependencies,
so no mocking is required.

Tests cover:
- haversine_metres: distance calculation correctness and symmetry
- parse_datetime_to_timestamp: Jamaica TZ applied to naive datetimes,
  aware datetimes preserved as-is
- cosine_similarity: identical, orthogonal, opposite, zero-vector cases
- derive_boost_ttl: clamp behaviour at min, max, and mid-range values
- derive_boost_amount: formula correctness, min/max enforcement
- rank_cameras_by_proximity: nearest-first ordering, missing-coord exclusion
- cameras_in_ring: ring boundary inclusion/exclusion (500 m bands)
- filter_sightings_after: timestamp filtering (inclusive at boundary)
- reconstruct_trajectory: gap detection, inferred node insertion,
  coordinate and confidence passthrough
"""
from datetime import datetime, timezone, timedelta

import pytest

from app.search.pipeline import (
    haversine_metres,
    parse_datetime_to_timestamp,
    cosine_similarity,
    derive_boost_ttl,
    derive_boost_amount,
    rank_cameras_by_proximity,
    cameras_in_ring,
    filter_sightings_after,
    reconstruct_trajectory,
    JAMAICA_TZ,
    TTL_MIN_SECONDS,
    TTL_MAX_SECONDS,
    PROXIMITY_RING_METRES,
)


# ---------------------------------------------------------------------------
# haversine_metres
# ---------------------------------------------------------------------------

class TestHaversine:
    def test_same_point_is_zero(self):
        assert haversine_metres(18.0, -76.0, 18.0, -76.0) == pytest.approx(0.0, abs=0.01)

    def test_result_is_positive(self):
        assert haversine_metres(18.0, -76.0, 18.001, -76.001) > 0.0

    def test_symmetric(self):
        a = haversine_metres(18.0, -76.0, 18.1, -76.1)
        b = haversine_metres(18.1, -76.1, 18.0, -76.0)
        assert a == pytest.approx(b, rel=1e-6)

    def test_roughly_correct_magnitude(self):
        # 1 degree of latitude ≈ 111 km
        dist = haversine_metres(0.0, 0.0, 1.0, 0.0)
        assert 110_000 < dist < 112_000

    def test_close_cameras_short_distance(self):
        # Two cameras ~500 m apart in Kingston
        dist = haversine_metres(18.0000, -76.8000, 18.0045, -76.8000)
        assert 400 < dist < 600


# ---------------------------------------------------------------------------
# parse_datetime_to_timestamp
# ---------------------------------------------------------------------------

class TestParseDatetime:
    def test_naive_datetime_assigned_jamaica_tz(self):
        dt_str = "2024-06-01T12:00:00"
        ts = parse_datetime_to_timestamp(dt_str)
        expected = datetime(2024, 6, 1, 12, 0, 0, tzinfo=JAMAICA_TZ).timestamp()
        assert ts == pytest.approx(expected, abs=1.0)

    def test_utc_aware_datetime_not_shifted(self):
        dt_str = "2024-06-01T17:00:00+00:00"
        ts = parse_datetime_to_timestamp(dt_str)
        expected = datetime(2024, 6, 1, 17, 0, 0, tzinfo=timezone.utc).timestamp()
        assert ts == pytest.approx(expected, abs=1.0)

    def test_explicit_tz_offset_preserved(self):
        # +05:30 IST — should NOT receive Jamaica offset
        dt_str = "2024-06-01T12:00:00+05:30"
        ts = parse_datetime_to_timestamp(dt_str)
        expected = datetime(2024, 6, 1, 6, 30, 0, tzinfo=timezone.utc).timestamp()
        assert ts == pytest.approx(expected, abs=1.0)

    def test_naive_and_jamaica_explicit_match(self):
        # A naive "2024-01-01T10:00:00" should equal "2024-01-01T10:00:00-05:00"
        naive_ts = parse_datetime_to_timestamp("2024-01-01T10:00:00")
        aware_ts = parse_datetime_to_timestamp("2024-01-01T10:00:00-05:00")
        assert naive_ts == pytest.approx(aware_ts, abs=1.0)


# ---------------------------------------------------------------------------
# cosine_similarity
# ---------------------------------------------------------------------------

class TestCosineSimilarity:
    def test_identical_vectors_return_one(self):
        v = [1.0, 0.0, 0.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors_return_zero(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0, abs=1e-6)

    def test_opposite_vectors_return_minus_one(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(-1.0, abs=1e-6)

    def test_zero_vector_returns_zero(self):
        assert cosine_similarity([0.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == 0.0

    def test_both_zero_vectors_return_zero(self):
        assert cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0


# ---------------------------------------------------------------------------
# derive_boost_ttl
# ---------------------------------------------------------------------------

class TestBoostTtl:
    def test_short_staleness_clamps_to_min(self):
        # 30s × 1.5 = 45s < TTL_MIN_SECONDS (60)
        assert derive_boost_ttl(30) == TTL_MIN_SECONDS

    def test_long_staleness_clamps_to_max(self):
        # 1000s × 1.5 = 1500s > TTL_MAX_SECONDS (600)
        assert derive_boost_ttl(1000) == TTL_MAX_SECONDS

    def test_mid_range_scales_linearly(self):
        # 200s × 1.5 = 300s — within [60, 600]
        ttl = derive_boost_ttl(200)
        assert TTL_MIN_SECONDS <= ttl <= TTL_MAX_SECONDS
        assert ttl == pytest.approx(300.0, abs=1.0)

    def test_infinite_staleness_clamps_to_max(self):
        assert derive_boost_ttl(float("inf")) == TTL_MAX_SECONDS

    def test_result_is_float(self):
        assert isinstance(derive_boost_ttl(120), float)


# ---------------------------------------------------------------------------
# derive_boost_amount
# ---------------------------------------------------------------------------

class TestBoostAmount:
    def test_minimum_amount_enforced(self):
        # Camera at max priority with zero staleness — still gets BOOST_MIN=5
        amount = derive_boost_amount(
            target_base_priority=10, max_network_priority=10, staleness_seconds=0
        )
        assert amount >= 5

    def test_maximum_amount_enforced(self):
        amount = derive_boost_amount(
            target_base_priority=1, max_network_priority=10, staleness_seconds=100_000
        )
        assert amount <= 50

    def test_stale_camera_gets_more_boost_than_fresh(self):
        fresh = derive_boost_amount(5, 10, 0)
        stale = derive_boost_amount(5, 10, 1800)
        assert stale > fresh

    def test_low_base_priority_gets_more_boost(self):
        low_pri  = derive_boost_amount(1, 10, 300)   # large gap from max
        high_pri = derive_boost_amount(9, 10, 300)   # small gap from max
        assert low_pri >= high_pri

    def test_result_is_int(self):
        assert isinstance(derive_boost_amount(5, 10, 300), int)


# ---------------------------------------------------------------------------
# rank_cameras_by_proximity
# ---------------------------------------------------------------------------

class TestRankByProximity:
    @pytest.fixture
    def cameras(self):
        return {
            "near": {"lat": 18.0010, "lng": -76.8000},
            "mid":  {"lat": 18.0050, "lng": -76.8000},
            "far":  {"lat": 18.0100, "lng": -76.8000},
        }

    def test_nearest_camera_first(self, cameras):
        ranked = rank_cameras_by_proximity(18.0000, -76.8000, cameras)
        assert ranked[0][0] == "near"

    def test_farthest_camera_last(self, cameras):
        ranked = rank_cameras_by_proximity(18.0000, -76.8000, cameras)
        assert ranked[-1][0] == "far"

    def test_distances_are_non_negative(self, cameras):
        ranked = rank_cameras_by_proximity(18.0000, -76.8000, cameras)
        assert all(d >= 0 for _, d in ranked)

    def test_cameras_missing_coordinates_excluded(self):
        cameras = {
            "valid":   {"lat": 18.001, "lng": -76.800},
            "no_lat":  {"lng": -76.800},
            "no_lng":  {"lat": 18.001},
            "neither": {},
        }
        ranked = rank_cameras_by_proximity(18.000, -76.800, cameras)
        ids = [r[0] for r in ranked]
        assert ids == ["valid"]

    def test_empty_cameras_returns_empty(self):
        assert rank_cameras_by_proximity(18.0, -76.0, {}) == []


# ---------------------------------------------------------------------------
# cameras_in_ring
# ---------------------------------------------------------------------------

class TestCamerasInRing:
    def test_ring_zero_captures_0_to_500m(self):
        ranked = [("near", 200), ("boundary", 499), ("edge", 500)]
        result = cameras_in_ring(ranked, 0)
        assert "near" in result
        assert "boundary" in result
        assert "edge" not in result  # 500 is the start of ring 1

    def test_ring_one_captures_500_to_1000m(self):
        ranked = [("a", 200), ("b", 700), ("c", 1200)]
        assert cameras_in_ring(ranked, 1) == ["b"]

    def test_empty_ring_returns_empty_list(self):
        ranked = [("a", 200)]
        assert cameras_in_ring(ranked, 1) == []

    def test_ring_boundary_inclusive_at_lower_end(self):
        # Exactly 500 m → belongs to ring 1, not ring 0
        ranked = [("exactly500", 500)]
        assert cameras_in_ring(ranked, 0) == []
        assert cameras_in_ring(ranked, 1) == ["exactly500"]

    def test_multiple_cameras_per_ring(self):
        ranked = [("a", 100), ("b", 300), ("c", 700)]
        assert set(cameras_in_ring(ranked, 0)) == {"a", "b"}


# ---------------------------------------------------------------------------
# filter_sightings_after
# ---------------------------------------------------------------------------

class TestFilterSightingsAfter:
    def test_includes_sighting_exactly_at_boundary(self):
        sightings = [{"camera_id": "A", "timestamp": 1000}]
        result = filter_sightings_after(sightings, after_timestamp=1000)
        assert len(result) == 1

    def test_excludes_sightings_before_threshold(self):
        sightings = [
            {"camera_id": "A", "timestamp": 900},
            {"camera_id": "B", "timestamp": 1000},
            {"camera_id": "C", "timestamp": 1100},
        ]
        result = filter_sightings_after(sightings, after_timestamp=1000)
        assert [s["camera_id"] for s in result] == ["B", "C"]

    def test_all_old_returns_empty(self):
        sightings = [{"camera_id": "A", "timestamp": 500}]
        assert filter_sightings_after(sightings, after_timestamp=1000) == []

    def test_empty_input_returns_empty(self):
        assert filter_sightings_after([], after_timestamp=1000) == []

    def test_all_new_returns_all(self):
        sightings = [
            {"camera_id": "A", "timestamp": 2000},
            {"camera_id": "B", "timestamp": 3000},
        ]
        result = filter_sightings_after(sightings, after_timestamp=1000)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# reconstruct_trajectory
# ---------------------------------------------------------------------------

class TestReconstructTrajectory:
    """
    Camera A and B are ~150 m apart.
    At ASSUMED_SPEED_MS = 11.1 m/s: min_travel ≈ 13.5 s → gap threshold ≈ 27 s.
    """

    @pytest.fixture
    def cameras(self):
        return {
            "A": {"label": "Alpha", "lat": 18.0000, "lng": -76.8000},
            "B": {"label": "Beta",  "lat": 18.0010, "lng": -76.8010},
        }

    def test_empty_sightings_returns_empty(self, cameras):
        assert reconstruct_trajectory([], cameras) == []

    def test_single_sighting_no_gap(self, cameras):
        sightings = [{"camera_id": "A", "timestamp": 1000, "plate_confidence": 0.9}]
        traj = reconstruct_trajectory(sightings, cameras)
        assert len(traj) == 1
        assert traj[0]["inferred"] is False
        assert traj[0]["camera_id"] == "A"

    def test_coordinates_attached_from_camera_metadata(self, cameras):
        sightings = [{"camera_id": "A", "timestamp": 1000, "plate_confidence": 0.9}]
        traj = reconstruct_trajectory(sightings, cameras)
        assert traj[0]["lat"] == cameras["A"]["lat"]
        assert traj[0]["lng"] == cameras["A"]["lng"]

    def test_plate_confidence_preserved(self, cameras):
        sightings = [{"camera_id": "A", "timestamp": 1000, "plate_confidence": 0.92}]
        traj = reconstruct_trajectory(sightings, cameras)
        assert traj[0]["confidence"] == 0.92

    def test_short_gap_no_inferred_node(self, cameras):
        # 20s gap — below the ~27s threshold for A↔B at 150 m apart
        sightings = [
            {"camera_id": "A", "timestamp": 1000, "plate_confidence": 0.9},
            {"camera_id": "B", "timestamp": 1020, "plate_confidence": 0.9},
        ]
        traj = reconstruct_trajectory(sightings, cameras)
        assert all(not n["inferred"] for n in traj)

    def test_large_gap_inserts_inferred_node(self, cameras):
        # 600s gap far exceeds the ~27s threshold
        sightings = [
            {"camera_id": "A", "timestamp": 1000, "plate_confidence": 0.9},
            {"camera_id": "B", "timestamp": 1600, "plate_confidence": 0.9},
        ]
        traj = reconstruct_trajectory(sightings, cameras)
        inferred = [n for n in traj if n["inferred"]]
        assert len(inferred) == 1
        assert inferred[0]["reason"] == "travel_time_gap"

    def test_inferred_node_has_gap_seconds(self, cameras):
        sightings = [
            {"camera_id": "A", "timestamp": 1000, "plate_confidence": 0.9},
            {"camera_id": "B", "timestamp": 1600, "plate_confidence": 0.9},
        ]
        traj = reconstruct_trajectory(sightings, cameras)
        inferred = next(n for n in traj if n["inferred"])
        assert inferred["gap_seconds"] > 0

    def test_same_consecutive_camera_no_gap_check(self, cameras):
        # Two sightings on the same camera — gap check is skipped by design
        sightings = [
            {"camera_id": "A", "timestamp": 1000, "plate_confidence": 0.9},
            {"camera_id": "A", "timestamp": 5000, "plate_confidence": 0.9},
        ]
        traj = reconstruct_trajectory(sightings, cameras)
        # No gap inserted between same-camera sightings
        assert all(not n["inferred"] for n in traj)

    def test_unknown_camera_in_sighting_handled_gracefully(self, cameras):
        sightings = [
            {"camera_id": "A",       "timestamp": 1000, "plate_confidence": 0.9},
            {"camera_id": "UNKNOWN", "timestamp": 1600, "plate_confidence": 0.9},
        ]
        traj = reconstruct_trajectory(sightings, cameras)
        # Should not raise; unknown camera gets None lat/lng but no crash
        assert len(traj) >= 1
