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
  # A resume restores the adapter WEIGHTS but not the Adam optimizer state, and that is not a
  # detail — it wrecked a run. v14 resumed from a healthy checkpoint (val 0.454 measured at
  # resume) and the loss went to 9.878 within 100 iterations, because Adam restarts with zero
  # moments and its bias correction makes the first steps near-maximal at full learning rate.
  # Converged weights do not survive that.
  #
  # So a resumed run re-warms: it starts at a fraction of the configured rate and ramps back
  # over 60 iterations, which is what the optimizer state would have been doing anyway.
  # Without this, resuming is worse than losing the run.
  resume=(--resume-adapter-file "$newest" --iters "$remaining")
  REWARM=1
  echo "re-warming the learning rate over 60 iterations (Adam state is not restored)"
else
  echo "starting $VERSION from scratch"
fi

sed -e "s#^data: .*#data: $WORK/data#" -e "s#^adapter_path: .*#adapter_path: $OUT#" \
    -e "s#^save_every: .*#save_every: 100#" "$CONFIG" > "$WORK/config.yaml"

# On a resume, replace whatever schedule the config carries with a re-warming one over the
# REMAINING iterations. Done in Python rather than sed because lr_schedule is a nested block and
# dropping it correctly with a line editor is how you end up with a config that silently means
# something else.
if [ "${REWARM:-0}" = "1" ]; then
  /usr/local/bin/python3 - "$WORK/config.yaml" "$remaining" <<'PY'
import re, sys
path, remaining = sys.argv[1], int(sys.argv[2])
text = open(path).read()
lr = float(re.search(r'^learning_rate:\s*([0-9.eE+-]+)', text, re.M).group(1))
# Drop an existing lr_schedule block: its key line plus the indented lines under it.
text = re.sub(r'^lr_schedule:\n(?:[ \t]+.*\n)*', '', text, flags=re.M)
text = text.rstrip('\n') + f"""
lr_schedule:
  name: cosine_decay
  arguments: [{lr:.8f}, {remaining}, {lr/10:.8f}]
  warmup: 60
  warmup_init: {lr/50:.8f}
"""
open(path, 'w').write(text)
print(f"resume schedule: warmup 60 → {lr:g} → {lr/10:g} over {remaining} iters")
PY
fi

# macOS ships bash 3.2, where expanding an empty array under `set -u` is an error, so both
# arrays need the ${x[@]+"${x[@]}"} guard rather than a bare "${x[@]}".
exec /usr/local/bin/python3 -m mlx_lm.lora --config "$WORK/config.yaml" \
     ${resume[@]+"${resume[@]}"} ${@+"$@"}
