#!/bin/bash
# Promote a freshly trained design adapter and score it against the incumbents.
#
# Two hard rules on this host, both learned the expensive way:
#
#   1. Never run MLX inference while MLX training is running. They contend for the same GPU
#      and both stall; it cost one session 16 minutes of a training run.
#   2. /private/tmp is not durable. It was wiped between sessions and took a whole training
#      run with it, so the adapter is copied back into the repo as soon as it exists — before
#      the eval, not after.
#
# Usage: scripts/finish-design-adapter.sh <version> [checkpoint]
#        scripts/finish-design-adapter.sh v10 0000800
set -euo pipefail

VERSION="${1:?usage: finish-design-adapter.sh <version> [checkpoint]}"
CKPT="${2:-}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="/private/tmp/kale-train-$VERSION"
OUT="$REPO/models/adapters/kale-design-qwen3-4b-$VERSION"

if pgrep -f "mlx_lm.lora" > /dev/null; then
  echo "training is still running — refusing to start inference (see rule 1)" >&2
  exit 1
fi

weights="$WORK/adapter/adapters.safetensors"
[ -n "$CKPT" ] && weights="$WORK/adapter/${CKPT}_adapters.safetensors"
[ -f "$weights" ] || { echo "no weights at $weights" >&2; exit 1; }

# Copy back first: the repo is the durable location, /private/tmp is not.
mkdir -p "$OUT" "/private/tmp/adapters/$VERSION"
cp "$WORK/adapter/adapter_config.json" "$OUT/"
cp "$weights" "$OUT/adapters.safetensors"
cp "$OUT"/* "/private/tmp/adapters/$VERSION/"
echo "promoted $(basename "$weights") → $OUT"

cd /private/tmp
/usr/local/bin/python3 eval_cad_adapter.py \
  --adapters /private/tmp/adapters/v7 /private/tmp/adapters/v9 "/private/tmp/adapters/$VERSION" \
  --repo /private/tmp/evalrepo \
  --out "/private/tmp/kale-design-$VERSION-cad.json"

cp "/private/tmp/kale-design-$VERSION-cad.json" "$REPO/models/evaluations/"
echo "scorecard → $REPO/models/evaluations/kale-design-$VERSION-cad.json"
