#!/usr/bin/env bash
# Merge the v18 LoRA into its base and quantise the result to a GGUF the free CPU tier can run.
#
# Why GGUF rather than the transformers path in docs/serving-v18-free.md: that path needs a GPU
# and burns metered hours, so the model is only up while you are paying attention. A quantised
# GGUF runs on a free always-on CPU box, which is the difference between "v18 is live" and
# "v18 is live when I remember to start it".
#
# Run this once on any machine with torch and ~20 GB free (a Lightning CPU studio is enough —
# merging is not a GPU operation). The output is a single ~2.5 GB file.
#
#   bash scripts/build-v18-gguf.sh
#
# Then upload build/gguf/kale-design-v18-q4_k_m.gguf to a HF model repo and point the Space at
# it (see docs/serving-v18-free.md).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADAPTER="${ADAPTER:-$ROOT/models/adapters/kale-design-qwen3-4b-v18-cad-repair-50}"
BASE="${BASE:-Qwen/Qwen3-4B-Instruct-2507}"
OUT="${OUT:-$ROOT/build/gguf}"
MERGED="$OUT/merged"
QUANT="${QUANT:-Q4_K_M}"
LLAMA_CPP="${LLAMA_CPP:-$OUT/llama.cpp}"

mkdir -p "$OUT"

if [ ! -d "$ADAPTER" ]; then
  echo "adapter not found: $ADAPTER" >&2
  exit 1
fi

echo "==> 1/4  installing merge dependencies"
python -m pip install --quiet --upgrade "torch" "transformers>=4.44" "peft>=0.11" "safetensors" "sentencepiece"

echo "==> 2/4  merging $ADAPTER into $BASE"
# merge_and_unload folds the LoRA into the base weights, so the result needs no PEFT at
# serving time and converts like any ordinary checkpoint.
python - "$BASE" "$ADAPTER" "$MERGED" <<'PY'
import sys
import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

base, adapter, out = sys.argv[1], sys.argv[2], sys.argv[3]
print(f"loading base {base} (cpu, bf16)")
model = AutoModelForCausalLM.from_pretrained(base, torch_dtype=torch.bfloat16,
                                             device_map="cpu", low_cpu_mem_usage=True)
print(f"applying adapter {adapter}")
model = PeftModel.from_pretrained(model, adapter)
model = model.merge_and_unload()
print(f"writing {out}")
model.save_pretrained(out, safe_serialization=True)
# The adapter directory carries the tokenizer the model was fine-tuned with; prefer it over the
# base tokenizer so the chat template that shaped training is the one that ships.
try:
    tokenizer = AutoTokenizer.from_pretrained(adapter)
except Exception:
    tokenizer = AutoTokenizer.from_pretrained(base)
tokenizer.save_pretrained(out)
print("merged")
PY

echo "==> 3/4  fetching llama.cpp converter"
if [ ! -d "$LLAMA_CPP" ]; then
  git clone --depth 1 https://github.com/ggml-org/llama.cpp "$LLAMA_CPP"
fi
python -m pip install --quiet -r "$LLAMA_CPP/requirements/requirements-convert_hf_to_gguf.txt"

echo "==> 4/4  converting and quantising to $QUANT"
python "$LLAMA_CPP/convert_hf_to_gguf.py" "$MERGED" \
  --outfile "$OUT/kale-design-v18-f16.gguf" --outtype f16

# llama-quantize ships prebuilt in llama.cpp releases; build it only if it is not on PATH.
if command -v llama-quantize >/dev/null 2>&1; then
  QUANTISER="llama-quantize"
else
  cmake -S "$LLAMA_CPP" -B "$LLAMA_CPP/build" -DLLAMA_CURL=OFF >/dev/null
  cmake --build "$LLAMA_CPP/build" --target llama-quantize -j >/dev/null
  QUANTISER="$LLAMA_CPP/build/bin/llama-quantize"
fi
"$QUANTISER" "$OUT/kale-design-v18-f16.gguf" "$OUT/kale-design-v18-q4_k_m.gguf" "$QUANT"

rm -f "$OUT/kale-design-v18-f16.gguf"
echo
echo "done: $OUT/kale-design-v18-q4_k_m.gguf"
ls -la "$OUT/kale-design-v18-q4_k_m.gguf"
echo
echo "verify it before uploading:"
echo "  KALE_INFERENCE_PROVIDER=local_llamacpp \\"
echo "  KALE_MODEL_PATH=$OUT/kale-design-v18-q4_k_m.gguf \\"
echo "  KALE_MODEL_VERSION=kale-design-qwen3-4b-v18-cad-repair-50 \\"
echo "  python -m uvicorn app.main:app --port 8001   # from services/inference"
echo "  curl localhost:8001/health   # provider must read local_llamacpp, not stub"
