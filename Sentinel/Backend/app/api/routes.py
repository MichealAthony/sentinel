"""
app/api/routes.py

FastAPI application with all pipeline interfaces.
All imports use app.* prefix to match the project structure.

Endpoints:

PIPELINE CONTROL (UI)
  POST  /pipeline/start           — start the processing pipeline
  POST  /pipeline/stop            — stop the processing pipeline
  GET   /pipeline/status          — running state + scheduler status

CAMERA REGISTRY (UI)
  GET    /cameras                 — list all registered cameras
  POST   /cameras                 — register a new camera at runtime
  DELETE /cameras/{camera_id}     — deregister a camera

VIDEO STREAMS (UI)
  GET   /stream/{camera_id}       — MJPEG stream, embed as <img src="...">

BOOST INTERFACE (Search layer → Ingestion, unidirectional)
  POST  /pipeline/boost           — boost priority of cameras of interest

PUBLISHER STATUS
  GET   /pipeline/publisher       — index queue depth + DB endpoint health
"""

import os
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services.camera_registry import CameraRegistry
from app.services.camera_scheduler import CameraScheduler
from app.services.vehicle_worker import VehicleWorker
from app.services.index_publisher import IndexPublisher
from app.services.stream_buffer import StreamBuffer
from app.pipeline_orchestrator import PipelineOrchestrator
from app.search.routes import search_router
from app.search.watcher import run_watcher

# =========================================================
# PATHS
# =========================================================
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CAMERAS_CONFIG  = os.path.join(APP_DIR, "cameras.json")
LPR_MODEL_PATH  = os.path.join(APP_DIR, "models", "best.pt")
YOLO_MODEL_PATH = os.path.join(APP_DIR, "models", "yolov8n.pt")

# =========================================================
# SHARED SINGLETONS
# Instantiated once here, injected into orchestrator and worker.
# =========================================================
registry     = CameraRegistry(config_path=CAMERAS_CONFIG)
scheduler    = CameraScheduler(registry)
stream_buffer = StreamBuffer()
publisher    = IndexPublisher(
    db_endpoint=os.getenv("DB_ENDPOINT", "http://localhost:8001/indexes")
)

worker = VehicleWorker(
    lpr_model_path=LPR_MODEL_PATH,
    vehicle_model_path=YOLO_MODEL_PATH,
    publisher=publisher,
    stream_buffer=stream_buffer
)

# ── CLIP VISUAL SEARCH ACTIVATION ────────────────────────────────────────────
# To activate CLIP visual feature search, replace the worker instantiation above with:
#
#   from app.services.clip_embedding_service import clip_service
#   from app.services.active_query_registry import active_query_registry
#
#   worker = VehicleWorker(
#       lpr_model_path=LPR_MODEL_PATH,
#       vehicle_model_path=YOLO_MODEL_PATH,
#       publisher=publisher,
#       stream_buffer=stream_buffer,
#       clip_service=clip_service,           # ← add this
#       query_registry=active_query_registry, # ← add this
#   )
#
# No other changes required. The CLIP service loads lazily on first use.
# ─────────────────────────────────────────────────────────────────────────────

orchestrator = PipelineOrchestrator(
    registry=registry,
    scheduler=scheduler,
    worker=worker,
    publisher=publisher,
    stream_buffer=stream_buffer
)


# =========================================================
# APP
# =========================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    watcher_task = asyncio.create_task(run_watcher())
    yield
    watcher_task.cancel()
    if orchestrator.is_running():
        orchestrator.stop()


app = FastAPI(
    title="Sentinel Pipeline API",
    description="Vehicle ingestion pipeline — control, camera management, and stream interfaces.",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search_router)


# =========================================================
# REQUEST MODELS
# =========================================================
class RegisterCameraRequest(BaseModel):
    camera_id: str
    stream_url: str
    base_priority: int = 5
    location: Optional[dict] = None
    # Source FPS of this camera. Used to calculate buffer size.
    # Set to actual camera FPS. Defaults to 30fps if unknown.
    source_fps: float = 30.0


class BoostRequest(BaseModel):
    camera_ids: list[str]     # cameras of interest from search layer
    boost_amount: int = 20    # how much to add on top of base priority
    ttl_seconds: float = 300  # boost duration in seconds (default 5 min)


# =========================================================
# PIPELINE CONTROL
# =========================================================
@app.post("/pipeline/start")
def start_pipeline():
    if orchestrator.is_running():
        return {"status": "already_running"}
    orchestrator.start()
    return {"status": "started"}


@app.post("/pipeline/stop")
def stop_pipeline():
    if not orchestrator.is_running():
        return {"status": "already_stopped"}
    orchestrator.stop()
    return {"status": "stopped"}


@app.get("/pipeline/status")
def pipeline_status():
    return {
        "running": orchestrator.is_running(),
        "cameras": scheduler.status(),
        "publisher": publisher.status(),
        "active_streams": stream_buffer.active_cameras()
    }


# =========================================================
# CAMERA REGISTRY
# =========================================================
@app.get("/cameras")
def list_cameras():
    return [cam.to_dict() for cam in registry.all()]


@app.post("/cameras")
def register_camera(req: RegisterCameraRequest):
    """
    Register a new camera at runtime without restarting the pipeline.
    Accepts RTSP stream URLs and local video file paths interchangeably.
    Persisted to cameras.json immediately — survives restarts.
    """
    try:
        cam = registry.register(
            camera_id=req.camera_id,
            stream_url=req.stream_url,
            base_priority=req.base_priority,
            location=req.location,
            source_fps=req.source_fps
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    if orchestrator.is_running():
        orchestrator.add_camera(cam)

    return {"status": "registered", "camera": cam.to_dict()}


@app.delete("/cameras/{camera_id}")
def deregister_camera(camera_id: str):
    """
    Remove a camera from the live pipeline at runtime.
    Persisted immediately — not restored on restart.
    """
    try:
        registry.deregister(camera_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    if orchestrator.is_running():
        orchestrator.remove_camera(camera_id)

    return {"status": "deregistered", "camera_id": camera_id}


# =========================================================
# VIDEO STREAMS (MJPEG)
# =========================================================
@app.get("/stream/{camera_id}")
async def stream_camera(camera_id: str):
    """
    MJPEG stream for a single camera.
    Embed directly in the UI with:
        <img src="http://localhost:8000/stream/CAM_01" />

    Serves processed frames with bounding boxes drawn.
    Serves the last available frame if the pipeline is paused —
    never blocks or errors while the pipeline is running.
    """
    if registry.get(camera_id) is None:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")

    async def generate():
        while True:
            frame_bytes = stream_buffer.read(camera_id)
            if frame_bytes:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                    + frame_bytes +
                    b"\r\n"
                )
            await asyncio.sleep(0.04)   # ~25fps cap, does not affect pipeline rate

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


# =========================================================
# BOOST INTERFACE (Search layer → Pipeline, unidirectional)
# =========================================================
@app.post("/pipeline/boost")
def boost_cameras(req: BoostRequest):
    """
    Called by the search layer when it identifies cameras with stale indexes.
    Cameras return to their BASE priority after TTL — not to equal.
    Chokepoint cameras retain their structural base priority permanently.

    This is the only interface between the search layer and the pipeline.
    The pipeline never sends signals back.
    """
    unknown = [cid for cid in req.camera_ids if registry.get(cid) is None]
    if unknown:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown camera_ids: {unknown}"
        )

    scheduler.boost_many(
        camera_ids=req.camera_ids,
        boost_amount=req.boost_amount,
        ttl_seconds=req.ttl_seconds
    )

    return {
        "status": "boosted",
        "camera_ids": req.camera_ids,
        "boost_amount": req.boost_amount,
        "ttl_seconds": req.ttl_seconds
    }


# =========================================================
# PUBLISHER STATUS
# =========================================================
@app.get("/pipeline/publisher")
def publisher_status():
    return publisher.status()


@app.get("/pipeline/diagnostics")
def stream_diagnostics():
    """
    Buffer health per camera.
    Shows buffer utilisation, frames dropped, and sampling parameters.
    A non-zero frames_dropped means the scheduler round trip is exceeding
    MAX_GAP_SECONDS and frames with investigative value may be lost.
    """
    return orchestrator.stream_diagnostics()