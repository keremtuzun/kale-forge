#!/usr/bin/env bash
# Start the full Kale.ai stack locally (no Docker, no GPU): inference :8001, analysis :8000,
# web :3000. Ctrl-C stops all three. Uses the stub inference provider by default — set
# KALE_INFERENCE_PROVIDER + KALE_MODEL_PATH in .env to serve a real open-weight model.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

# Load .env for the services, which read plain os.getenv. docker-compose consumes .env
# natively, but local dev never did — so the header's promise that .env selects the
# inference provider was silently false and the stack always ran the stub.
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ ! -x .venv/bin/uvicorn ]]; then
  echo "No virtualenv found. Run ./scripts/setup.sh first." >&2
  exit 1
fi

pids=()
cleanup() {
  echo
  echo "Stopping Kale.ai stack..."
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "Starting inference service on :8001 ..."
( cd services/inference && exec "$repo_root/.venv/bin/uvicorn" app.main:app --port 8001 ) &
pids+=($!)

echo "Starting analysis service on :8000 ..."
( cd services/analysis && exec "$repo_root/.venv/bin/uvicorn" app.main:app --port 8000 ) &
pids+=($!)

echo "Starting web app on :3000 ..."
( exec npm run dev:web ) &
pids+=($!)

echo "Kale.ai is starting. Open http://localhost:3000  (Ctrl-C to stop everything)"
wait
