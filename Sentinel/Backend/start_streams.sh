#!/usr/bin/env bash
# Starts mediamtx (RTSP server) and 15 ffmpeg loops feeding it.
# Each camera gets its own path: rtsp://localhost:8554/cam_01 ... cam_15
#
# Video assignments:
#   test_video  → CAM_02 03 05 07 08 10 15
#   test_video2 → CAM_04 09 14
#   test_video3 → CAM_01 06 11 12 13
#
# Usage:  ./start_streams.sh
# Stop:   ./stop_streams.sh

set -e

BASE="/Users/rohanbrown/Desktop/Courses/COMP3901/Sentinel copy 2/backend/app"
VIDEO1="${BASE}/test_video.mov"
VIDEO2="${BASE}/test_video2.mov"
VIDEO3="${BASE}/test_video3.mov"
RTSP_BASE="rtsp://localhost:8554"
PID_DIR="$(dirname "$0")/.stream_pids"

for V in "$VIDEO1" "$VIDEO2" "$VIDEO3"; do
  [ -f "$V" ] || { echo "ERROR: video not found at $V"; exit 1; }
done

command -v mediamtx >/dev/null 2>&1 || { echo "ERROR: mediamtx not found. Run: brew install mediamtx"; exit 1; }
command -v ffmpeg   >/dev/null 2>&1 || { echo "ERROR: ffmpeg not found.    Run: brew install ffmpeg";    exit 1; }

mkdir -p "$PID_DIR"

# ── mediamtx ─────────────────────────────────────────────────────────────────
if pgrep -x mediamtx >/dev/null 2>&1; then
  echo "[streams] mediamtx already running"
else
  mediamtx "$(dirname "$0")/mediamtx.yml" >/tmp/mediamtx.log 2>&1 &
  echo $! > "$PID_DIR/mediamtx.pid"
  echo "[streams] mediamtx started (pid $!)"
  sleep 1
fi

# ── ffmpeg loops ──────────────────────────────────────────────────────────────
for i in $(seq -w 1 15); do
  CAM="cam_${i}"
  case "$CAM" in
    cam_02|cam_03|cam_05|cam_07|cam_08|cam_10|cam_15) VIDEO="$VIDEO1" ;;
    cam_04|cam_09|cam_14)                              VIDEO="$VIDEO2" ;;
    cam_01|cam_06|cam_11|cam_12|cam_13)                VIDEO="$VIDEO3" ;;
  esac
  LOG="/tmp/ffmpeg_${CAM}.log"

  ffmpeg -re -stream_loop -1 -i "$VIDEO" \
    -c:v copy -an -f rtsp -rtsp_transport tcp \
    "${RTSP_BASE}/${CAM}" \
    >"$LOG" 2>&1 &

  PID=$!
  echo $PID > "$PID_DIR/${CAM}.pid"
  echo "[streams] ${CAM} → ${RTSP_BASE}/${CAM}  ($(basename "$VIDEO")  pid $PID)"
done

echo ""
echo "All 15 streams live. Stop with: ./stop_streams.sh"
