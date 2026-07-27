#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

python_bin="${PYTHON_BIN:-python3}"
"$python_bin" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("Kale.ai requires Python 3.11 or newer.")
PY

if [[ ! -x .venv/bin/python ]]; then
  "$python_bin" -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e "services/analysis[dev]"
npm install

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example. Review secrets before a non-local deployment."
fi

echo "Setup complete. Run: npm run dev:inference, npm run dev:analysis, npm run dev:web"
