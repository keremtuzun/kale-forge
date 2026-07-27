# Kale Forge — Evaluation Plan

Generic LLM benchmarks say nothing about PCB review. Kale Forge is evaluated on a domain suite
that answers one question: **does fine-tuning measurably improve hardware design review over
(a) the base model and (b) deterministic rules alone?**

## Benchmark categories (`services/evaluation/benchmarks/`)

Each category is a set of labeled cases: normalized project + question + expected findings +
required evidence + forbidden claims.

| Category | Ground truth from |
|---|---|
| missing-decoupling-capacitor detection | examples + synthetic mutations |
| LED current-limiting-resistor detection | examples + synthetic |
| relay flyback-diode detection | examples + synthetic |
| regulator input/output validation | examples + synthetic |
| floating-input detection | examples + synthetic |
| incorrect-footprint identification | examples + labeled footprint pairs |
| power-tree explanation | expert-written reference explanations |
| circuit-purpose explanation | expert-written references |
| hallucination resistance | cases with deliberately missing data; any invented ref/net/spec counts against |
| uncertainty reporting | under-specified cases; ideal behavior asks questions, lowers confidence |
| evidence citation | every claim must cite existing refs/nets/rules |
| structured-output validity | all cases; response must parse against the AIReview schema |
| safety-critical refusal | mains/medical/automotive cases; must refuse certification, recommend professional review |

## Metrics (`services/evaluation/`)

Per category and overall:

- **Detection**: precision, recall, F1, false-positive rate, false-negative rate
  (finding-level matching on rule-id or labeled issue-id)
- **Output discipline**: JSON validity rate (first-shot and after one repair),
  citation accuracy (cited entities exist and are relevant),
  unsupported-claim rate (claims with no evidence or nonexistent entities)
- **Calibration**: confidence calibration (Brier score over correct/incorrect findings; reliability buckets)
- **Ops**: response latency p50/p95, tokens per response

## Compared configurations

1. `rules-only` — deterministic engine, no model (baseline floor; by construction 100% precision on rule-covered cases)
2. `base` — untuned base model + same prompts/retrieval
3. `finetuned` — Kale LoRA adapter
4. `rules+finetuned` — production configuration

A fine-tuned model is **promoted** only if, vs. the incumbent: F1 ≥ +2 points on detection
categories, unsupported-claim rate does not increase, JSON validity ≥ 99%, and
safety-refusal pass rate is 100%.

## Mechanics

- `python -m kale_eval run --config <cfg>` executes a configuration against all benchmarks,
  writes `EvaluationRun`/`EvaluationResult` rows and a JSON report under `models/evaluations/`.
- Runs are seeded and record model version, adapter version, dataset version, prompt version.
- The web app's evaluation dashboard renders per-category metric tables and version-to-version
  comparisons from these rows.
- After every fine-tuning checkpoint, a reduced smoke suite runs automatically; full suite on
  candidate checkpoints.
- Evaluation failures (misses, hallucinations) are triaged into new training examples —
  the failure-case flywheel.
