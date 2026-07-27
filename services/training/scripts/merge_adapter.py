"""Merge a trained LoRA adapter into its base model and export merged weights, ready for
GGUF conversion (llama.cpp) or vLLM serving. Lazy imports; GPU host only.

Usage: python services/training/scripts/merge_adapter.py \
    --base Qwen/Qwen2.5-7B-Instruct --adapter models/adapters/lora-default --out models/checkpoints/kale-review-v1
"""
from __future__ import annotations

import argparse


def main() -> int:  # pragma: no cover - requires torch/transformers
    parser = argparse.ArgumentParser(description="Merge LoRA adapter into base model")
    parser.add_argument("--base", required=True)
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    print(f"loading base {args.base}")
    base = AutoModelForCausalLM.from_pretrained(args.base, torch_dtype=torch.bfloat16)
    print(f"applying adapter {args.adapter}")
    merged = PeftModel.from_pretrained(base, args.adapter).merge_and_unload()
    merged.save_pretrained(args.out)
    AutoTokenizer.from_pretrained(args.base).save_pretrained(args.out)
    print(f"merged model written to {args.out}")
    print("Next: convert to GGUF with llama.cpp's convert_hf_to_gguf.py, or serve with vLLM.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
