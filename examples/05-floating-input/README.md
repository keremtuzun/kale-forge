# 05-floating-input

A digital input is left without a defined logic level after its pull resistor is removed, so it floats.

**Category:** floating_input · **Safety class:** general

**Intended finding:** `CONN-004`

**Rule findings that fire (8):** COMP-002, COMP-007, COMP-011, COMP-012, CONN-004, CONN-005

## Files
- `source/circuit.cir` — SPICE representation (provenance; full pin-type semantics are in `normalized.json`)
- `normalized.json` — Kale.ai normalized circuit (the internal contract)
- `expected_findings.json` — findings the rule engine is expected to produce
- `ideal_review.json` — the ideal Kale model response (AIReview schema)
- `labels.json` — evaluation labels
