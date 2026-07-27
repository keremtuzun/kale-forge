# 07-rail-no-source

A power rail has loads but nothing feeding it (the regulator that would source it is absent), so the rail would be dead at power-up.

**Category:** power_tree_explanation · **Safety class:** power

**Intended finding:** `PWR-007`

**Rule findings that fire (2):** COMP-002, PWR-007

## Files
- `source/circuit.cir` — SPICE representation (provenance; full pin-type semantics are in `normalized.json`)
- `normalized.json` — Kale.ai normalized circuit (the internal contract)
- `expected_findings.json` — findings the rule engine is expected to produce
- `ideal_review.json` — the ideal Kale model response (AIReview schema)
- `labels.json` — evaluation labels
