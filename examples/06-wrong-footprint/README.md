# 06-wrong-footprint

An IC has no/incorrect footprint assigned. Detecting a true package↔footprint mismatch requires component-spec data; this example is detected via the missing-footprint rule.

**Category:** incorrect_footprint · **Safety class:** general

**Intended finding:** `COMP-002`

**Rule findings that fire (5):** COMP-002, COMP-011, COMP-012, CONN-005

## Files
- `source/circuit.cir` — SPICE representation (provenance; full pin-type semantics are in `normalized.json`)
- `normalized.json` — Kale.ai normalized circuit (the internal contract)
- `expected_findings.json` — findings the rule engine is expected to produce
- `ideal_review.json` — the ideal Kale model response (AIReview schema)
- `labels.json` — evaluation labels
