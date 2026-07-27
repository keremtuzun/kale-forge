# 02-mcu-no-decoupling

The microcontroller has a supply pin with no local decoupling capacitor to ground, risking brownout resets and switching noise.

**Category:** missing_decoupling · **Safety class:** general

**Intended finding:** `COMP-012`

**Rule findings that fire (8):** COMP-002, COMP-011, COMP-012, CONN-005, PWR-003

## Files
- `source/circuit.cir` — SPICE representation (provenance; full pin-type semantics are in `normalized.json`)
- `normalized.json` — Kale.ai normalized circuit (the internal contract)
- `expected_findings.json` — findings the rule engine is expected to produce
- `ideal_review.json` — the ideal Kale model response (AIReview schema)
- `labels.json` — evaluation labels
