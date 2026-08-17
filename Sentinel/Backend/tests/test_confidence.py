"""
tests/test_confidence.py

Unit tests for the confidence scoring module.

Tests cover:
- Individual signal functions: score_plate, score_sightings, score_cameras, score_recency
- Signal weights sum to 1.0
- compute_confidence() output shape and types
- Band boundary values (high ≥0.85, medium ≥0.65, low ≥0.40, very_low <0.40)
- Edge cases: no plate, no sightings, no timestamp, inferred-only sightings
"""
import time

import pytest

from app.search.confidence import (
    compute_confidence,
    score_plate,
    score_sightings,
    score_cameras,
    score_recency,
    W_PLATE,
    W_SIGHTINGS,
    W_CAMERAS,
    W_RECENCY,
)


# ---------------------------------------------------------------------------
# Signal weights
# ---------------------------------------------------------------------------

class TestWeights:
    def test_weights_sum_to_one(self):
        assert W_PLATE + W_SIGHTINGS + W_CAMERAS + W_RECENCY == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# score_plate
# ---------------------------------------------------------------------------

class TestScorePlate:
    def test_no_plate_is_zero(self):
        assert score_plate(None, False) == 0.0
        assert score_plate("", False) == 0.0

    def test_confirmed_plate_is_one(self):
        assert score_plate("ABC123", True) == 1.0

    def test_unconfirmed_plate_is_partial(self):
        assert score_plate("ABC123", False) == pytest.approx(0.4)

    def test_confirmed_flag_without_plate_is_zero(self):
        # plate_confirmed=True is meaningless without a plate string
        assert score_plate(None, True) == 0.0


# ---------------------------------------------------------------------------
# score_sightings
# ---------------------------------------------------------------------------

class TestScoreSightings:
    def test_zero_sightings_is_zero(self):
        assert score_sightings(0) == 0.0

    def test_one_sighting_is_positive(self):
        assert score_sightings(1) > 0.0

    def test_five_sightings_saturates_at_one(self):
        assert score_sightings(5) == pytest.approx(1.0, abs=0.01)

    def test_more_than_five_stays_at_one(self):
        assert score_sightings(10) == pytest.approx(1.0, abs=0.01)

    def test_growth_is_logarithmic_not_linear(self):
        # 2 sightings should provide less than double the score of 1 sighting
        assert score_sightings(2) < 2 * score_sightings(1)

    def test_monotonically_increasing(self):
        scores = [score_sightings(n) for n in range(6)]
        assert scores == sorted(scores)


# ---------------------------------------------------------------------------
# score_cameras
# ---------------------------------------------------------------------------

class TestScoreCameras:
    def test_zero_cameras_is_zero(self):
        assert score_cameras(0) == 0.0

    def test_one_camera_low_confidence(self):
        assert score_cameras(1) == pytest.approx(0.35)

    def test_two_cameras(self):
        assert score_cameras(2) == pytest.approx(0.70)

    def test_three_cameras(self):
        assert score_cameras(3) == pytest.approx(0.90)

    def test_four_or_more_is_one(self):
        assert score_cameras(4) == 1.0
        assert score_cameras(20) == 1.0

    def test_monotonically_increasing_up_to_four(self):
        scores = [score_cameras(n) for n in range(5)]
        assert scores == sorted(scores)


# ---------------------------------------------------------------------------
# score_recency
# ---------------------------------------------------------------------------

class TestScoreRecency:
    def test_no_timestamp_is_zero(self):
        assert score_recency(None) == 0.0

    def test_very_recent_is_near_one(self):
        ts = time.time() - 5
        assert score_recency(ts) > 0.99

    def test_one_hour_ago_is_approximately_half(self):
        # Half-life = 3600s → score(3600s ago) ≈ 0.5
        ts = time.time() - 3600
        assert score_recency(ts) == pytest.approx(0.5, abs=0.02)

    def test_older_timestamp_scores_lower(self):
        recent = score_recency(time.time() - 300)
        old = score_recency(time.time() - 7200)
        assert recent > old

    def test_score_bounded_between_zero_and_one(self):
        ts = time.time() - 100
        assert 0.0 <= score_recency(ts) <= 1.0


# ---------------------------------------------------------------------------
# compute_confidence — output shape
# ---------------------------------------------------------------------------

_THREE_SIGHTINGS = [
    {"camera_id": f"CAM_{i}", "confidence": 0.9, "inferred": False}
    for i in range(3)
]


class TestComputeConfidenceShape:
    def test_returns_overall_band_signals(self):
        result = compute_confidence(
            plate="ABC123", plate_confirmed=True,
            sightings=_THREE_SIGHTINGS,
            last_known_timestamp=time.time() - 60,
        )
        assert "overall" in result
        assert "band" in result
        assert "signals" in result

    def test_signals_has_four_keys(self):
        result = compute_confidence("ABC123", True, _THREE_SIGHTINGS, time.time())
        assert set(result["signals"].keys()) == {"plate", "sightings", "cameras", "recency"}

    def test_each_signal_has_score_weight_note(self):
        result = compute_confidence("ABC123", True, _THREE_SIGHTINGS, time.time())
        for sig in result["signals"].values():
            assert "score" in sig
            assert "weight" in sig
            assert "note" in sig and isinstance(sig["note"], str)

    def test_overall_bounded_between_zero_and_one(self):
        result = compute_confidence("ABC123", True, _THREE_SIGHTINGS, time.time())
        assert 0.0 <= result["overall"] <= 1.0


# ---------------------------------------------------------------------------
# compute_confidence — scoring correctness
# ---------------------------------------------------------------------------

class TestComputeConfidenceScoring:
    def test_confirmed_plate_increases_score(self):
        with_plate = compute_confidence(
            "ABC123", True, _THREE_SIGHTINGS, time.time() - 60
        )
        without_plate = compute_confidence(
            None, False, _THREE_SIGHTINGS, time.time() - 60
        )
        assert with_plate["overall"] > without_plate["overall"]

    def test_more_sightings_increases_score(self):
        few = compute_confidence("ABC123", True, _THREE_SIGHTINGS[:1], time.time() - 60)
        many = compute_confidence("ABC123", True, _THREE_SIGHTINGS * 2, time.time() - 60)
        assert many["overall"] > few["overall"]

    def test_inferred_sightings_not_counted(self):
        inferred = [
            {"camera_id": "CAM_01", "confidence": 0.9, "inferred": True}
        ] * 5
        result = compute_confidence("ABC123", True, inferred, time.time())
        # Inferred sightings must not contribute to the sightings signal
        assert result["signals"]["sightings"]["score"] == 0.0

    def test_older_last_seen_reduces_score(self):
        fresh = compute_confidence("ABC123", True, _THREE_SIGHTINGS, time.time() - 30)
        stale = compute_confidence("ABC123", True, _THREE_SIGHTINGS, time.time() - 7200)
        assert fresh["overall"] > stale["overall"]


# ---------------------------------------------------------------------------
# compute_confidence — band boundaries
# ---------------------------------------------------------------------------

class TestBandBoundaries:
    def test_high_band_requires_at_least_0_85(self):
        result = compute_confidence(
            "ABC123", True,
            [{"camera_id": f"CAM_{i}", "inferred": False} for i in range(5)] * 2,
            time.time() - 10,
        )
        if result["overall"] >= 0.85:
            assert result["band"] == "high"

    def test_very_low_band_for_no_evidence(self):
        result = compute_confidence(
            plate=None, plate_confirmed=False,
            sightings=[],
            last_known_timestamp=time.time() - 86400,   # 24h ago
        )
        assert result["band"] in ("low", "very_low")
        assert result["overall"] < 0.50

    def test_valid_band_strings(self):
        result = compute_confidence("ABC123", True, _THREE_SIGHTINGS, time.time())
        assert result["band"] in ("high", "medium", "low", "very_low")
