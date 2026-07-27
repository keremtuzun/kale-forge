"""Training pipeline tests: schema, jsonl validation, secret scan, dedup, split, synthetic gen, dry-run."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TRAINING_PKG = REPO_ROOT / "services" / "training"
sys.path.insert(0, str(TRAINING_PKG))

from kale_training.dedup import deduplicate  # noqa: E402
from kale_training.generate_synthetic import generate  # noqa: E402
from kale_training.jsonl import validate_file, write_jsonl  # noqa: E402
from kale_training.schema import ExampleInput, ExampleMetadata, ExampleOutput, TrainingExample  # noqa: E402
from kale_training.secret_scan import scan_text  # noqa: E402
from kale_training.split import split_dataset  # noqa: E402


def _ex(id_, instruction="Review this circuit.", safety="general"):
    return TrainingExample(
        instruction=instruction, input=ExampleInput(),
        output=ExampleOutput(summary="ok", confidence=0.5),
        metadata=ExampleMetadata(id=id_, safety_class=safety),
    )


def test_schema_roundtrip():
    ex = _ex("a1")
    dumped = ex.model_dump_json()
    assert TrainingExample.model_validate_json(dumped).metadata.id == "a1"


def test_jsonl_validation_catches_bad_rows(tmp_path):
    good = tmp_path / "good.jsonl"
    write_jsonl([_ex("a"), _ex("b")], good)
    assert validate_file(good) == []
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"instruction": "x"}\n{not json}\n')
    errors = validate_file(bad)
    assert len(errors) == 2


def test_secret_scan_catches_secrets():
    assert any(f["type"] == "aws_access_key" for f in scan_text("key AKIAIOSFODNN7EXAMPLE here"))
    assert any(f["type"] == "private_key_block"
               for f in scan_text("-----BEGIN RSA PRIVATE KEY-----\nMII..."))
    assert any(f["type"] == "email" for f in scan_text("contact john.doe@example.com"))


def test_secret_scan_clean_text():
    assert scan_text("This LED needs a current-limiting resistor on the +5V rail.") == []


def test_dedup():
    a, b = _ex("1"), _ex("2")  # same instruction+input -> duplicate
    deduped, removed = deduplicate([a, b])
    assert len(deduped) == 1 and removed == 1


def test_split_deterministic_and_stratified():
    examples = [_ex(f"g{i}", instruction=f"q{i}") for i in range(40)] + \
               [_ex(f"s{i}", instruction=f"safety{i}", safety="safety_critical") for i in range(20)]
    split_a = split_dataset(examples, seed=7)
    split_b = split_dataset(examples, seed=7)
    assert [e.metadata.id for e in split_a["train"]] == [e.metadata.id for e in split_b["train"]]
    total = sum(len(v) for v in split_a.values())
    assert total == 60
    # both strata represented across splits (not all in one bucket)
    assert any(e.metadata.safety_class == "safety_critical" for e in split_a["train"])


def test_generate_synthetic_produces_valid_rows():
    examples = generate(seed=42)
    assert len(examples) >= 10
    # all schema-valid
    for ex in examples:
        TrainingExample.model_validate_json(ex.model_dump_json())
    # includes clean, uncertainty (question-asking), and safety-critical refusal cases
    assert any(not e.output.confirmed_findings and "No rule-level issues" in e.output.summary
               for e in examples)
    assert any(e.output.questions_for_engineer for e in examples)
    assert any(e.metadata.safety_class == "safety_critical" for e in examples)
    # faulty LED example should carry the LED-resistor rule finding as ground truth
    led_faulty = [e for e in examples if any(f.get("rule_id") == "COMP-008"
                  for f in e.input.rule_findings)]
    assert led_faulty, "expected a synthetic example where the LED-resistor rule fires"


def test_train_sft_dry_run(tmp_path):
    # generate a mini dataset and run the dry-run (no torch)
    examples = generate(seed=1)
    ds_dir = tmp_path / "ds"
    write_jsonl(examples, ds_dir / "train.jsonl")
    write_jsonl(examples[:3], ds_dir / "val.jsonl")
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(
        f"base_model: Qwen/Qwen2.5-0.5B-Instruct\n"
        f"dataset_dir: {ds_dir}\ndataset_version: test\nseed: 1\nlr: 0.0002\nepochs: 1\n"
        f"per_device_batch: 1\ngrad_accum: 4\nmax_seq_len: 2048\n"
        f"lora:\n  r: 8\n  alpha: 16\n  dropout: 0.05\n  target_modules: [q_proj]\n"
        f"quantization:\n  enabled: false\n  bits: 16\n  type: none\n"
        f"tracking:\n  backend: none\n  uri: file:./mlruns\noutput_dir: models/adapters/test\n"
    )
    result = subprocess.run(
        [sys.executable, str(TRAINING_PKG / "scripts" / "train_sft.py"),
         "--config", str(cfg), "--dry-run"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[dry-run] OK" in result.stdout
