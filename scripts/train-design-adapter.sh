#!/bin/bash
# Train a design adapter so progress survives the session that started it.
#
# Background training does not outlive the Claude Code process, and /private/tmp is wiped
# between sessions. A v10 run was lost three times to that combination, twice before any
# checkpoint existed. So:
#
#   * checkpoints are written straight into the repo, which is durable, not into /private/tmp;
#   * save_every is small, so a kill costs one interval rather than the whole run;
#   * relaunching resumes from the newest checkpoint instead of starting over, and prints the
#     iteration it resumed at so a partial run is never mistaken for a fresh one.
#
# The dataset still lives on local disk: it is read repeatedly and regenerates in seconds.
#
# Usage: scripts/train-design-adapter.sh v10 [extra mlx_lm.lora args...]
set -euo pipefail

VERSION="${1:?usage: train-design-adapter.sh <version> [extra args]}"; shift || true
REPO="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="$REPO/training/configs/mlx-design-4b-$VERSION.yaml"
DATASET="$(awk '/^data:/ {print $2}' "$CONFIG")"
OUT="$REPO/models/adapters/kale-design-qwen3-4b-$VERSION-work"
WORK="/private/tmp/kale-train-$VERSION"

[ -f "$CONFIG" ] || { echo "no config at $CONFIG" >&2; exit 1; }
if pgrep -f "mlx_lm.lora" > /dev/null; then
  echo "a training run is already going; stop it first" >&2; exit 1
fi

mkdir -p "$OUT" "$WORK"
[ -d "$WORK/data" ] || cp -R "$REPO/$DATASET" "$WORK/data"

# Resume from the newest checkpoint this version has ever reached, wherever it got to.
resume=()
newest="$(ls -t "$OUT"/*_adapters.safetensors 2>/dev/null | head -1 || true)"
if [ -n "$newest" ]; then
  done_iters="$(basename "$newest" | sed 's/_adapters.safetensors//' | sed 's/^0*//')"
  total="$(awk '/^iters:/ {print $2}' "$CONFIG")"
  remaining=$(( total - done_iters ))
  if [ "$remaining" -le 0 ]; then
    echo "$VERSION already reached $done_iters/$total iterations — nothing to do"; exit 0
  fi
  echo "resuming from iteration $done_iters; $remaining to go"
  resume=(--resume-adapter-file "$newest" --iters "$remaining")
else
  echo "starting $VERSION from scratch"
fi

sed -e "s#^data: .*#data: $WORK/data#" -e "s#^adapter_path: .*#adapter_path: $OUT#" \
    -e "s#^save_every: .*#save_every: 100#" "$CONFIG" > "$WORK/config.yaml"

# macOS ships bash 3.2, where expanding an empty array under `set -u` is an error, so both
# arrays need the ${x[@]+"${x[@]}"} guard rather than a bare "${x[@]}".
exec /usr/local/bin/python3 -m mlx_lm.lora --config "$WORK/config.yaml" \
     ${resume[@]+"${resume[@]}"} ${@+"$@"}
