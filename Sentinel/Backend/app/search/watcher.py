"""
app/search/watcher.py

Background investigation watcher.

Two loops run on every tick:

1. ACTIVE investigations — polls for new DB sightings every POLL_INTERVAL_SECONDS.
   When new sightings arrive for a tracked vehicle:
     a. Rebuilds the full trajectory since the investigation's after_timestamp
     b. Updates the stored result — triggers has_update on next poll
     c. Boosts predicted-next cameras to stay ahead of the vehicle's movement

2. PENDING investigations — retries the metadata search every PENDING_RETRY_SECONDS.
   When the vehicle first appears in the DB:
     a. Runs the full pipeline to build an initial result
     b. Activates the investigation (status: pending → active, has_update: True)
     c. The UI poll detects has_update and fetches the populated result

This is what allows operators to open an investigation before a vehicle is
seen — the system watches and self-populates when the vehicle appears.
"""

import asyncio

from app.search import client, pipeline
from app.search.markov import transition_matrix
from app.search.confidence import compute_confidence
from app.search.investigations import investigation_store

POLL_INTERVAL_SECONDS = 15
PENDING_RETRY_SECONDS = 10


async def _refresh_investigation(inv) -> bool:
    """
    Check for new DB sightings and update the stored result if any are found.
    Returns True if the investigation was updated.
    """
    if not inv.result:
        return False

    vehicle_id = inv.result.get("vehicle_id")
    if not vehicle_id:
        return False

    last_ts = inv.result.get("last_known_timestamp") or 0

    all_sightings = await client.get_sightings(vehicle_id)
    if not all_sightings:
        return False

    newest_ts = max(s.get("timestamp", 0) for s in all_sightings)
    if newest_ts <= last_ts:
        return False

    # New sightings found — rebuild trajectory from the investigation's start time
    cameras = await client.get_all_cameras()

    try:
        after_ts = pipeline.parse_datetime_to_timestamp(inv.params["last_seen_at"])
    except Exception:
        after_ts = 0

    relevant = pipeline.filter_sightings_after(all_sightings, after_ts)
    if not relevant:
        return False

    trajectory = pipeline.reconstruct_trajectory(relevant, cameras)

    last_known_camera = relevant[-1]["camera_id"]
    last_known_timestamp = relevant[-1]["timestamp"]

    if len(relevant) > 1:
        transition_matrix.record_trajectory(relevant)

    predicted_next = transition_matrix.predict(
        from_camera=last_known_camera,
        cameras=cameras,
        vehicle_sightings=relevant,
        top_k=5,
    )

    confidence = compute_confidence(
        plate=inv.result.get("plate"),
        plate_confirmed=inv.result.get("plate_confirmed", False),
        sightings=trajectory,
        last_known_timestamp=last_known_timestamp,
    )

    # Boost predicted-next cameras that are stale so we're ready for the next move
    if predicted_next:
        next_cam_ids = [p["camera_id"] for p in predicted_next[:3]]
        staleness = await pipeline.check_freshness(next_cam_ids)
        stale = {cid: s for cid, s in staleness.items()
                 if s > pipeline.FRESHNESS_THRESHOLD_SECONDS}
        if stale:
            await pipeline.boost_stale_cameras(stale, cameras)

    investigation_store.update_result(inv.investigation_id, {
        **inv.result,
        "sightings": trajectory,
        "predicted_next": predicted_next,
        "last_known_camera": last_known_camera,
        "last_known_timestamp": last_known_timestamp,
        "confidence": confidence,
    })
    return True


async def _try_activate_pending(inv) -> bool:
    """
    Retry a pending investigation. If the vehicle now exists in the DB,
    run the full pipeline and activate the investigation.
    Returns True if activated.
    """
    params = inv.params

    # Quick check — is there any matching vehicle in the DB yet?
    candidates = await client.search_vehicles(
        plate=params.get("plate"),
        vehicle_type=params.get("vehicle_type"),
        colour=params.get("colour"),
    )
    if not candidates:
        return False

    # Vehicle found — run the full pipeline to build the initial result
    cameras = await client.get_all_cameras()
    result = await pipeline.run(
        plate=params.get("plate"),
        vehicle_type=params.get("vehicle_type"),
        colour=params.get("colour"),
        last_seen_at=params["last_seen_at"],
        last_known_location=params["last_known_location"],
        cameras=cameras,
    )

    if "error" in result:
        return False

    investigation_store.activate(inv.investigation_id, result)
    print(f"[InvestigationWatcher] Activated pending {inv.investigation_id[:8]} "
          f"— vehicle first seen at {result.get('last_known_camera')}")
    return True


async def run_watcher():
    """Background watcher loop — started at app startup via lifespan."""
    print("[InvestigationWatcher] Started")
    tick = 0
    while True:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        tick += 1
        try:
            # Refresh active investigations on every tick
            active = [inv for inv in investigation_store.list_all()
                      if not inv.closed and inv.status == "active" and inv.result]
            for inv in active:
                try:
                    if await _refresh_investigation(inv):
                        print(f"[InvestigationWatcher] Updated {inv.investigation_id[:8]}")
                except Exception as e:
                    print(f"[InvestigationWatcher] Error on {inv.investigation_id[:8]}: {e}")

            # Retry pending investigations on every outer tick (every POLL_INTERVAL_SECONDS).
            # PENDING_RETRY_SECONDS (10s) is the intended target interval but the outer
            # loop sleeps POLL_INTERVAL_SECONDS (15s), so retries actually fire every 15s.
            pending = investigation_store.list_pending()
            for inv in pending:
                try:
                    await _try_activate_pending(inv)
                except Exception as e:
                    print(f"[InvestigationWatcher] Pending error on {inv.investigation_id[:8]}: {e}")

        except Exception as e:
            print(f"[InvestigationWatcher] Loop error: {e}")
