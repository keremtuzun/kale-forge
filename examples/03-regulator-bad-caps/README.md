# 03-regulator-bad-caps

The voltage regulator is missing a required capacitor, which can cause instability or oscillation.

**Category:** regulator_io · **Safety class:** power

**Intended finding:** `COMP-011`

**Rule findings that fire (6):** COMP-002, COMP-011, COMP-012, PWR-003

## Files
- `source/circuit.cir` — SPICE representation (provenance; full pin-type semantics are in `normalized.json`)
- `normalized.json` — Kale.ai normalized circuit (the internal contract)
- `expected_findings.json` — findings the rule engine is expected to produce
- `ideal_review.json` — the ideal Kale model response (AIReview schema)
- `labels.json` — evaluation labels
