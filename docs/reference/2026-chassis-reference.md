# 2026 chassis and bumper reference

Source: `2026-chassis-reference.zip` — an Onshape Part Studio OBJ export of a real 2026 chassis.
Measured with the OBJ's own units (metres), converted to inches.

| Part | Measurement |
|---|---|
| Frame outer | 27.56 × 23.62 in (700 × 600 mm) |
| Tube stock | 50 × 25 mm (1.97 × 0.98 in), strong axis vertical |
| Rails | 2 × 600 mm full length + 2 × 650 mm captured between them |
| Corner gussets | 2.95 × 2.80 × 0.98 in, one corner carrying two stacked |
| Bumper | 5.00 in tall, 3.31 in proud per side, underside flush with the frame rail |
| Bumper outer | 34.17 × 30.24 in |

The light grey sheet in the reference screenshots is the Onshape **Top plane**, not a bellypan.
There is no bellypan in the export.

## What was taken from it

The mesh is **not** imported. `models/V18_RELIABILITY_ARCHITECTURE.md` requires validated
FeatureScript rather than OBJ flattening, so the export was measured and the numbers drive the
parametric compiler:

- `BUMPER_HEIGHT_IN`, `BUMPER_THICKNESS_IN` and the plywood/noodle split in
  `services/analysis/app/services/frc_cad.py`, applied by `_bumper()`.
- The corner gusset footprint in `_chassis()`.
- `bumper_envelope()`, published on the spec as `spec["bumper"]`, so the viewer, the packaging
  maths and the cut list all read one definition.

The rail pattern already matched: `_chassis()` built the mitre-less frame — two full-length
side rails with the front and back captured between them — before the reference arrived.

## What was deliberately not copied

The reference's 700 × 600 mm frame is that team's choice, not a constant. Kale sizes the frame
from the prompt and the season's perimeter budget, so the reference contributes **construction
and proportion**, not dimensions. A 2026 design still resolves to a legal frame; only the way
the bumper and corners are built now follows the reference.

The 0.98 in gusset thickness is the reference's own modelling (a solid corner block). Kale
emits a gusset feature and lets the compiler give it a plate thickness, so that number is not
carried across.
