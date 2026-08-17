"""
main.py

Application entry point.

Usage (from the backend/ directory):
    python -m app.main

This starts the FastAPI server on port 8000.
The pipeline can be auto-started here or controlled via the UI
using POST /pipeline/start and POST /pipeline/stop.
"""

import os
import sys

os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "0")  # suppress FFmpeg HEVC decoder noise

# Ensure backend/ is on sys.path so all app.* imports resolve correctly
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Set AUTO_START=false in the environment to suppress auto-start on boot.
AUTO_START = os.getenv("AUTO_START", "true").lower() == "true"


def main():
    import uvicorn
    from app.api.routes import app, orchestrator

    if AUTO_START:
        print("[Main] Auto-starting pipeline...")
        orchestrator.start()

    print("[Main] API server starting at http://localhost:8000")
    print("[Main] Docs at http://localhost:8000/docs")

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()
