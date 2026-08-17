"""
app/search/pipeline.py

Search pipeline — six-phase vehicle search with:
- Proximity-ordered camera search expanding outward from last known location
- Derived boost amount and TTL based on network priority and staleness
- Stolen vehicle cross-reference
- Overall match confidence scoring
- Markov Chain trajectory prediction with global matrix and topology prior
- Explicit trajectory gap flagging

DESIGN DECISIONS
----------------
1. Search anchored to last known location, expands in proximity rings.
   Closer cameras searched first. Search centre updates to last confirmed
   sighting if the vehicle was found, otherwise stays at operator location.

2. Boost amount derived from network priority range and staleness.
   boost_amount = (max_base_priority - target_base_priority) + urgency_factor
   urgency_factor = clamp(staleness_seconds / 300, 0, 3) × URGENCY_WEIGHT
   This ensures boosted cameras always outrank all others, with additional
   headroom proportional to how urgently fresh data is needed.

3. Boost TTL derived from staleness.
   TTL = clamp(staleness × 1.5, 60s, 600s)
   A camera 2 hours stale cannot catch up in 60 seconds.

4. Freshness threshold is 5 minutes, not 24 hours.
   24 hours means yesterday's data is considered fresh — wrong for live search.

5. Identity resolution uses plate confirmation + recency, not inter-candidate
   embedding similarity. With no query image, comparing candidates against
   each other is unreliable.

6. Stolen vehicle cross-reference.
   If the located vehicle matches a stolen vehicle report, the result
   is flagged so the operator can take appropriate action.

7. Overall confidence score derived from four weighted signals:
   plate confirmation, sighting count, camera diversity, recency.
   Gives the operator a single indicator of result reliability.

8. Markov prediction combines per-vehicle, global, and topology sources
   with adaptive weights. Topology prior ensures useful predictions at
   cold start before enough trajectories are accumulated.
"""

import time
import math
import asyncio
import httpx
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.search.markov import transition_matrix
from app.search.confidence import compute_confidence
from app.core.config import TIMEZONE_OFFSET_HOURS
from app.utils.geo import haversine_metres

# =========================================================
# CONSTANTS
# =========================================================

FRESHNESS_THRESHOLD_SECONDS = 300

TTL_STALENESS_MULTIPLIER = 1.5
TTL_MIN_SECONDS = 60
TTL_MAX_SECONDS = 600

# Boost amount derivation
# urgency_factor = clamp(staleness / 300, 0, 3) × URGENCY_WEIGHT
# This adds up to 3 × URGENCY_WEIGHT on top of the base amount for very stale cameras
URGENCY_WEIGHT = 5

BACKOFF_INITIAL_SECONDS = 2.0
BACKOFF_MULTIPLIER = 2.0
BACKOFF_MAX_WAIT_SECONDS = 90.0

PIPELINE_BOOST_URL = "http://localhost:8000/pipeline/boost"

ASSUMED_SPEED_MS = 11.1  # 40 km/h urban average

_stolen_check_warned = False

PROXIMITY_RING_METRES = 500
MAX_RINGS = 6

JAMAICA_TZ = timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS))


# =========================================================
# UTILITIES
# =========================================================


def parse_datetime_to_timestamp(dt_string: str) -> float:
    dt = datetime.fromisoformat(dt_string)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JAMAICA_TZ)
    return dt.timestamp()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    va = np.array(a, dtype="float32")
    vb = np.array(b, dtype="float32")
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def derive_boost_ttl(staleness_seconds: float) -> float:
    """TTL scales with staleness — stale cameras need more time to catch up."""
    if math.isinf(staleness_seconds):
        return float(TTL_MAX_SECONDS)
    return float(max(TTL_MIN_SECONDS, min(TTL_MAX_SECONDS, staleness_seconds * TTL_STALENESS_MULTIPLIER)))


def derive_boost_amount(
    target_base_priority: int,
    max_network_priority: int,
    staleness_seconds: float,
) -> int:
    """
    Compute boost amount from network priority structure and staleness.

    Formula:
        base_gap    = max_network_priority - target_base_priority
        urgency     = clamp(staleness / 300, 0, 3) × URGENCY_WEIGHT
        boost       = clamp(base_gap + urgency, BOOST_MIN, BOOST_MAX)

    base_gap ensures the boosted camera outranks ALL other cameras in the
    network regardless of their base priorities.

    urgency adds extra headroom proportional to how stale the camera is:
        - 5 min stale  → urgency = 1 × URGENCY_WEIGHT
        - 15 min stale → urgency = 3 × URGENCY_WEIGHT (capped)

    target_base_priority is subtracted implicitly — a chokepoint camera at
    base 10 needs less raw boost than a side street camera at base 3 to
    dominate the scheduler, because it starts from a higher base.

    Result is always at least BOOST_MIN so even the highest-priority
    camera gets a meaningful elevation.
    """
    BOOST_MIN = 5
    BOOST_MAX = 50

    base_gap = max(0, max_network_priority - target_base_priority)
    if math.isinf(staleness_seconds):
        urgency = 3.0 * URGENCY_WEIGHT
    else:
        urgency = min(staleness_seconds / 300, 3.0) * URGENCY_WEIGHT

    raw = base_gap + urgency
    return int(max(BOOST_MIN, min(BOOST_MAX, round(raw))))


# =========================================================
# CAMERA RANKING
# =========================================================

def rank_cameras_by_proximity(
    centre_lat: float,
    centre_lng: float,
    cameras: dict[str, dict],
) -> list[tuple[str, float]]:
    distances = []
    for cid, cam in cameras.items():
        if not cam.get("lat") or not cam.get("lng"):
            continue
        dist = haversine_metres(centre_lat, centre_lng, cam["lat"], cam["lng"])
        distances.append((cid, dist))
    return sorted(distances, key=lambda x: x[1])


def cameras_in_ring(ranked: list[tuple[str, float]], ring_index: int) -> list[str]:
    low  = ring_index * PROXIMITY_RING_METRES
    high = (ring_index + 1) * PROXIMITY_RING_METRES
    return [cid for cid, dist in ranked if low <= dist < high]


# =========================================================
# IDENTITY RESOLUTION
# =========================================================

async def resolve_identity(candidates: list[dict]) -> Optional[dict]:
    """
    Select the most likely vehicle from metadata-filter candidates.

    Priority:
    1. Single candidate — return immediately.
    2. Prefer plate-confirmed candidates.
    3. Among those, prefer most recently seen.
    4. Use embedding similarity as tiebreaker when multiple confirmed
       candidates exist — compare against the most recent one's embedding.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    confirmed   = [c for c in candidates if c.get("plate_confirmed")]
    unconfirmed = [c for c in candidates if not c.get("plate_confirmed")]
    pool = sorted(confirmed if confirmed else unconfirmed,
                  key=lambda c: c.get("last_seen", 0), reverse=True)

    if len(pool) == 1:
        return pool[0]

    # Embedding tiebreaker
    from app.search import client  # noqa: PLC0415
    reference     = pool[0]
    ref_embedding = await client.get_embedding(reference["vehicle_id"])
    if ref_embedding is None:
        return reference

    best, best_score = reference, 1.0
    for candidate in pool[1:]:
        emb = await client.get_embedding(candidate["vehicle_id"])
        if emb is None:
            continue
        score = cosine_similarity(ref_embedding, emb)
        if score > best_score:
            best_score = score
            best = candidate

    return best


# =========================================================
# FRESHNESS AND BOOST
# =========================================================

async def check_freshness(camera_ids: list[str]) -> dict[str, float]:
    """Return {camera_id: staleness_seconds}. inf = never indexed."""
    from app.search import client  # noqa: PLC0415
    now = time.time()
    result = {}
    for cid in camera_ids:
        last = await client.get_last_indexed(cid)
        result[cid] = float("inf") if last is None else max(0.0, now - last)
    return result


async def boost_stale_cameras(
    stale: dict[str, float],
    cameras: dict[str, dict],
) -> None:
    """
    Send boost signals with derived amounts and TTLs.
    Boost amount is computed per-camera from network priority structure
    and individual staleness.
    """
    if not stale:
        return

    max_priority = max(
        (cam.get("base_priority", 5) for cam in cameras.values()),
        default=10
    )

    for cid, staleness_sec in stale.items():
        cam = cameras.get(cid, {})
        target_priority = cam.get("base_priority", 5)
        amount = derive_boost_amount(target_priority, max_priority, staleness_sec)
        ttl    = derive_boost_ttl(staleness_sec)
        await _send_boost([cid], amount, ttl)


async def _send_boost(camera_ids: list[str], boost_amount: int, ttl_seconds: float) -> None:
    payload = {"camera_ids": camera_ids, "boost_amount": boost_amount, "ttl_seconds": ttl_seconds}
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            await http.post(PIPELINE_BOOST_URL, json=payload)
        print(f"[Search] Boosted {camera_ids} amount={boost_amount} ttl={ttl_seconds:.0f}s")
    except Exception as e:
        print(f"[Search] Boost failed for {camera_ids}: {e}")


async def wait_for_freshness(camera_ids: list[str]) -> tuple[bool, list[str]]:
    """Exponential backoff wait for cameras to become fresh."""
    elapsed, wait = 0.0, BACKOFF_INITIAL_SECONDS
    still_stale = list(camera_ids)

    while elapsed < BACKOFF_MAX_WAIT_SECONDS and still_stale:
        await asyncio.sleep(wait)
        elapsed += wait
        wait = min(wait * BACKOFF_MULTIPLIER, BACKOFF_MAX_WAIT_SECONDS - elapsed)

        now = time.time()
        from app.search import client  # noqa: PLC0415
        still_stale = [
            cid for cid in still_stale
            if (lambda last: last is None or (now - last) > FRESHNESS_THRESHOLD_SECONDS)(
                await client.get_last_indexed(cid)
            )
        ]

    return (len(still_stale) == 0, still_stale)


# =========================================================
# STOLEN VEHICLE CHECK
# =========================================================

async def check_stolen(vehicle_id: str, plate: Optional[str]) -> Optional[dict]:
    """
    Cross-reference the located vehicle against stolen vehicle reports.
    Returns the stolen vehicle report if a match is found, None otherwise.

    Matching logic:
    1. Plate match (primary) — exact match against reported stolen plates
    2. Vehicle ID match (secondary) — if vehicle_id was previously flagged

    Returns:
    {
      "report_id": "...",
      "plate": "ABC1234",
      "reported_at": 1710000000.0,
      "reported_by": "Kingston Central Police",
      "vehicle_type": "car",
      "colour": "black"
    }
    or None if no match.
    """
    global _stolen_check_warned
    try:
        from app.search import client  # noqa: PLC0415
        report = await client.get_stolen_report(vehicle_id=vehicle_id, plate=plate)
        return report
    except NotImplementedError:
        if not _stolen_check_warned:
            print("[Search] Stolen vehicle check not implemented — stolen_report will always be null.")
            _stolen_check_warned = True
        return None
    except Exception as e:
        print(f"[Search] Stolen check failed: {e}")
        return None


# =========================================================
# SIGHTING FILTER
# =========================================================

def filter_sightings_after(sightings: list[dict], after_timestamp: float) -> list[dict]:
    """Return sightings at or after after_timestamp."""
    return [s for s in sightings if s.get("timestamp", 0) >= after_timestamp]


# =========================================================
# TRAJECTORY RECONSTRUCTION
# =========================================================

def reconstruct_trajectory(
    sightings: list[dict],
    cameras: dict[str, dict],
) -> list[dict]:
    """
    Build trajectory from ordered sightings with explicit gap flagging.

    A gap node is inserted between two consecutive sightings when:
      actual_gap > 2 × min_travel_time

    The factor of 2 accounts for route variation, speed variation, and
    pipeline processing delays. Gaps are flagged explicitly rather than
    silently omitted so the operator sees where the system is inferring.
    """
    if not sightings:
        return []

    trajectory = []

    for i, sighting in enumerate(sightings):
        cam_meta = cameras.get(sighting["camera_id"], {})
        trajectory.append({
            "camera_id": sighting["camera_id"],
            "label":     cam_meta.get("label", sighting["camera_id"]),
            "lat":       cam_meta.get("lat"),
            "lng":       cam_meta.get("lng"),
            "timestamp": sighting["timestamp"],
            "confidence": sighting.get("plate_confidence"),
            "inferred":  False,
        })

        if i >= len(sightings) - 1:
            continue

        next_s = sightings[i + 1]
        if sighting["camera_id"] == next_s["camera_id"]:
            continue

        curr_cam = cameras.get(sighting["camera_id"])
        next_cam = cameras.get(next_s["camera_id"])
        if not (curr_cam and next_cam and curr_cam.get("lat") and next_cam.get("lat")):
            continue

        dist_m = haversine_metres(curr_cam["lat"], curr_cam["lng"], next_cam["lat"], next_cam["lng"])
        min_travel = dist_m / ASSUMED_SPEED_MS
        actual_gap = next_s["timestamp"] - sighting["timestamp"]

        if actual_gap > min_travel * 2:
            trajectory.append({
                "camera_id":  None,
                "label":      None,
                "lat":        None,
                "lng":        None,
                "timestamp":  None,
                "confidence": None,
                "inferred":   True,
                "reason":     "travel_time_gap",
                "gap_seconds": round(actual_gap - min_travel, 1),
            })

    return trajectory


# =========================================================
# MAIN SEARCH ENTRY POINT
# =========================================================

async def run(
    plate: Optional[str],
    vehicle_type: Optional[str],
    colour: Optional[str],
    last_seen_at: str,
    last_known_location: dict,
    cameras: Optional[dict[str, dict]] = None,
) -> dict:
    """
    Six-phase vehicle search pipeline.

    Phase 1: Parse timestamp and load camera metadata
    Phase 2: Metadata filter — find candidate vehicles
    Phase 3: Identity resolution — select most likely vehicle
    Phase 4: Proximity-ordered freshness check, derived boost, backoff wait
    Phase 5: Trajectory reconstruction with gap flagging
    Phase 6: Markov prediction + confidence scoring + stolen vehicle check
    """

    # --- Phase 1: Parse and load ---
    try:
        after_timestamp = parse_datetime_to_timestamp(last_seen_at)
    except Exception:
        return {"error": "invalid_input", "message": "last_seen_at could not be parsed."}

    if cameras is None:
        from app.search import client  # noqa: PLC0415
        cameras = await client.get_all_cameras()

    centre_lat = last_known_location.get("lat")
    centre_lng = last_known_location.get("lng")

    # --- Phase 2: Metadata filter ---
    from app.search import client  # noqa: PLC0415
    candidates = await client.search_vehicles(plate=plate, vehicle_type=vehicle_type, colour=colour)
    if not candidates:
        return {"error": "no_match", "message": "No vehicles found matching the description."}

    # --- Phase 3: Identity resolution ---
    vehicle = await resolve_identity(candidates)
    if not vehicle:
        return {"error": "no_match", "message": "Could not resolve vehicle identity."}

    vehicle_id = vehicle["vehicle_id"]

    # Initial sightings fetch
    from app.search import client  # noqa: PLC0415
    all_sightings = await client.get_sightings(vehicle_id)
    relevant_sightings = filter_sightings_after(all_sightings, after_timestamp)

    # --- Phase 4: Proximity-ordered freshness check and boost ---
    staleness_warning = False

    # Determine search centre: last confirmed sighting if available,
    # otherwise operator-provided last known location
    if relevant_sightings:
        last_cam = cameras.get(relevant_sightings[-1]["camera_id"], {})
        search_lat = last_cam.get("lat") or centre_lat
        search_lng = last_cam.get("lng") or centre_lng
    else:
        search_lat, search_lng = centre_lat, centre_lng

    if search_lat and search_lng:
        search_ranked = rank_cameras_by_proximity(search_lat, search_lng, cameras)

        for ring in range(MAX_RINGS):
            ring_cams = cameras_in_ring(search_ranked, ring)
            if not ring_cams:
                continue

            staleness = await check_freshness(ring_cams)
            stale = {cid: s for cid, s in staleness.items() if s > FRESHNESS_THRESHOLD_SECONDS}

            if stale:
                await boost_stale_cameras(stale, cameras)
                staleness_warning = True  # cameras were stale; watcher updates when fresh

    # --- Phase 5: Trajectory reconstruction ---
    trajectory = reconstruct_trajectory(relevant_sightings, cameras)

    # Record observed transitions to improve future predictions
    if len(relevant_sightings) > 1:
        transition_matrix.record_trajectory(relevant_sightings)

    # --- Phase 6: Prediction, confidence, stolen check ---
    predicted_next      = []
    last_known_camera   = None
    last_known_timestamp = None

    if relevant_sightings:
        last_known_camera    = relevant_sightings[-1]["camera_id"]
        last_known_timestamp = relevant_sightings[-1]["timestamp"]

        predicted_next = transition_matrix.predict(
            from_camera=last_known_camera,
            cameras=cameras,
            vehicle_sightings=relevant_sightings,
            top_k=5,
        )

    # Confidence scoring
    plate_confirmed = vehicle.get("plate_confirmed", False)
    confidence = compute_confidence(
        plate=vehicle.get("best_plate"),
        plate_confirmed=plate_confirmed,
        sightings=trajectory,
        last_known_timestamp=last_known_timestamp,
    )

    # Stolen vehicle cross-reference
    stolen_report = await check_stolen(vehicle_id, vehicle.get("best_plate"))

    return {
        "vehicle_id":           vehicle_id,
        "plate":                vehicle.get("best_plate"),
        "plate_confirmed":      plate_confirmed,
        "vehicle_type":         vehicle.get("vehicle_type"),
        "colour_label":         vehicle.get("colour_label"),
        "colour_rgb":           vehicle.get("colour_rgb"),
        "sightings":            trajectory,
        "predicted_next":       predicted_next,
        "last_known_camera":    last_known_camera,
        "last_known_timestamp": last_known_timestamp,
        "staleness_warning":    staleness_warning,
        "confidence":           confidence,
        "stolen_report":        stolen_report,
    }
