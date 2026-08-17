# Sentinel UI

React + Vite frontend for the Sentinel vehicle tracking pipeline.

## Setup

```bash
cd sentinel-ui
npm install
npm run dev
```

Opens at http://localhost:3000

The Vite dev server proxies all `/api` requests to the pipeline at
`http://localhost:8000` — so the pipeline must be running before
you search or control it from the UI.

## Tabs

**Search** — query vehicles by plate, type, colour, last seen time,
and last known location (map picker). Results appear as a sighting
timeline in the sidebar and a numbered route on the map.

**Pipeline** — start/stop the pipeline, view scheduler state per camera,
send boost signals to specific cameras with custom amount and TTL.

**Cameras** — register and remove cameras at runtime with full config
including source FPS and coordinates. Map picker sets lat/lng.

**Streams** — live MJPEG feed per camera, embeddable as a plain img tag.

## Map

Uses Leaflet.js with OpenStreetMap — no API key required.

Route rendering:
- Confirmed sightings: red numbered circles, solid red lines, directional arrows
- Inferred gaps: grey circles, dashed grey lines
- Predicted next cameras: amber dashed rings with probability percentage
- Camera pins: blue squares at registered coordinates
- Last known location: red dot (set via map picker)

## Connecting the search layer

The search endpoint is called at POST /api/search/vehicles with body:
  { plate, vehicle_type, colour, last_seen, location }

The response shape expected is defined in ResultsPanel.jsx.
Until the search team's endpoint is live, the UI shows a placeholder message.

## Notes

- All pipeline API calls go through the Vite proxy at /api -> localhost:8000
- MJPEG streams go directly to localhost:8000/stream/{camera_id}
- Pipeline status is polled every 5 seconds automatically
