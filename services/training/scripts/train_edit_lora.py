"""CPU LoRA fine-tune on the design_edit corpus (transformers + peft, no trl needed).

Trains Qwen2.5-0.5B-Instruct — the largest base that trains in a few hours on this
laptop's CPU — on the exact runtime intent task with revisions, loss masked to the
assistant answer. Checkpoints regularly so a partial run is still a usable adapter.

  python services/training/scripts/train_edit_lora.py \
      --data datasets/processed/design-edit-v1 \
      --out models/adapters/kale-edit-qwen05b-v1 [--max-rows N] [--epochs 1]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="datasets/processed/design-edit-v1")
    ap.add_argument("--out", default="models/adapters/kale-edit-qwen05b-v1")
    ap.add_argument("--base", default="Qwen/Qwen2.5-0.5B-Instruct")
    ap.add_argument("--max-rows", type=int, default=0)
    ap.add_argument("--epochs", type=float, default=1.0)
    ap.add_argument("--max-seq", type=int, default=1792)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--grad-accum", type=int, default=8)
    args = ap.parse_args()

    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import (AutoModelForCausalLM, AutoTokenizer, Trainer,
                              TrainingArguments)

    data_dir = REPO_ROOT / args.data
    out_dir = REPO_ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    def read(path: Path) -> list[dict]:
        rows = []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    rows.append(json.loads(line))
        return rows

    train_rows = read(data_dir / "train.jsonl")
    val_rows = read(data_dir / "val.jsonl")
    if args.max_rows:
        train_rows = train_rows[: args.max_rows]
    print(f"train={len(train_rows)} val={len(val_rows)} base={args.base}", flush=True)

    tokenizer = AutoTokenizer.from_pretrained(args.base)
    model = AutoModelForCausalLM.from_pretrained(args.base, dtype=torch.float32)
    model.config.use_cache = False

    lora = LoraConfig(r=8, lora_alpha=16, lora_dropout=0.05, task_type="CAUSAL_LM",
                      target_modules=["q_proj", "k_proj", "v_proj", "o_proj"])
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    def encode(row: dict) -> dict:
        messages = row["messages"]
        prompt_ids = tokenizer.apply_chat_template(messages[:-1], tokenize=True,
                                                   add_generation_prompt=True)
        if hasattr(prompt_ids, "input_ids"):        # transformers 5.x returns BatchEncoding
            prompt_ids = prompt_ids["input_ids"]
        prompt_ids = list(prompt_ids)
        answer_ids = tokenizer(messages[-1]["content"] + tokenizer.eos_token,
                               add_special_tokens=False)["input_ids"]
        input_ids = (prompt_ids + answer_ids)[: args.max_seq]
        labels = ([-100] * len(prompt_ids) + answer_ids)[: args.max_seq]
        return {"input_ids": input_ids, "labels": labels}

    encoded_train = [encode(r) for r in train_rows]
    encoded_val = [encode(r) for r in val_rows[:60]]
    lengths = sorted(len(e["input_ids"]) for e in encoded_train)
    print(f"seq p50={lengths[len(lengths)//2]} p95={lengths[int(len(lengths)*0.95)]} "
          f"max={lengths[-1]}", flush=True)

    class Collator:
        def __call__(self, features):
            width = max(len(f["input_ids"]) for f in features)
            pad = tokenizer.pad_token_id or tokenizer.eos_token_id
            batch_ids, batch_labels, batch_mask = [], [], []
            for f in features:
                n = width - len(f["input_ids"])
                batch_ids.append(f["input_ids"] + [pad] * n)
                batch_labels.append(f["labels"] + [-100] * n)
                batch_mask.append([1] * len(f["input_ids"]) + [0] * n)
            return {"input_ids": torch.tensor(batch_ids),
                    "labels": torch.tensor(batch_labels),
                    "attention_mask": torch.tensor(batch_mask)}

    steps_per_epoch = max(1, len(encoded_train) // args.grad_accum)
    train_args = TrainingArguments(
        output_dir=str(out_dir), num_train_epochs=args.epochs,
        per_device_train_batch_size=1, gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr, lr_scheduler_type="cosine", warmup_ratio=0.05,
        logging_steps=5, save_steps=max(20, steps_per_epoch // 5), save_total_limit=3,
        eval_strategy="no", report_to=[], seed=42, use_cpu=True,
        dataloader_num_workers=0,
    )
    trainer = Trainer(model=model, args=train_args, train_dataset=encoded_train,
                      data_collator=Collator())
    start = time.time()
    trainer.train()
    model.save_pretrained(str(out_dir))
    tokenizer.save_pretrained(str(out_dir))
    (out_dir / "TRAINING_NOTE.json").write_text(json.dumps({
        "base": args.base, "rows": len(encoded_train), "epochs": args.epochs,
        "minutes": round((time.time() - start) / 60, 1),
        "task": "design_edit intent (runtime contract, revision precision)"},
        indent=2), encoding="utf-8")
    print("saved adapter to", out_dir, flush=True)


if __name__ == "__main__":
    main()
