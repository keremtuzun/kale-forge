#!/usr/bin/env bash
set -euo pipefail

mkdir -p /data/kale/storage /data/kale/logs

cleanup() {
  kill "${INFERENCE_PID:-}" "${ANALYSIS_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

(
  cd /app/services/inference
  exec /opt/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001
) &
INFERENCE_PID=$!

for _ in $(seq 1 180); do
  if curl --silent --fail http://127.0.0.1:8001/health >/dev/null; then
    break
  fi
  if ! kill -0 "$INFERENCE_PID" 2>/dev/null; then
    wait "$INFERENCE_PID"
  fi
  sleep 1
done
curl --silent --fail http://127.0.0.1:8001/health >/dev/null

(
  cd /app/services/analysis
  exec /opt/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
) &
ANALYSIS_PID=$!

for _ in $(seq 1 60); do
  if curl --silent --fail http://127.0.0.1:8000/health >/dev/null; then
    break
  fi
  if ! kill -0 "$ANALYSIS_PID" 2>/dev/null; then
    wait "$ANALYSIS_PID"
  fi
  sleep 1
done
curl --silent --fail http://127.0.0.1:8000/health >/dev/null

cd /app
/app/node_modules/.bin/next start /app/apps/web -p "${PORT:-7860}"
