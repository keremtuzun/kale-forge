"""CLI: python -m kale_eval run --config rules-only
       python -m kale_eval compare <a.json> <b.json>"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from kale_eval.cases import load_cases
from kale_eval.configs import EvalConfig
from kale_eval.report import build_report, compare, to_markdown, write_report
from kale_eval.runner import run_config

BENCHMARKS_DIR = Path(__file__).resolve().parent.parent / "benchmarks"


def cmd_run(args) -> int:
    cfg = EvalConfig.named(args.config, inference_url=args.inference_url, model_version=args.model_version)
    cases = load_cases(args.benchmarks or BENCHMARKS_DIR)
    if not cases:
        print("no benchmark cases found")
        return 1
    acc, case_reports = run_config(cfg, cases)
    timestamp = args.timestamp or "manual-run"
    report = build_report(cfg, acc, case_reports, cases, timestamp)
    path = write_report(report, timestamp)
    print(to_markdown(report))
    print(f"\nwrote {path}")
    return 0


def cmd_compare(args) -> int:
    a = json.loads(Path(args.run_a).read_text())
    b = json.loads(Path(args.run_b).read_text())
    print(compare(a, b))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="kale_eval")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--config", required=True,
                     help="rules-only | base | finetuned | rules+finetuned")
    run.add_argument("--inference-url", default="http://localhost:8001")
    run.add_argument("--model-version", default=None)
    run.add_argument("--benchmarks", default=None)
    run.add_argument("--timestamp", default=None)
    run.set_defaults(func=cmd_run)
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("run_a")
    cmp_.add_argument("run_b")
    cmp_.set_defaults(func=cmd_compare)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
