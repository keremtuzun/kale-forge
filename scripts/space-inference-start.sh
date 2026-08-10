#!/usr/bin/env bash
# Entrypoint for the free-tier inference Space: fetch the GGUF once, then serve.
set -euo pipefail

: "${KALE_GGUF_REPO:?set KALE_GGUF_REPO to the HF repo holding the quantised model}"
: "${KALE_GGUF_FILE:?set KALE_GGUF_FILE to the .gguf filename inside that repo}"

echo "fetching ${KALE_GGUF_REPO}/${KALE_GGUF_FILE} (cached in \$HF_HOME after the first boot)"
KALE_MODEL_PATH="$(python - <<'PY'
import os
from huggingface_hub import hf_hub_download

print(hf_hub_download(repo_id=os.environ["KALE_GGUF_REPO"],
                      filename=os.environ["KALE_GGUF_FILE"],
                      token=os.environ.get("HF_TOKEN") or None))
PY
)"
export KALE_MODEL_PATH
echo "model at ${KALE_MODEL_PATH}"

cd /app/services/inference
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-7860}"
