"""
app/search/visual_pipeline.py

Visual feature search pipeline for the POST /search/priority endpoint.
Called exclusively by that endpoint — not part of any existing search path.
"""

import asyncio
from typing import Optional

from app.services.clip_embedding_service import clip_service
from app.services.active_query_registry import active_query_registry
from app.search.investigations import investigation_store
from app.search import client, pipeline


async def run_visual_search(
    visual_description: str,
    priority_tier: str,
    plate: Optional[str],
    vehicle_type: Optional[str],
    colour: Optional[str],
    last_seen_at: str,
    last_known_location: dict,
    investigation_id: str,
    cameras: dict[str, dict],
) -> dict:
    """
    Four-step visual search pipeline:
    1. Encode the text description via CLIP
    2. Retrospective DB query over stored CLIP embeddings (parallel with step 3)
    3. Standard attribute filter if any attributes are provided
    4. Register a live monitoring query on incoming frames
    """

    # Step 1 — encode text description
    if not clip_service.ready():
        return {
            "error": "clip_not_available",
            "message": (
                "CLIPEmbeddingService is not ready. "
                "Install the clip package and ensure the model has loaded."
            ),
            "activation_steps": [
                "pip install git+https://github.com/openai/CLIP.git",
                "In app/api/routes.py pass clip_service=clip_service to VehicleWorker",
                "In app/api/routes.py pass query_registry=active_query_registry to VehicleWorker",
                "Uncomment the activation block in POST /search/priority",
            ],
        }

    text_embedding = clip_service.encode_text(visual_description)

    # Steps 2 + 3 — retrospective DB query and optional attribute filter in parallel
    has_attributes = any([plate, vehicle_type, colour])

    retro_task = asyncio.create_task(
        client.visual_search_retrospective(
            text_embedding=text_embedding.tolist(),
            threshold=0.50,
        )
    )
    attr_task = (
        asyncio.create_task(
            pipeline.run(
                plate=plate,
                vehicle_type=vehicle_type,
                colour=colour,
                last_seen_at=last_seen_at,
                last_known_location=last_known_location,
                cameras=cameras,
            )
        )
        if has_attributes else None
    )

    # Collect retrospective results
    try:
        past_matches = await retro_task
        if past_matches is None:
            past_matches = []
    except NotImplementedError:
        past_matches = []
    except Exception as e:
        print(f"[VisualPipeline] Retrospective search failed: {e}")
        past_matches = []

    # Merge attribute results — deduplicate by vehicle_id, keep higher score
    if attr_task is not None:
        try:
            attribute_result = await attr_task
            if "error" not in attribute_result:
                seen_ids = {m["vehicle_id"] for m in past_matches}
                for sighting in attribute_result.get("sightings", []):
                    vid = sighting.get("vehicle_id")
                    if vid and vid not in seen_ids:
                        seen_ids.add(vid)
                        past_matches.append({
                            "vehicle_id": vid,
                            "camera_id": sighting.get("camera_id"),
                            "timestamp": sighting.get("timestamp"),
                            "score": 0.0,
                            "colour_label": sighting.get("colour_label"),
                            "vehicle_type": sighting.get("vehicle_type"),
                        })
            past_matches.sort(key=lambda m: m.get("score", 0.0), reverse=True)
        except Exception as e:
            print(f"[VisualPipeline] Attribute filter failed: {e}")

    # Step 4 — register live monitoring query (1-hour TTL)
    def on_match(vehicle_id, camera_id, score, timestamp):
        investigation_store.append_live_match(investigation_id, {
            "vehicle_id": vehicle_id,
            "camera_id": camera_id,
            "score": score,
            "timestamp": timestamp,
            "source": "live",
        })

    active_query_registry.register(
        query_id=investigation_id,
        text_embedding=text_embedding,
        on_match=on_match,
        threshold=0.50,
        ttl_seconds=3600,
    )

    return {
        "investigation_id": investigation_id,
        "visual_description": visual_description,
        "retrospective_matches": past_matches,
        "live_monitoring": True,
        "live_threshold": 0.50,
        "monitoring_expires_in_seconds": 3600,
        "staleness_warning": False,
    }
