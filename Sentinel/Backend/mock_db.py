"""
mock_db.py

Temporary mock DB endpoint for verifying pipeline output.
Run this alongside the main pipeline to see exactly what
indexes are being produced from your test video.

Usage:
    cd "/Users/rohanbrown/Desktop/Courses/COMP3901/Sentinel copy/backend"
    uvicorn mock_db:app --port 8001

You will see each detection record printed as it arrives.
"""

import json
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Mock DB")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

record_count = 0


@app.post("/indexes")
async def receive_index(request: Request):
    global record_count
    data = await request.json()
    record_count += 1

    # Strip embeddings from print — 512 floats is unreadable
    display = {k: v for k, v in data.items() if k != "embeddings"}

    print(f"\n{'='*60}")
    print(f"  Record #{record_count}")
    print(f"{'='*60}")
    print(f"  vehicle_id    : {data.get('vehicle_id', 'N/A')}")
    print(f"  camera_id     : {data.get('camera_id', 'N/A')}")
    print(f"  timestamp     : {data.get('timestamp', 'N/A')}")
    print(f"  vehicle_type  : {data.get('vehicle_type', 'N/A')}")
    print(f"  colour_label  : {data.get('colour_label', 'N/A')}")
    print(f"  colour_rgb    : {data.get('colour_rgb', 'N/A')}")
    print(f"  plate         : {data.get('plate', 'N/A')}")
    print(f"  plate_conf    : {data.get('plate_confidence', 'N/A')}")
    print(f"  plate_confirm : {data.get('plate_confirmed', 'N/A')}")
    print(f"  bbox          : {data.get('bbox', 'N/A')}")
    print(f"  embeddings    : reid_v1={'present' if 'reid_v1' in (data.get('embeddings') or {}) else 'absent'}")
    print(f"  schema_version: {data.get('schema_version', 'N/A')}")
    print(f"  add_features  : {data.get('additional_features', {})}")
    print(f"{'='*60}")
    print(f"  Total received: {record_count}")

    return {"status": "ok", "record": record_count}


@app.get("/")
def status():
    return {"status": "mock_db running", "records_received": record_count}
