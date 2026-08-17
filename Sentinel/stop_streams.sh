#!/usr/bin/env bash
# Kills all ffmpeg stream processes and mediamtx started by start_streams.sh

PID_DIR="$(dirname "$0")/.stream_pids"

if [ ! -d "$PID_DIR" ]; then
  echo "[streams] No PID directory found — nothing to stop."
  exit 0
fi

for PID_FILE in "$PID_DIR"/*.pid; do
  [ -f "$PID_FILE" ] || continue
  PID=$(cat "$PID_FILE")
  NAME=$(basename "$PID_FILE" .pid)
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID" && echo "[streams] Stopped $NAME (pid $PID)"
  else
    echo "[streams] $NAME (pid $PID) already gone"
  fi
  rm -f "$PID_FILE"
done

rmdir "$PID_DIR" 2>/dev/null || true
echo "[streams] Done."
