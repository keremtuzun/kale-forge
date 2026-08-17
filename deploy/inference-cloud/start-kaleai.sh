#!/usr/bin/env bash
# Analysis API + Next.js, for the recovered Kale.ai app. The original also started an
# in-container inference service; that job now belongs to the v18 container next door
# (INFERENCE_URL), so this only supervises the two processes that are actually local.
set -uo pipefail

mkdir -p /data/kale/storage /data/kale/logs

cleanup() { kill "${ANALYSIS_PID:-}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

( cd /app/services/analysis && exec /opt/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 ) &
ANALYSIS_PID=$!

for _ in $(seq 1 90); do
  curl --silent --fail http://127.0.0.1:8000/health >/dev/null && break
  kill -0 "$ANALYSIS_PID" 2>/dev/null || { echo "analysis API died on startup"; wait "$ANALYSIS_PID"; exit 1; }
  sleep 1
done
curl --silent --fail http://127.0.0.1:8000/health >/dev/null || { echo "analysis API never became healthy"; exit 1; }
echo "analysis API healthy on :8000"

cd /app
exec /app/node_modules/.bin/next start /app/apps/web -p "${PORT:-7860}"
