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
OUT="$REPO/models/adapters/kale-design-qwen3-4b-$VERSION"

# Where the training run actually wrote its checkpoints. train-design-adapter.sh was changed to
# write into the repo (durable) rather than /private/tmp (wiped between sessions), and this
# script was not updated with it — so it looked for weights that had never been there and
# failed with "no weights at …", which reads exactly like a training failure rather than a
# path mismatch. Both locations are checked now, newest-first, so either layout resolves.
WORK_REPO="$REPO/models/adapters/kale-design-qwen3-4b-$VERSION-work"
WORK_TMP="/private/tmp/kale-train-$VERSION/adapter"

if pgrep -f "mlx_lm.lora" > /dev/null; then
  echo "training is still running — refusing to start inference (see rule 1)" >&2
  exit 1
fi

weights=""
for dir in "$WORK_REPO" "$WORK_TMP"; do
  candidate="$dir/adapters.safetensors"
  [ -n "$CKPT" ] && candidate="$dir/${CKPT}_adapters.safetensors"
  if [ -f "$candidate" ]; then weights="$candidate"; workdir="$dir"; break; fi
done
[ -n "$weights" ] || {
  echo "no ${CKPT:+checkpoint $CKPT }weights under $WORK_REPO or $WORK_TMP" >&2
  echo "checkpoints present:" >&2
  ls -1 "$WORK_REPO" 2>/dev/null | sed 's/^/  /' >&2 || true
  exit 1
}

# Copy back first: the repo is the durable location, /private/tmp is not.
mkdir -p "$OUT" "/private/tmp/adapters/$VERSION"
cp "$workdir/adapter_config.json" "$OUT/"
cp "$weights" "$OUT/adapters.safetensors"
cp "$OUT"/* "/private/tmp/adapters/$VERSION/"
echo "promoted $(basename "$weights") → $OUT"

# Score against the incumbent, not against whatever the last release happened to be. The
# baseline was pinned to v7/v9 and went stale the moment v10 shipped, which makes a scorecard
# that looks like a comparison and is not one. BASELINES can be overridden to widen it.
: "${BASELINES:=v10}"
baseline_args=()
for base in $BASELINES; do
  dir="$REPO/models/adapters/kale-design-qwen3-4b-$base"
  if [ -d "$dir" ]; then baseline_args+=("$dir"); else echo "skipping absent baseline $base" >&2; fi
done

/usr/local/bin/python3 "$REPO/services/evaluation/kale_eval/eval_cad_adapter.py" \
  --adapters ${baseline_args[@]+"${baseline_args[@]}"} "$OUT" \
  --repo "$REPO" \
  --out "$REPO/models/evaluations/kale-design-$VERSION-cad.json"

echo "scorecard → $REPO/models/evaluations/kale-design-$VERSION-cad.json"
