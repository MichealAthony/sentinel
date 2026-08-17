"""
tests/test_identity_engine.py

Unit tests for the Re-ID IdentityEngine.

Tests cover:
- First sighting always creates a new vehicle
- Similar embeddings match the same vehicle (cosine similarity above threshold)
- Dissimilar embeddings create distinct vehicles
- Centroid refinement: seen_count and camera_history grow correctly
- Plate voting: only high-confidence (≥0.85) readings count; requires
  PLATE_CONFIRMATION_MIN_VOTES consistent reads for confirmation
- should_save cooldown window
- cleanup() removes idle vehicles from memory AND from the FAISS index
"""
import numpy as np
import pytest

from app.services.identity_engine import IdentityEngine, PLATE_CONFIRMATION_MIN_VOTES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm(v):
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def _rand_emb(seed, dim=512):
    rng = np.random.default_rng(seed)
    return _norm(rng.standard_normal(dim).astype("float32"))


def _near(base, noise=0.02, seed=99):
    """Return an embedding close to base (should exceed similarity_threshold=0.78)."""
    rng = np.random.default_rng(seed)
    v = base + rng.standard_normal(base.shape).astype("float32") * noise
    return _norm(v)


def _far(seed=7, dim=512):
    """Return a random embedding unlikely to match anything else."""
    return _rand_emb(seed, dim)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def engine():
    return IdentityEngine(embedding_dim=512, similarity_threshold=0.78)


@pytest.fixture
def base_emb():
    return _rand_emb(seed=42)


# ---------------------------------------------------------------------------
# Create — first sighting
# ---------------------------------------------------------------------------

class TestCreate:
    def test_first_sighting_is_new(self, engine, base_emb):
        vid, is_new = engine.match_or_create(base_emb, timestamp=1000, camera_id="CAM_01")
        assert is_new is True
        assert isinstance(vid, str) and len(vid) > 0

    def test_none_embedding_returns_none(self, engine):
        vid, is_new = engine.match_or_create(None, timestamp=1000, camera_id="CAM_01")
        assert vid is None
        assert is_new is False

    def test_new_vehicle_stored_in_vehicles(self, engine, base_emb):
        vid, _ = engine.match_or_create(base_emb, timestamp=1000, camera_id="CAM_01")
        assert vid in engine.vehicles

    def test_faiss_index_grows_by_one(self, engine, base_emb):
        assert engine.index.ntotal == 0
        engine.match_or_create(base_emb, timestamp=1000, camera_id="CAM_01")
        assert engine.index.ntotal == 1

    def test_two_distinct_vehicles_stored_separately(self, engine):
        vid_a, _ = engine.match_or_create(_far(seed=1), timestamp=1000, camera_id="CAM_01")
        vid_b, _ = engine.match_or_create(_far(seed=2), timestamp=1001, camera_id="CAM_01")
        assert vid_a != vid_b
        assert engine.index.ntotal == 2


# ---------------------------------------------------------------------------
# Match — subsequent sightings of the same vehicle
# ---------------------------------------------------------------------------

class TestMatch:
    def test_similar_embedding_matches_same_vehicle(self, engine, base_emb):
        vid1, _ = engine.match_or_create(base_emb, timestamp=1000, camera_id="CAM_01")
        emb2 = _near(base_emb, noise=0.02, seed=10)
        vid2, is_new = engine.match_or_create(emb2, timestamp=1001, camera_id="CAM_02")
        assert is_new is False
        assert vid2 == vid1

    def test_dissimilar_embedding_creates_new_vehicle(self, engine, base_emb):
        vid1, _ = engine.match_or_create(base_emb, timestamp=1000, camera_id="CAM_01")
        emb2 = _far(seed=99)
        vid2, is_new = engine.match_or_create(emb2, timestamp=1001, camera_id="CAM_02")
        assert is_new is True
        assert vid2 != vid1

    def test_match_updates_seen_count(self, engine, base_emb):
        vid, _ = engine.match_or_create(base_emb, timestamp=1000, camera_id="CAM_01")
        engine.match_or_create(_near(base_emb), timestamp=1001, camera_id="CAM_02")
        assert engine.vehicles[vid]["seen_count"] == 2

    def test_match_appends_camera_history(self, engine, base_emb):
        vid, _ = engine.match_or_create(base_emb, timestamp=1000, camera_id="CAM_01")
        engine.match_or_create(_near(base_emb), timestamp=1001, camera_id="CAM_02")
        history = engine.vehicles[vid]["camera_history"]
        assert len(history) == 2
        assert history[1]["camera_id"] == "CAM_02"

    def test_faiss_index_not_grown_on_match(self, engine, base_emb):
        engine.match_or_create(base_emb, timestamp=1000, camera_id="CAM_01")
        engine.match_or_create(_near(base_emb), timestamp=1001, camera_id="CAM_02")
        assert engine.index.ntotal == 1  # still 1 vehicle


# ---------------------------------------------------------------------------
# Plate voting
# ---------------------------------------------------------------------------

class TestPlateVoting:
    def test_low_confidence_plate_not_stored(self, engine, base_emb):
        vid, _ = engine.match_or_create(
            base_emb, timestamp=1000, camera_id="CAM_01",
            plate_text="ABC123", plate_conf=0.50,   # below 0.85 threshold
        )
        assert engine.get_plate(vid) is None

    def test_high_confidence_plate_stored_on_create(self, engine, base_emb):
        vid, _ = engine.match_or_create(
            base_emb, timestamp=1000, camera_id="CAM_01",
            plate_text="ABC123", plate_conf=0.90,
        )
        assert engine.get_plate(vid) == "ABC123"

    def test_plate_not_confirmed_below_min_votes(self, engine, base_emb):
        vid, _ = engine.match_or_create(
            base_emb, timestamp=1000, camera_id="CAM_01",
            plate_text="ABC123", plate_conf=0.90,
        )
        # Only 1 vote — needs PLATE_CONFIRMATION_MIN_VOTES (default 3)
        assert engine.is_plate_confirmed(vid) is False

    def test_plate_confirmed_after_min_votes(self, engine, base_emb):
        vid, _ = engine.match_or_create(
            base_emb, timestamp=1000, camera_id="CAM_01",
            plate_text="ABC123", plate_conf=0.90,
        )
        for i in range(PLATE_CONFIRMATION_MIN_VOTES - 1):
            emb = _near(base_emb, noise=0.01, seed=i)
            engine.match_or_create(
                emb, timestamp=1001 + i, camera_id="CAM_02",
                plate_text="ABC123", plate_conf=0.90,
            )
        assert engine.is_plate_confirmed(vid) is True

    def test_majority_plate_wins_over_minority(self, engine, base_emb):
        # 3 votes for ABC123, 1 vote for WRONG1
        vid, _ = engine.match_or_create(
            base_emb, timestamp=1000, camera_id="CAM_01",
            plate_text="ABC123", plate_conf=0.90,
        )
        for i in range(2):
            engine.match_or_create(
                _near(base_emb, noise=0.01, seed=i), timestamp=1001 + i,
                camera_id="CAM_02", plate_text="ABC123", plate_conf=0.90,
            )
        engine.match_or_create(
            _near(base_emb, noise=0.01, seed=10), timestamp=1010,
            camera_id="CAM_03", plate_text="WRONG1", plate_conf=0.90,
        )
        assert engine.get_plate(vid) == "ABC123"

    def test_get_plate_returns_none_for_unknown_vehicle(self, engine):
        assert engine.get_plate("nonexistent-id") is None

    def test_is_plate_confirmed_returns_false_for_unknown_vehicle(self, engine):
        assert engine.is_plate_confirmed("nonexistent-id") is False


# ---------------------------------------------------------------------------
# should_save cooldown
# ---------------------------------------------------------------------------

class TestShouldSave:
    def test_first_call_always_saves(self, engine, base_emb):
        vid, _ = engine.match_or_create(base_emb, timestamp=1000, camera_id="CAM_01")
        # First save sets last_saved; we test the second call immediately after
        engine.should_save(vid, timestamp=1000)
        # Within cooldown — should NOT save
        assert engine.should_save(vid, timestamp=1002) is False

    def test_after_cooldown_saves_again(self, engine, base_emb):
        vid, _ = engine.match_or_create(base_emb, timestamp=1000, camera_id="CAM_01")
        engine.should_save(vid, timestamp=1000)
        assert engine.should_save(vid, timestamp=1010) is True  # 10s > 5s cooldown

    def test_unknown_vehicle_saves(self, engine):
        assert engine.should_save("ghost-id", timestamp=1000) is True


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

class TestCleanup:
    def test_idle_vehicle_removed_from_vehicles(self, engine, base_emb):
        vid, _ = engine.match_or_create(base_emb, timestamp=1000, camera_id="CAM_01")
        engine.cleanup(current_time=1000 + 601)   # 601s > 600s idle threshold
        assert vid not in engine.vehicles

    def test_active_vehicle_not_removed(self, engine, base_emb):
        vid, _ = engine.match_or_create(base_emb, timestamp=1000, camera_id="CAM_01")
        engine.cleanup(current_time=1000 + 60)    # only 60s — within threshold
        assert vid in engine.vehicles

    def test_cleanup_removes_vector_from_faiss(self, engine, base_emb):
        engine.match_or_create(base_emb, timestamp=1000, camera_id="CAM_01")
        assert engine.index.ntotal == 1
        engine.cleanup(current_time=1000 + 700)
        assert engine.index.ntotal == 0

    def test_cleanup_removes_faiss_id_mapping(self, engine, base_emb):
        vid, _ = engine.match_or_create(base_emb, timestamp=1000, camera_id="CAM_01")
        engine.cleanup(current_time=1000 + 700)
        assert vid not in engine.vehicle_to_faiss_id
