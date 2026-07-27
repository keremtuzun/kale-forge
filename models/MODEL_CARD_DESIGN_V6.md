# Kale Forge Design Model — adapter v6

LoRA adapter specializing `mlx-community/Qwen3-4B-Instruct-2507-4bit` into the Design Studio's
FRC design-intent task. Supersedes v4 (`MODEL_CARD_DESIGN_V4.md`). An intermediate v5 was
trained and deliberately not shipped — see "iteration history" below.

## What changed vs v4

1. **Catalog enrichment** (`frc-parts-2026.07.25`, all specs web-verified against vendor docs):
   SDS MK5i and MK5n modules (R1 7.03 / R2 6.03 / R3 5.27, 26:1 gear-driven azimuth); Kraken
   X60 trapezoidal + FOC curves (6000 rpm/7.09 N·m/366 A and 5800 rpm/9.37 N·m/483 A); REV PDH
   exact channel split (20×40 A ATO + 3×15 A + 1 switchable); CTRE PDP 2.0 (24×40 A); the ATO
   breaker family; turret techniques (large-bore bearing, wire management, zeroing) and climb
   techniques (deep-cage engagement, telescoping bearing blocks, one-way-bearing PTO).
2. **De-templated corpus**: every design note/risk samples from 3 phrasing variants, so the
   model learns reasoning, not sentences.
3. **Hardware-named intents**: ~1/3 of training requests name real drivetrain hardware
   ("MK5i at R3 on Kraken X60s"), forcing drive_type=swerve and injecting catalog-true ratios,
   plate sizes, and computed free-speed ceilings into the reasoning.

## Training

| | v4 | v5 (not shipped) | **v6** |
|---|---|---|---|
| Dataset | design-v4, 2,298 rows | design-v5, 2,508 | **design-v6, 2,708** |
| Iters | 300 | 400 | **600** |
| Final val loss | 0.061 | 0.088 | **0.101** |

(Val loss is not comparable across versions — later corpora are deliberately higher-entropy.)

## Behavioral evaluation (8 diverse prompts, temp 0.7)

| Metric | v4 | v5 | **v6** |
|---|---|---|---|
| Valid JSON | 6/6 | 8/8 | **8/8** |
| Distinct designs | 6/6 | 8/8 | **8/8** |
| Enum violations | 0 | ~5 (blended values) | **1** |
| Mean nearest-corpus similarity | 0.945 | 0.691 | **0.805** |
| Max similarity | 0.996 | 0.807 | **0.882** |
| "MK5i at R3" → swerve | n/a | ✗ (tank) | **✓** |
| Turret request → turreted shooter | n/a | ✓ | **✓** |
| Deep-climb request → deep-cage hook | n/a | ✓ | **✓** |
| Exact training-row copies | 0 | 0 | **0** |

## Iteration history (why v5 was skipped)

v5 proved de-templating slashes similarity (0.945→0.691) but at 400 iters the higher-entropy
targets were undertrained: it emitted blended, schema-invalid options ("deep-cage climb arm")
and ignored named hardware ("MK5i" → tank). v6 added hardware-named training intents and 600
iters: enum discipline recovered (1 violation in 8 outputs), the MK5i→swerve mapping now works,
and similarity stays far below v4.

## Honest limitations

- One enum violation in 8 eval outputs ("wristed elevator" as an arm_type) — the runtime's
  schema-constrained decoding rejects such values in production, but the raw model is not yet
  violation-free.
- Constraint adherence is imperfect: a "do ONE job" request still over-scoped to 4 subsystems.
- Similarity 0.805 means phrasing still leans on corpus vocabulary; free-form novelty remains
  the roadmap's frontier (docs/robotics-synthesis-roadmap.md).
- Produces design intent, not native CAD. Never claims rule compliance or structural safety.
- Trained off-repo on local disk (iCloud read-latency workaround); artifacts copied back.
