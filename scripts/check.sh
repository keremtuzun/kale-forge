#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing .venv. Run ./scripts/setup.sh first." >&2
  exit 1
fi

.venv/bin/ruff check services/analysis services/inference services/training services/evaluation
.venv/bin/ruff format --check services/analysis services/inference services/training services/evaluation
npm run typecheck
npm run test:py
npm run test:web
npm run build:web
