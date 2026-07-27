# 04-relay-no-flyback

The relay coil is switched by a transistor but has no flyback diode; the inductive spike at turn-off can destroy the driver.

**Category:** relay_flyback · **Safety class:** power

**Intended finding:** `COMP-009`

**Rule findings that fire (7):** COMP-002, COMP-009, CONN-005, PWR-007

## Files
- `source/circuit.cir` — SPICE representation (provenance; full pin-type semantics are in `normalized.json`)
- `normalized.json` — Kale.ai normalized circuit (the internal contract)
- `expected_findings.json` — findings the rule engine is expected to produce
- `ideal_review.json` — the ideal Kale model response (AIReview schema)
- `labels.json` — evaluation labels
