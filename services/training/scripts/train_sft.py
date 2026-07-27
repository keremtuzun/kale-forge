"""Supervised fine-tuning entrypoint (LoRA / QLoRA) for the Kale review model.

--dry-run validates the config + dataset WITHOUT importing torch, so it runs anywhere (CI).
The real training path lazily imports transformers/peft/trl and is meant for a GPU host.

Usage:
  python services/training/scripts/train_sft.py --config training/configs/lora-default.yaml --dry-run
  python services/training/scripts/train_sft.py --config training/configs/lora-default.yaml
  python services/training/scripts/train_sft.py --config ... --resume-from models/adapters/lora-default/checkpoint-500
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
TRAINING_PKG = REPO_ROOT / "services" / "training"
for p in (str(TRAINING_PKG),):
    if p not in sys.path:
        sys.path.insert(0, p)

from kale_training.jsonl import read_jsonl, validate_file  # noqa: E402

SYSTEM_PROMPT = (
    "You are Kale.ai's specialized hardware design-review model. Review the circuit and "
    "respond with a single JSON object: summary, confirmed_findings, possible_findings, "
    "recommendations, questions_for_engineer, evidence, confidence, limitations. Cite only "
    "components and nets present in the input. Never claim safety or standards compliance."
)


def load_config(path: str) -> dict:
    with open(path) as fh:
        return yaml.safe_load(fh)


def _resolve_dataset_dir(cfg: dict) -> Path:
    ds = cfg.get("dataset_dir", "datasets/processed/latest")
    path = REPO_ROOT / ds if not Path(ds).is_absolute() else Path(ds)
    if path.name == "latest" and not path.exists():
        parent = path.parent
        candidates = sorted([d for d in parent.glob("*") if d.is_dir()], reverse=True) if parent.exists() else []
        if candidates:
            return candidates[0]
    return path


def _find_split_files(dataset_dir: Path) -> dict[str, Path]:
    out = {}
    for split in ("train", "val", "test"):
        for candidate in (dataset_dir / f"{split}.jsonl", dataset_dir / split / "data.jsonl"):
            if candidate.exists():
                out[split] = candidate
                break
    # fall back to a single dataset.jsonl / synthetic.jsonl as 'train'
    if "train" not in out:
        for name in ("dataset.jsonl", "synthetic.jsonl"):
            if (dataset_dir / name).exists():
                out["train"] = dataset_dir / name
                break
    return out


def dry_run(cfg: dict) -> int:
    print(f"[dry-run] base_model = {cfg['base_model']}")
    problems = 0
    for key in ("base_model", "dataset_dir", "seed", "lr", "epochs", "lora", "output_dir"):
        if key not in cfg:
            print(f"  ! missing config key: {key}")
            problems += 1
    dataset_dir = _resolve_dataset_dir(cfg)
    print(f"[dry-run] dataset_dir = {dataset_dir}")
    if not dataset_dir.exists():
        print("  ! dataset directory does not exist")
        return 1
    splits = _find_split_files(dataset_dir)
    if "train" not in splits:
        print("  ! no train split found (expected train.jsonl / dataset.jsonl / synthetic.jsonl)")
        return 1
    for split, path in splits.items():
        errors = validate_file(path)
        rows = sum(1 for _ in read_jsonl(path))
        # whitespace token-length approximation
        lengths = []
        for ex in read_jsonl(path):
            text = SYSTEM_PROMPT + ex.instruction + json.dumps(ex.input.model_dump(), default=str) \
                + json.dumps(ex.output.model_dump(), default=str)
            lengths.append(len(text.split()))
        avg = sum(lengths) / len(lengths) if lengths else 0
        max_len = max(lengths) if lengths else 0
        status = "OK" if not errors else f"{len(errors)} INVALID ROWS"
        print(f"  {split}: {rows} rows [{status}], ~{avg:.0f} avg tokens, ~{max_len} max "
              f"(config max_seq_len={cfg.get('max_seq_len')})")
        if errors:
            for e in errors[:3]:
                print(f"      {e}")
            problems += 1
        if max_len > cfg.get("max_seq_len", 4096):
            print(f"      note: some examples exceed max_seq_len and would be truncated")
    q = cfg.get("quantization", {})
    print(f"[dry-run] quantization: enabled={q.get('enabled')} bits={q.get('bits')} type={q.get('type')}")
    print(f"[dry-run] lora: {cfg['lora']}")
    print(f"[dry-run] tracking: {cfg.get('tracking')}")
    print("[dry-run] OK" if problems == 0 else f"[dry-run] {problems} problem(s) found")
    return 0 if problems == 0 else 1


def train(cfg: dict, resume_from: str | None) -> int:  # pragma: no cover - GPU path
    """Real training path. Lazily imports heavy libraries so --dry-run stays lightweight."""
    import torch  # noqa: PLC0415
    from datasets import Dataset  # noqa: PLC0415
    from peft import LoraConfig  # noqa: PLC0415
    from transformers import (  # noqa: PLC0415
        AutoModelForCausalLM,
        AutoTokenizer,
        set_seed,
    )
    from trl import SFTConfig, SFTTrainer  # noqa: PLC0415

    set_seed(cfg["seed"])
    dataset_dir = _resolve_dataset_dir(cfg)
    splits = _find_split_files(dataset_dir)

    def to_chat(ex):
        return {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(ex.input.model_dump(), default=str)},
                {"role": "assistant", "content": json.dumps(ex.output.model_dump(), default=str)},
            ]
        }

    train_rows = [to_chat(ex) for ex in read_jsonl(splits["train"])]
    eval_rows = [to_chat(ex) for ex in read_jsonl(splits["val"])] if "val" in splits else None
    train_ds = Dataset.from_list(train_rows)
    eval_ds = Dataset.from_list(eval_rows) if eval_rows else None

    tokenizer = AutoTokenizer.from_pretrained(cfg["base_model"])
    quant = cfg.get("quantization", {})
    model_kwargs = {"torch_dtype": torch.bfloat16}
    if quant.get("enabled"):
        from transformers import BitsAndBytesConfig  # noqa: PLC0415

        model_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=quant.get("bits") == 4,
            bnb_4bit_quant_type=quant.get("type", "nf4"),
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=quant.get("double_quant", True),
        )
    model = AutoModelForCausalLM.from_pretrained(cfg["base_model"], **model_kwargs)

    peft_config = LoraConfig(
        r=cfg["lora"]["r"], lora_alpha=cfg["lora"]["alpha"], lora_dropout=cfg["lora"]["dropout"],
        target_modules=cfg["lora"]["target_modules"], task_type="CAUSAL_LM",
    )

    output_dir = str(REPO_ROOT / cfg["output_dir"])
    sft_config = SFTConfig(
        output_dir=output_dir, num_train_epochs=cfg["epochs"],
        per_device_train_batch_size=cfg["per_device_batch"],
        gradient_accumulation_steps=cfg["grad_accum"], learning_rate=cfg["lr"],
        lr_scheduler_type=cfg.get("lr_scheduler", "linear"),
        warmup_ratio=cfg.get("warmup_ratio", 0.0), max_seq_length=cfg["max_seq_len"],
        packing=cfg.get("packing", False), seed=cfg["seed"],
        eval_strategy="epoch" if (eval_ds and cfg.get("eval_per_epoch")) else "no",
        save_strategy="epoch", save_total_limit=cfg.get("save_total_limit", 3),
        report_to=[] if cfg.get("tracking", {}).get("backend") in (None, "none") else ["mlflow"],
    )
    _setup_tracking(cfg)
    trainer = SFTTrainer(model=model, args=sft_config, train_dataset=train_ds,
                         eval_dataset=eval_ds, peft_config=peft_config, processing_class=tokenizer)
    trainer.train(resume_from_checkpoint=resume_from)
    trainer.save_model(output_dir)
    print(f"saved adapter to {output_dir}")
    return 0


def _setup_tracking(cfg: dict) -> None:  # pragma: no cover
    import os

    tracking = cfg.get("tracking", {})
    if tracking.get("backend") == "mlflow":
        os.environ.setdefault("MLFLOW_TRACKING_URI", tracking.get("uri", "file:./mlruns"))
    elif tracking.get("backend") == "wandb":
        os.environ.setdefault("WANDB_PROJECT", "kale-review-model")


def main() -> int:
    parser = argparse.ArgumentParser(description="Kale SFT (LoRA/QLoRA)")
    parser.add_argument("--config", required=True)
    parser.add_argument("--dataset-version", default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume-from", default=None)
    args = parser.parse_args()
    cfg = load_config(args.config)
    if args.dataset_version:
        cfg["dataset_version"] = args.dataset_version
    if args.seed is not None:
        cfg["seed"] = args.seed
    if args.dry_run:
        return dry_run(cfg)
    return train(cfg, args.resume_from)


if __name__ == "__main__":
    raise SystemExit(main())
