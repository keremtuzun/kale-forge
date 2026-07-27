#!/bin/bash
# Promote the v9 adapter and score it against v7.
#
# Run only after training has stopped — MLX inference and MLX training contend for the same
# GPU, and running them together stalls both (it cost this session 16 minutes).
set -e
REPO="$HOME/Desktop/Kale"
WORK=/private/tmp/kale-train-v9
OUT="$REPO/models/adapters/kale-design-qwen3-4b-v9"

if pgrep -f "mlx_lm.lora" > /dev/null; then
  echo "training still running — refusing to start inference"; exit 1
fi

# Promote the iter-800 checkpoint as the adapter weights.
mkdir -p /private/tmp/adapters/v9
cp "$WORK/adapter/adapter_config.json" /private/tmp/adapters/v9/
cp "$WORK/adapter/0000800_adapters.safetensors" /private/tmp/adapters/v9/adapters.safetensors
echo "promoted iter-800 checkpoint"

cd /private/tmp
/usr/local/bin/python3 eval_cad_adapter.py \
  --adapters /private/tmp/adapters/v7 /private/tmp/adapters/v9 \
  --repo /private/tmp/evalrepo \
  --intent-prompts 6 --cad-prompts 6 --max-tokens 1100 \
  --out /private/tmp/kale-design-v9-cad.json

# Copy the adapter back into the repo only once the eval has produced a scorecard.
mkdir -p "$OUT"
cp /private/tmp/adapters/v9/* "$OUT/"
cp /private/tmp/kale-design-v9-cad.json "$REPO/models/evaluations/kale-design-v9-cad.json"
echo "adapter and scorecard copied into the repo"
