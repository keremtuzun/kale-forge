#!/usr/bin/env bash
# Merge the v18 design LoRA into its base and produce a quantised GGUF the Oracle VM can serve.
#
# Why this shape: the deployed image already installs llama-cpp-python and nothing else
# (Dockerfile:31-34), so a merged q4 GGUF is the only way to serve a Kale design adapter there
# without adding torch, transformers and peft to a container that also runs the web app.
#
# Run this on a machine with the base weights, ~16 GB RAM and ideally a GPU — the Lightning box,
# not the Oracle VM. The VM only ever sees the finished .gguf.
#
# Usage:  ./scripts/build-design-gguf.sh [output-dir]
#
# Requires: torch, transformers, peft  (pip install -e "services/analysis[transformers]")
#           a llama.cpp checkout built with llama-quantize  (LLAMA_CPP=/path/to/llama.cpp)
set -euo pipefail

ADAPTER="${ADAPTER:-models/adapters/kale-design-qwen3-4b-v18-cad-repair-50}"
BASE="${BASE:-Qwen/Qwen3-4B-Instruct-2507}"
LLAMA_CPP="${LLAMA_CPP:-$HOME/llama.cpp}"
QUANT="${QUANT:-Q4_K_M}"
OUT_DIR="${1:-models/checkpoints}"
STEM="kale-design-qwen3-4b-v18-cad-repair-50"
MERGED="${MERGED:-$(mktemp -d)/merged}"

[ -d "$ADAPTER" ] || { echo "adapter not found: $ADAPTER" >&2; exit 1; }
[ -f "$LLAMA_CPP/convert_hf_to_gguf.py" ] || {
  echo "llama.cpp not found at $LLAMA_CPP (set LLAMA_CPP=/path/to/llama.cpp)" >&2
  echo "  git clone https://github.com/ggml-org/llama.cpp && cmake -B build && cmake --build build -j" >&2
  exit 1
}
mkdir -p "$OUT_DIR"

echo "==> 1/4  Merging $ADAPTER into $BASE"
# The tokenizer is taken from the adapter, not the base: it carries the chat template the
# adapter was trained against, and a GGUF built with a different template would be prompted
# differently at inference than it was in training.
ADAPTER="$ADAPTER" BASE="$BASE" MERGED="$MERGED" python - <<'PY'
import os
import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

adapter, base, merged = os.environ["ADAPTER"], os.environ["BASE"], os.environ["MERGED"]
model = AutoModelForCausalLM.from_pretrained(base, torch_dtype=torch.bfloat16, device_map="cpu")
model = PeftModel.from_pretrained(model, adapter).merge_and_unload()
model.save_pretrained(merged, safe_serialization=True)

tokenizer = AutoTokenizer.from_pretrained(adapter)
if tokenizer.chat_template is None:  # older peft saves it beside the adapter instead
    template = os.path.join(adapter, "chat_template.jinja")
    if os.path.exists(template):
        with open(template, encoding="utf-8") as handle:
            tokenizer.chat_template = handle.read()
assert tokenizer.chat_template, "refusing to build a GGUF with no chat template"
tokenizer.save_pretrained(merged)
print(f"merged -> {merged}")
PY

echo "==> 2/4  Converting to GGUF (f16)"
python "$LLAMA_CPP/convert_hf_to_gguf.py" "$MERGED" \
  --outfile "$OUT_DIR/$STEM.f16.gguf" --outtype f16

echo "==> 3/4  Quantising to $QUANT"
"$LLAMA_CPP/build/bin/llama-quantize" \
  "$OUT_DIR/$STEM.f16.gguf" "$OUT_DIR/$STEM.${QUANT,,}.gguf" "$QUANT"

echo "==> 4/4  Checking the build"
GGUF="$OUT_DIR/$STEM.${QUANT,,}.gguf"
ls -lh "$GGUF"
# The chat template has to survive into the GGUF metadata or llama.cpp will format prompts
# with its own default and the adapter's training format is lost.
if "$LLAMA_CPP/build/bin/llama-gguf" "$GGUF" 2>/dev/null | grep -qi "chat_template"; then
  echo "chat template: embedded"
else
  echo "WARNING: no chat_template in GGUF metadata — verify before serving" >&2
fi
# Structured output is the only thing production asks of this model, so prove it emits JSON
# under a grammar before anyone ships it.
"$LLAMA_CPP/build/bin/llama-cli" -m "$GGUF" -no-cnv -n 96 --temp 0.55 \
  -p $'<|im_start|>system\nReply with one JSON object.<|im_end|>\n<|im_start|>user\nDesign a 27x27 swerve robot with an intake.<|im_end|>\n<|im_start|>assistant\n' \
  2>/dev/null | tail -5

rm -f "$OUT_DIR/$STEM.f16.gguf"
cat <<EOF

Built: $GGUF

Ship it:
  scp "$GGUF" ubuntu@150.136.151.230:~/kale/models/checkpoints/
  # then on the VM, in ~/kale/.env.oracle:
  #   KALE_INFERENCE_PROVIDER=local_llamacpp
  #   KALE_MODEL_PATH=/app/models/checkpoints/$(basename "$GGUF")
  #   KALE_MODEL_VERSION=$STEM
  ./scripts/deploy-oracle.sh

Check free RAM on the VM first — this is ~2.5 GB resident against the 1.5B model's ~1 GB, in a
container that also runs Next.js and the analysis service.
EOF
