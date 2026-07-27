# 01-led-no-resistor

This LED circuit drives an LED directly between a supply rail and ground with no series current-limiting resistor, so the LED current is uncontrolled.

**Category:** led_resistor · **Safety class:** power

**Intended finding:** `COMP-008`

**Rule findings that fire (4):** COMP-004, COMP-008, PWR-003

## Files
- `source/circuit.cir` — SPICE representation (provenance; full pin-type semantics are in `normalized.json`)
- `normalized.json` — Kale.ai normalized circuit (the internal contract)
- `expected_findings.json` — findings the rule engine is expected to produce
- `ideal_review.json` — the ideal Kale model response (AIReview schema)
- `labels.json` — evaluation labels
