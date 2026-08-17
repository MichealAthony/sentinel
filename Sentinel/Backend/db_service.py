"""
db_service.py

Sentinel DB ingestion service.
Receives VehicleIndex records from the pipeline's IndexPublisher and
writes them to Postgres. Replaces mock_db.py for production use.

Run alongside the main pipeline:
    cd backend
    uvicorn db_service:app --port 8001

Prerequisites:
    CREATE EXTENSION vector;   -- pgvector, required for embeddings table

Tables written: vehicles, sightings, embeddings

Write rules:
    vehicles  — upserted per vehicle_id. best_plate is only written when
                plate_confirmed=True, preserving the confirmed value across
                subsequent unconfirmed sightings.
    sightings — one row per detection event, always inserted (never merged).
    embeddings — upserted per (vehicle_id, embedding_type). The centroid
                 embedding from the pipeline's IdentityEngine is written on
                 every confirmed detection, so the stored vector stays current.
"""

import os
import json
import asyncpg
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://rohanbrown@localhost/sentinel"
)

_pool: asyncpg.Pool | None = None


# =========================================================
# STARTUP / SHUTDOWN
# =========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    print(f"[DBService] Pool open → {DATABASE_URL.split('@')[-1]}")
    yield
    if _pool:
        await _pool.close()
    print("[DBService] Pool closed")


app = FastAPI(title="Sentinel DB Service", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# ENDPOINTS
# =========================================================

@app.post("/indexes", status_code=201)
async def receive_index(request: Request):
    """
    Receive a VehicleIndex record from the pipeline and persist it.
    Called by IndexPublisher on every vehicle detection.
    """
    data = await request.json()
    try:
        await _write_record(data)
    except Exception as e:
        print(f"[DBService] Write failed for {data.get('vehicle_id', '?')}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "ok"}


@app.get("/")
def health():
    return {"status": "running", "pool": "open" if _pool else "closed"}


# =========================================================
# WRITE LOGIC
# =========================================================

async def _write_record(data: dict) -> None:
    vehicle_id = data["vehicle_id"]

    ts_unix = data.get("timestamp")
    ts = (datetime.fromtimestamp(ts_unix, tz=timezone.utc)
          if ts_unix else datetime.now(timezone.utc))

    camera_id       = data.get("camera_id", "")
    plate           = data.get("plate")
    plate_conf      = data.get("plate_confidence", 0.0)
    plate_confirmed = data.get("plate_confirmed", False)
    vehicle_type    = data.get("vehicle_type")
    colour_label    = data.get("colour_label")
    colour_rgb      = data.get("colour_rgb")            # list[int] | None → INTEGER[]
    colour_dist     = data.get("colour_distribution")   # list[dict] | None → JSONB
    bbox            = data.get("bbox")                  # list[int] | None → INTEGER[]
    image_path      = data.get("image_path")
    add_features    = data.get("additional_features", {})
    schema_ver      = data.get("schema_version", 1)
    embeddings      = data.get("embeddings", {})

    # Only propagate plate to the vehicles table when confirmed —
    # unconfirmed OCR reads are stored in sightings but don't overwrite
    # a previously confirmed best_plate.
    confirmed_plate = plate if plate_confirmed else None

    async with _pool.acquire() as conn:
        async with conn.transaction():

            # ── vehicles ────────────────────────────────────────────────
            # COALESCE on best_plate means: if this record has no confirmed
            # plate (confirmed_plate IS NULL), keep whatever was already there.
            await conn.execute(
                """
                INSERT INTO vehicles
                    (vehicle_id, first_seen, last_seen, last_camera_id,
                     best_plate, vehicle_type, colour_label, colour_rgb,
                     schema_version)
                VALUES ($1::uuid, $2, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (vehicle_id) DO UPDATE SET
                    last_seen      = EXCLUDED.last_seen,
                    last_camera_id = EXCLUDED.last_camera_id,
                    vehicle_type   = COALESCE(EXCLUDED.vehicle_type,  vehicles.vehicle_type),
                    colour_label   = COALESCE(EXCLUDED.colour_label,  vehicles.colour_label),
                    colour_rgb     = COALESCE(EXCLUDED.colour_rgb,    vehicles.colour_rgb),
                    schema_version = EXCLUDED.schema_version,
                    best_plate     = COALESCE(EXCLUDED.best_plate,    vehicles.best_plate)
                """,
                vehicle_id, ts, camera_id,
                confirmed_plate, vehicle_type, colour_label, colour_rgb,
                schema_ver,
            )

            # ── sightings ────────────────────────────────────────────────
            # Every detection is a new row — sightings are never merged.
            # colour_distribution and additional_features stored as JSONB.
            await conn.execute(
                """
                INSERT INTO sightings
                    (vehicle_id, camera_id, timestamp, bbox,
                     plate, plate_confidence, plate_confirmed,
                     colour_label, colour_rgb, colour_distribution,
                     image_path, additional_features, schema_version)
                VALUES
                    ($1::uuid, $2, $3, $4,
                     $5, $6, $7,
                     $8, $9, $10::jsonb,
                     $11, $12::jsonb, $13)
                """,
                vehicle_id, camera_id, ts, bbox,
                plate, plate_conf, plate_confirmed,
                colour_label, colour_rgb, json.dumps(colour_dist),
                image_path, json.dumps(add_features), schema_ver,
            )

            # ── embeddings ───────────────────────────────────────────────
            # One row per (vehicle_id, embedding_type). The pipeline sends
            # the current centroid embedding, so we always overwrite —
            # the latest centroid is the most accurate.
            # pgvector expects the vector as a bracketed float list string:
            # "[0.01,0.02,...]"
            for embedding_type, vector_list in embeddings.items():
                if not vector_list:
                    continue
                vec_str = "[" + ",".join(f"{float(x):.8f}" for x in vector_list) + "]"
                await conn.execute(
                    """
                    INSERT INTO embeddings
                        (vehicle_id, embedding_type, vector, updated_at)
                    VALUES ($1::uuid, $2, $3::vector, $4)
                    ON CONFLICT (vehicle_id, embedding_type) DO UPDATE SET
                        vector     = EXCLUDED.vector,
                        updated_at = EXCLUDED.updated_at
                    """,
                    vehicle_id, embedding_type, vec_str, datetime.now(timezone.utc),
                )
