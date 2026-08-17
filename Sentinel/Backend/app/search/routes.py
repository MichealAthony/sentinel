"""
app/search/routes.py

FastAPI search router. Mount in app/api/routes.py:

    from app.search.routes import search_router
    app.include_router(search_router)

ENDPOINTS
---------

SEARCH
  POST /search/vehicles
    One-shot vehicle search. Returns full result immediately.
    Used when the operator just wants a result without creating
    a persistent investigation.

INVESTIGATIONS
  POST   /search/investigations
    Create a new investigation. Runs the full search pipeline,
    stores the result, and returns the investigation with an ID
    the UI uses for polling and updates.

  GET    /search/investigations
    List all active investigations (summaries, not full results).
    Used by the InvestigationsPanel to populate the right sidebar.

  GET    /search/investigations/{id}
    Get the full current result for one investigation.
    Called when the operator selects an investigation to view.

  PUT    /search/investigations/{id}/refresh
    Re-run the search for an existing investigation and update its result.
    Called by the UI's Refresh button on an investigation card.

  GET    /search/investigations/{id}/poll
    Lightweight poll — does NOT re-run the search.
    Checks if new sightings exist since ?since=<unix_timestamp>.
    Returns { has_update, new_sighting_count, last_known_timestamp }.
    Called every N seconds by InvestigationsPanel to drive notifications.

  DELETE /search/investigations/{id}
    Close and remove an investigation.

MARKOV
  GET    /search/markov
    Current transition matrix diagnostics.

  POST   /search/markov/record
    Record a single camera transition. For testing.

  POST   /search/markov/seed
    Bulk seed the transition matrix from a topology definition.
    Accepts a list of { from_camera, to_camera, count } entries.
    Use this to prime the model with known road topology before
    real vehicle data accumulates. Important for cold-start quality.

HISTORY
  GET    /search/history
    List the last N search results (investigations), most recent first.
    Allows the UI to restore state on page refresh.
"""

import time
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from app.search import pipeline, client, visual_pipeline
from app.search.markov import transition_matrix
from app.search.investigations import investigation_store
from app.search.recoveries import recovery_store

search_router = APIRouter(prefix="/search", tags=["search"])


# =========================================================
# REQUEST MODELS
# =========================================================

class VehicleSearchRequest(BaseModel):
    plate:               Optional[str] = None
    vehicle_type:        Optional[str] = None
    colour:              Optional[str] = None
    last_seen_at:        str            # ISO 8601 datetime
    last_known_location: dict           # {"lat": float, "lng": float}
    label:               Optional[str] = None  # human label for the investigation
    priority_tier:       str = "standard"       # "standard" | "high"


class VisualSearchRequest(BaseModel):
    visual_description:  str
    priority_tier:       str = "high"
    plate:               Optional[str] = None
    vehicle_type:        Optional[str] = None
    colour:              Optional[str] = None
    last_seen_at:        str
    last_known_location: dict


class RecordTransitionRequest(BaseModel):
    from_camera: str
    to_camera:   str


class SeedEntry(BaseModel):
    from_camera: str
    to_camera:   str
    count:       int = 1  # how many times to record this transition


class SeedRequest(BaseModel):
    entries: list[SeedEntry]


class PatchInvestigationRequest(BaseModel):
    label:  Optional[str]  = None
    notes:  Optional[str]  = None
    pinned: Optional[bool] = None


class CreateRecoveryRequest(BaseModel):
    investigation_id: Optional[str] = None
    label:            str
    lat:              float
    lng:              float
    notes:            Optional[str] = ""
    plate:            Optional[str] = None
    vehicle_type:     Optional[str] = None
    colour_label:     Optional[str] = None
    reported_at:      Optional[float] = None


# =========================================================
# SHARED: RUN SEARCH
# =========================================================

async def _run_search(req: VehicleSearchRequest) -> dict:
    """
    Shared search execution used by both /vehicles and /investigations.
    Raises HTTPException on validation failure.
    Returns result dict on success.
    """
    if not req.plate and not req.vehicle_type and not req.colour:
        raise HTTPException(
            status_code=400,
            detail="At least one of plate, vehicle_type, or colour must be provided."
        )

    cameras = await client.get_all_cameras()

    result = await pipeline.run(
        plate=req.plate,
        vehicle_type=req.vehicle_type,
        colour=req.colour,
        last_seen_at=req.last_seen_at,
        last_known_location=req.last_known_location,
        cameras=cameras,
    )

    if "error" in result:
        raise HTTPException(status_code=404, detail=result["message"])

    return result


# =========================================================
# PRIORITY VISUAL SEARCH
# =========================================================

@search_router.post("/priority", summary="Visual feature search by natural language description")
async def visual_priority_search(req: VisualSearchRequest):
    """
    Search for vehicles matching a natural language visual description.
    Combines retrospective DB search over stored CLIP embeddings with
    live monitoring of incoming camera frames.

    Returns HTTP 501 until CLIPEmbeddingService is activated.
    Activation steps are documented in the 501 response body.
    """
    # ── ACTIVATION GUARD — remove this block to activate ──────────────
    from app.services.clip_embedding_service import clip_service
    if not clip_service.ready():
        raise HTTPException(status_code=501, detail={
            "error": "not_implemented",
            "message": (
                "Visual feature search requires CLIP. "
                "The endpoint contract is live — build your client against it now."
            ),
            "activation_steps": [
                "pip install git+https://github.com/openai/CLIP.git",
                "In app/api/routes.py: pass clip_service=clip_service to VehicleWorker",
                "In app/api/routes.py: pass query_registry=active_query_registry to VehicleWorker",
                "Remove this guard block",
            ],
        })
    # ──────────────────────────────────────────────────────────────────

    cameras = await client.get_all_cameras()
    inv = investigation_store.create(
        label=req.visual_description[:60],
        params=req.dict(),
        result=None,
    )

    result = await visual_pipeline.run_visual_search(
        visual_description=req.visual_description,
        priority_tier=req.priority_tier,
        plate=req.plate,
        vehicle_type=req.vehicle_type,
        colour=req.colour,
        last_seen_at=req.last_seen_at,
        last_known_location=req.last_known_location,
        investigation_id=inv.investigation_id,
        cameras=cameras,
    )

    investigation_store.update_result(inv.investigation_id, result)
    return {"investigation_id": inv.investigation_id, **result}


# =========================================================
# ONE-SHOT SEARCH
# =========================================================

@search_router.post("/vehicles", summary="One-shot vehicle search")
async def search_vehicles(req: VehicleSearchRequest):
    """
    Run the full search pipeline and return the result immediately.
    Does not create a persistent investigation.
    Use POST /search/investigations if you want to track updates over time.
    """
    return await _run_search(req)


# =========================================================
# INVESTIGATIONS — CREATE
# =========================================================

@search_router.post("/investigations", summary="Create a new investigation")
async def create_investigation(req: VehicleSearchRequest):
    """
    Run the search pipeline and store the result as a named investigation.

    If no vehicle is found yet (no_match), creates a pending investigation
    instead of returning 404. The watcher will retry periodically and
    populate the result when the vehicle first appears on the network.

    Returns the full investigation including:
    - investigation_id (used for all subsequent calls)
    - status ("active" or "pending")
    - label (human-readable name)
    - result (full search result, or null if pending)
    - created_at (unix timestamp)
    """
    label = req.label or _derive_label(req)

    try:
        result = await _run_search(req)
    except HTTPException as e:
        if e.status_code == 404:
            # Vehicle not in DB yet — create a pending investigation so the
            # watcher can populate it when the vehicle first appears.
            inv = investigation_store.create(
                label=label, params=req.dict(), result=None, status="pending"
            )
            print(f"[Search] No match — pending investigation created: {inv.investigation_id[:8]}")
            return {
                "investigation_id": inv.investigation_id,
                "label":            inv.label,
                "status":           "pending",
                "created_at":       inv.created_at,
                "message":          "No vehicle found yet. Investigation is watching for first sighting.",
            }
        raise

    inv = investigation_store.create(label=label, params=req.dict(), result=result)

    return {
        "investigation_id": inv.investigation_id,
        "label":            inv.label,
        "status":           "active",
        "created_at":       inv.created_at,
        **result,
    }


def _derive_label(req: VehicleSearchRequest) -> str:
    """Generate a readable label from search params when none is provided."""
    parts = []
    if req.plate:
        parts.append(req.plate)
    if req.colour:
        parts.append(req.colour.replace("_", " "))
    if req.vehicle_type:
        parts.append(req.vehicle_type)
    return " · ".join(parts) if parts else "Search"


# =========================================================
# INVESTIGATIONS — LIST
# =========================================================

@search_router.get("/investigations", summary="List all active investigations")
def list_investigations():
    """
    Return lightweight summaries of all active investigations.
    Used by InvestigationsPanel to populate the right sidebar.
    Does not include full sighting timelines — call GET /{id} for those.

    Return shape:
    [
      {
        investigation_id, label, plate, vehicle_type, colour_label,
        sighting_count, last_known_camera, last_known_timestamp,
        predicted_next (top 1), staleness_warning, has_update,
        created_at, updated_at
      }
    ]
    """
    return [inv.summary() for inv in investigation_store.list_all()]


# =========================================================
# INVESTIGATIONS — GET FULL RESULT
# =========================================================

@search_router.get("/investigations/{investigation_id}", summary="Get full investigation result")
def get_investigation(investigation_id: str):
    """
    Return the full current result for an investigation.
    Called when the operator clicks an investigation card to view it.
    Marks the investigation as read (clears has_update flag).
    """
    inv = investigation_store.get(investigation_id)
    if not inv:
        raise HTTPException(status_code=404, detail=f"Investigation {investigation_id} not found.")

    investigation_store.mark_checked(investigation_id)

    return {
        "investigation_id": inv.investigation_id,
        "label":            inv.label,
        "status":           inv.status,
        "notes":            inv.notes,
        "pinned":           inv.pinned,
        "created_at":       inv.created_at,
        "updated_at":       inv.updated_at,
        **(inv.result or {}),
    }


# =========================================================
# INVESTIGATIONS — PATCH METADATA
# =========================================================

@search_router.patch("/investigations/{investigation_id}", summary="Update investigation metadata")
def patch_investigation(investigation_id: str, req: PatchInvestigationRequest):
    """Update label, notes, or pinned flag without re-running the search."""
    if not investigation_store.update_meta(investigation_id, req.label, req.notes, req.pinned):
        raise HTTPException(status_code=404, detail=f"Investigation {investigation_id} not found.")
    return {"status": "updated", "investigation_id": investigation_id}


# =========================================================
# INVESTIGATIONS — REFRESH
# =========================================================

@search_router.put("/investigations/{investigation_id}/refresh", summary="Re-run search for an investigation")
async def refresh_investigation(investigation_id: str):
    """
    Re-run the full search pipeline for an existing investigation
    and update its stored result.

    Called by the UI Refresh button on an investigation card.
    Also called automatically when a poll detects new sightings and
    the operator wants the full updated result.

    Returns the updated full result.
    """
    inv = investigation_store.get(investigation_id)
    if not inv:
        raise HTTPException(status_code=404, detail=f"Investigation {investigation_id} not found.")

    params = inv.params
    req = VehicleSearchRequest(**params)
    result = await _run_search(req)

    investigation_store.update_result(investigation_id, result)
    investigation_store.mark_checked(investigation_id)

    return {
        "investigation_id": investigation_id,
        "label":            inv.label,
        "updated_at":       time.time(),
        **result,
    }


# =========================================================
# INVESTIGATIONS — POLL
# =========================================================

@search_router.get("/investigations/{investigation_id}/poll", summary="Lightweight poll for new sightings")
def poll_investigation(
    investigation_id: str,
    since: float = Query(..., description="Unix timestamp of last check"),
):
    """
    Lightweight poll — does NOT re-run the search.

    Compares last_known_timestamp in the stored result against `since`.
    If the trajectory has advanced, returns has_update=True so the UI
    can show a notification badge without fetching the full result.

    The UI calls this every N seconds per investigation. Only when
    has_update=True does it call GET /{id} to fetch the full update.

    Return shape:
    {
      "has_update": bool,
      "new_sighting_count": int,
      "last_known_timestamp": float | null
    }
    """
    inv = investigation_store.get(investigation_id)
    if not inv:
        raise HTTPException(status_code=404, detail=f"Investigation {investigation_id} not found.")

    return investigation_store.poll(investigation_id, since)


# =========================================================
# INVESTIGATIONS — CLOSE
# =========================================================

@search_router.delete("/investigations/{investigation_id}", summary="Close an investigation")
def close_investigation(investigation_id: str):
    """
    Remove an investigation from the active list.
    Persisted immediately — the investigation will not be restored on restart.
    """
    if not investigation_store.close(investigation_id):
        raise HTTPException(status_code=404, detail=f"Investigation {investigation_id} not found.")
    return {"status": "closed", "investigation_id": investigation_id}


# =========================================================
# SEARCH HISTORY
# =========================================================

@search_router.get("/history", summary="Recent search history")
def search_history(limit: int = Query(default=20, le=50)):
    """
    Return the most recent investigations in reverse chronological order.
    Allows the UI to restore investigation state on page refresh.
    Returns summaries only — call GET /investigations/{id} for full results.
    """
    all_invs = investigation_store.list_all()
    return [inv.summary() for inv in all_invs[:limit]]


# =========================================================
# MARKOV — STATUS
# =========================================================

@search_router.get("/markov", summary="Markov transition matrix diagnostics")
def markov_status():
    """
    Return current state of the transition matrix.
    Shows how many transitions have been observed and from which cameras.
    Use this to monitor how quickly the model is learning from real data.

    Return shape:
    {
      "cameras_with_outgoing_data": int,
      "total_observed_transitions": int,
      "file": str
    }
    """
    return transition_matrix.summary()


# =========================================================
# MARKOV — RECORD SINGLE TRANSITION
# =========================================================

@search_router.post("/markov/record", summary="Record a single camera transition")
def record_transition(req: RecordTransitionRequest):
    """
    Manually record a single camera-to-camera transition.
    Useful for testing and for manually correcting the matrix.
    For bulk seeding use POST /markov/seed.
    """
    if req.from_camera == req.to_camera:
        raise HTTPException(status_code=400, detail="from_camera and to_camera must be different.")
    transition_matrix.record(req.from_camera, req.to_camera)
    return {"status": "recorded", "from": req.from_camera, "to": req.to_camera}


# =========================================================
# MARKOV — BULK SEED
# =========================================================

@search_router.post("/markov/seed", summary="Bulk seed the transition matrix from road topology")
def seed_transitions(req: SeedRequest):
    """
    Pre-populate the Markov transition matrix from known road topology.

    This is critical for cold-start quality — without seeding, the model
    has no data until real vehicles accumulate enough sightings.
    Seeding with road topology ensures predictions are geographically
    sensible from day one.

    Example body:
    {
      "entries": [
        {"from_camera": "CAM_01", "to_camera": "CAM_02", "count": 10},
        {"from_camera": "CAM_01", "to_camera": "CAM_03", "count": 6},
        {"from_camera": "CAM_02", "to_camera": "CAM_04", "count": 8}
      ]
    }

    Count represents the relative likelihood — higher count means higher
    transition probability. Use road connectivity and typical traffic
    patterns to set reasonable counts.

    Note: seeded counts are combined with observed counts. As real vehicle
    data accumulates, observed patterns will increasingly dominate over
    the topology prior.
    """
    if not req.entries:
        raise HTTPException(status_code=400, detail="entries list cannot be empty.")

    total = 0
    errors = []
    for entry in req.entries:
        if entry.from_camera == entry.to_camera:
            errors.append(f"{entry.from_camera} -> {entry.to_camera}: self-transition skipped")
            continue
        if entry.count < 1:
            errors.append(f"{entry.from_camera} -> {entry.to_camera}: count must be >= 1")
            continue
        for _ in range(entry.count):
            transition_matrix.record(entry.from_camera, entry.to_camera)
        total += entry.count

    return {
        "status": "seeded",
        "transitions_recorded": total,
        "warnings": errors,
        "matrix_summary": transition_matrix.summary(),
    }


# =========================================================
# RECOVERIES
# =========================================================

@search_router.post("/recoveries", summary="Mark a vehicle recovery")
def create_recovery(req: CreateRecoveryRequest):
    """
    Record that a vehicle was physically located at (lat, lng).
    Stored in a persistent log independent of the investigation lifecycle —
    survives investigation closure and server restarts.
    """
    r = recovery_store.create(
        investigation_id=req.investigation_id,
        label=req.label,
        lat=req.lat,
        lng=req.lng,
        notes=req.notes or "",
        plate=req.plate,
        vehicle_type=req.vehicle_type,
        colour_label=req.colour_label,
        reported_at=req.reported_at,
    )
    return r.to_dict()


@search_router.get("/recoveries", summary="List all recoveries")
def list_recoveries():
    """Return all recorded recoveries, newest first."""
    return [r.to_dict() for r in recovery_store.list_all()]


@search_router.delete("/recoveries/{recovery_id}", summary="Delete a recovery record")
def delete_recovery(recovery_id: str):
    if not recovery_store.delete(recovery_id):
        raise HTTPException(status_code=404, detail=f"Recovery {recovery_id} not found.")
    return {"status": "deleted", "recovery_id": recovery_id}
