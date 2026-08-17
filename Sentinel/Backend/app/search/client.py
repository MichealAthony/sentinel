"""
app/search/client.py

Database interface for the search layer.
All queries use the shared asyncpg pool from app.core.database.

Timestamps:
    The pipeline works in Unix floats (seconds since epoch).
    The DB stores timestamps as TIMESTAMPTZ.
    All queries convert back to float via EXTRACT(EPOCH FROM ...).

Cameras:
    Camera metadata (location, priority, fps) lives in cameras.json —
    the pipeline is the source of truth. last_indexed is derived from
    the sightings table so it reflects actual processing activity.
"""

import json
from pathlib import Path
from typing import Optional

from app.core.database import get_pool

APP_DIR      = Path(__file__).parent.parent
CAMERAS_JSON = APP_DIR / "cameras.json"


# =========================================================
# PHASE 2 — METADATA FILTER
# =========================================================

async def search_vehicles(
    plate: Optional[str] = None,
    vehicle_type: Optional[str] = None,
    colour: Optional[str] = None,
) -> list[dict]:
    """
    Return vehicles matching the provided attributes, ordered by most recent.
    plate is matched case-insensitively against best_plate (confirmed only).

    Return shape:
    [{ vehicle_id, best_plate, plate_confirmed, vehicle_type,
       colour_label, colour_rgb, last_seen, last_camera_id }]
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                vehicle_id::text,
                best_plate,
                (best_plate IS NOT NULL)                      AS plate_confirmed,
                vehicle_type,
                colour_label,
                colour_rgb,
                EXTRACT(EPOCH FROM last_seen)::float          AS last_seen,
                last_camera_id
            FROM vehicles
            WHERE ($1::text IS NULL
                    OR (best_plate IS NOT NULL AND best_plate ILIKE $1 || '%'))
              AND ($2::text IS NULL OR vehicle_type = $2)
              AND ($3::text IS NULL OR colour_label = $3)
            ORDER BY last_seen DESC
            LIMIT 50
            """,
            plate, vehicle_type, colour,
        )
        return [dict(r) for r in rows]


# =========================================================
# PHASE 3 / 5 — SIGHTINGS
# =========================================================

async def get_sightings(vehicle_id: str) -> list[dict]:
    """
    All sightings for a vehicle ordered by timestamp ASC.

    Return shape:
    [{ camera_id, timestamp, plate_confidence, plate_confirmed,
       bbox, colour_label, image_path }]
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                camera_id,
                EXTRACT(EPOCH FROM timestamp)::float  AS timestamp,
                plate_confidence,
                plate_confirmed,
                bbox,
                colour_label,
                image_path
            FROM sightings
            WHERE vehicle_id = $1::uuid
            ORDER BY timestamp ASC
            """,
            vehicle_id,
        )
        return [dict(r) for r in rows]


# =========================================================
# PHASE 3 — EMBEDDING TIEBREAKER
# =========================================================

async def get_embedding(
    vehicle_id: str,
    embedding_type: str = "reid_v1",
) -> Optional[list[float]]:
    """
    Stored embedding vector for a vehicle. Returns None if absent.
    Return shape: list[float] (512 elements) or None.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT vector::text
            FROM embeddings
            WHERE vehicle_id = $1::uuid AND embedding_type = $2
            """,
            vehicle_id, embedding_type,
        )
    if row is None:
        return None
    # pgvector serialises as "[f1,f2,...]" — strip brackets and parse
    raw = row["vector"].strip()
    return [float(x) for x in raw[1:-1].split(",")]


# =========================================================
# PHASE 1 — CAMERA METADATA
# =========================================================

async def get_all_cameras() -> dict[str, dict]:
    """
    All cameras with location metadata, keyed by camera_id.
    Reads location/priority/fps from cameras.json (source of truth).
    last_indexed is derived from the sightings table.

    Return shape:
    { "CAM_01": { camera_id, label, lat, lng, base_priority,
                  source_fps, last_indexed } }
    """
    if not CAMERAS_JSON.exists():
        return {}

    with open(CAMERAS_JSON) as f:
        cameras_list = json.load(f)

    if not cameras_list:
        return {}

    camera_ids = [c["camera_id"] for c in cameras_list]

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                camera_id,
                EXTRACT(EPOCH FROM MAX(timestamp))::float AS last_indexed
            FROM sightings
            WHERE camera_id = ANY($1)
            GROUP BY camera_id
            """,
            camera_ids,
        )

    last_indexed = {r["camera_id"]: r["last_indexed"] for r in rows}

    return {
        cam["camera_id"]: {
            "camera_id":     cam["camera_id"],
            "label":         cam.get("location", {}).get("label", cam["camera_id"]),
            "lat":           cam.get("location", {}).get("lat"),
            "lng":           cam.get("location", {}).get("lng"),
            "base_priority": cam.get("base_priority", 5),
            "source_fps":    cam.get("source_fps", 30),
            "last_indexed":  last_indexed.get(cam["camera_id"]),
        }
        for cam in cameras_list
    }


# =========================================================
# PHASE 4 — FRESHNESS CHECK
# =========================================================

async def get_last_indexed(camera_id: str) -> Optional[float]:
    """
    Unix timestamp of most recent sighting for camera_id.
    Returns None if camera has never been indexed.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT EXTRACT(EPOCH FROM MAX(timestamp))::float AS last_indexed
            FROM sightings
            WHERE camera_id = $1
            """,
            camera_id,
        )
    return row["last_indexed"] if row else None


# =========================================================
# PHASE 6 — STOLEN VEHICLE CHECK
# =========================================================

async def get_stolen_report(
    vehicle_id: Optional[str] = None,
    plate: Optional[str] = None,
) -> Optional[dict]:
    """
    Cross-reference against stolen vehicle reports.
    Plate match (primary) takes precedence; vehicle_id is secondary.
    Returns None if no match.

    Return shape:
    { report_id, plate, reported_at, reported_by, vehicle_type, colour }
    or None
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                report_id::text,
                plate,
                EXTRACT(EPOCH FROM reported_at)::float AS reported_at,
                reported_by,
                vehicle_type,
                colour
            FROM stolen_vehicles
            WHERE ($1::text IS NOT NULL AND plate = $1)
               OR ($2::text IS NOT NULL AND vehicle_id = $2)
            ORDER BY
                CASE WHEN $1::text IS NOT NULL AND plate = $1 THEN 0 ELSE 1 END
            LIMIT 1
            """,
            plate, vehicle_id,
        )
    return dict(row) if row else None


# =========================================================
# MARKOV PERSISTENCE (optional DB backend)
# =========================================================

async def save_transition_counts(counts: dict[str, dict[str, int]]) -> None:
    """
    Persist Markov transition counts to DB.
    Not yet implemented — TransitionMatrix falls back to JSON file.
    """
    pass


async def load_transition_counts() -> dict[str, dict[str, int]]:
    """Load transition counts from DB. Returns {} — JSON file is used instead."""
    return {}


# =========================================================
# VISUAL SEARCH — CLIP EMBEDDING SIMILARITY
# =========================================================

async def visual_search_retrospective(
    text_embedding: list[float],
    threshold: float = 0.50,
) -> list[dict]:
    """
    Cosine similarity search over stored CLIP embeddings.

    Return shape:
    [{ vehicle_id, camera_id, timestamp, score, colour_label, vehicle_type }]
    ordered by score DESC.

    pgvector SQL (requires clip_embedding vector(512) column on sightings):
      SELECT vehicle_id, camera_id, timestamp, colour_label, vehicle_type,
             1 - (clip_embedding <=> $1::vector) AS score
      FROM sightings
      WHERE 1 - (clip_embedding <=> $1::vector) >= $2
      ORDER BY score DESC
      LIMIT 100

    Fallback without pgvector (correct but O(n)):
      Fetch all clip_v1 from embeddings table, compute cosine similarity
      in Python, filter by threshold, sort by score.

    DB schema requirement:
      ALTER TABLE sightings ADD COLUMN clip_embedding vector(512);
      CREATE INDEX ON sightings
        USING ivfflat (clip_embedding vector_cosine_ops) WITH (lists = 100);
    """
    raise NotImplementedError("visual_search_retrospective: DB team must implement")
