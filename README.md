# Sentinel

Sentinel is a multi-camera vehicle tracking and search system. It ingests live video from registered cameras, detects vehicles and license plates, links sightings into per-vehicle identity trails across cameras, and predicts likely next locations using a Markov transition model. Investigators use the web UI to search for a vehicle, view its route on a map, and monitor live camera feeds.

## Architecture

- **Backend** (`backend/`) — FastAPI service that runs the detection/ingestion pipeline and exposes a REST API.
- **Frontend** (`frontend/`) — React + Vite single-page app for search, pipeline control, camera management, and live streams.

```
backend/app/
├── main.py                  # entry point (uvicorn server, auto-starts pipeline)
├── pipeline_orchestrator.py # wires registry/scheduler/worker/publisher together
├── api/routes.py            # FastAPI app + pipeline/camera/stream endpoints
├── ingestion/                # video stream reading + frame sampling
├── services/                 # detection, OCR, colour mapping, embeddings,
│                              # identity engine, vehicle index, camera scheduler,
│                              # index publisher, stream buffer
├── search/                   # search API, Markov prediction, confidence scoring,
│                              # investigations, recoveries, watcher
├── models/ & schemas/         # DB models + pydantic schemas
└── utils/                    # geo + markov helpers

frontend/src/
├── App.jsx
├── components/                # SearchPanel, ResultsPanel, PipelinePanel,
│                              # CamerasPanel, StreamsPanel, MapView, TopBar, ...
├── hooks/usePipelineStatus.js
└── api/pipeline.js
```

## Requirements

- Python 3.11
- Node.js (for the frontend)
- PostgreSQL

## Setup

### Backend

```bash
cd backend
pip install -r app/requirements.txt
```

Configure the database via the `DATABASE_URL` environment variable (defaults to `postgresql://<user>@localhost/sentinel`, see `app/core/config.py`). Optional environment variables:

- `AUTO_START` — auto-start the pipeline on boot (default `true`)
- `DB_ENDPOINT` — index publisher endpoint (default `http://localhost:8001/indexes`)
- `TIMEZONE_OFFSET_HOURS` — deployment UTC offset (default `-5`, Jamaica)

Run the server:

```bash
python -m app.main
```

The API starts at `http://localhost:8000` (docs at `/docs`).

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Opens at `http://localhost:3000`. The Vite dev server proxies `/api` requests to the backend at `http://localhost:8000`, which must be running first.

## API Overview

**Pipeline control**
- `POST /pipeline/start` / `POST /pipeline/stop`
- `GET /pipeline/status` — running state + scheduler status
- `POST /pipeline/boost` — boost camera priority (search → ingestion)
- `GET /pipeline/publisher` — index queue depth + DB health

**Cameras**
- `GET /cameras`, `POST /cameras`, `DELETE /cameras/{camera_id}`

**Streams**
- `GET /stream/{camera_id}` — MJPEG stream

**Search** (`/search` prefix)
- `POST /search/vehicles`
- `POST /search/investigations`, `GET /search/investigations`, `GET /search/investigations/{id}`, `PUT /search/investigations/{id}/refresh`, `GET /search/investigations/{id}/poll`, `DELETE /search/investigations/{id}`
- `GET /search/markov`, `POST /search/markov/record`, `POST /search/markov/seed`
- `GET /search/history`

## Frontend Tabs

- **Search** — query vehicles by plate, type, colour, last-seen time, and location; results show as a sighting timeline plus a numbered route on the map (confirmed sightings in red, inferred gaps in grey, predicted next cameras in amber).
- **Pipeline** — start/stop the pipeline, view per-camera scheduler state, send boost signals.
- **Cameras** — register/remove cameras at runtime, set coordinates via a map picker.
- **Streams** — live MJPEG feed per camera.

## Testing

```bash
cd backend
pytest
```

Tests cover confidence scoring, the identity engine, Markov prediction, pipeline utilities, and the camera scheduler (`backend/tests/`).
