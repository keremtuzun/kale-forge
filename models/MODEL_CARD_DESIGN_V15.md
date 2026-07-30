# Kale Forge Design Model — adapter v15

LoRA adapter over `mlx-community/Qwen3-4B-Instruct-2507-4bit`. v15 is the **data** revision:
v14's proven-stable recipe (rank 8, 6 layers, lr 1e-4, window 3072) fed the design-v13 corpus,
which scales the families that carry CAD detail and design justification.

> **Status: promoted as the CAD-generation adapter.** Best held-out CAD validity of any
> adapter to date (5/7 vs v10's 4/7), at the cost of a real regression on intent JSON
> validity (7/9 vs 9/9). Serve it behind the schema-validation + deterministic-fallback
> guard, which the Design Studio applies to intent output anyway. v10 remains the safer
> choice where intent validity matters more than CAD depth.

## What changed: the corpus

design-v13 — 4,533 rows vs v12's 3,768. Three families scaled, per the direction to train on
CAD-model and technical-binder data:

| family | v12 | v13 | note |
|---|---|---|---|
| `binder` | 143 | **480** | now covers the **drivetrain** (module choice, free speed vs stall thrust, encoder placement); sampled across ~110 robots |
| `cad_qa` | 262 | **624** | cut lists, travel, exit velocity, winch torque across 55 more sampled robots |
| `cad_geometry` | 429 | 495 | growth capped by the per-structure dedup, deliberately |
| `season_rules` | 726 | 726 | held at full strength — this family carries the generalisation |
| `design_intent` | 1400 | 1400 | unchanged |

Window check with the real tokenizer: longest row 2,955 of 3,072, zero truncation in any
split. The defect that halved v11's CAD validity stays fixed.

## Training

800 iterations configured; the host session died at ~640 (the same way v14's did). Checkpoint
600 was promoted directly — **no resume**. Val trajectory: 2.848 (start) → 1.471 (100) →
0.716 (200) → 0.657 (300) → 0.931 (400, transient) → 0.616 (500) → **0.615 (600)**. The
500→600 plateau plus v11's overshoot history made the remaining 200 iterations not worth the
unvalidated resume path that destroyed v14. Peak memory 6.37 GB on the 8 GB M2.

## Evaluation

`models/evaluations/kale-design-v15-cad.json`, same-run comparison, 9 intent prompts and
7 held-out CAD mechanisms (mechanisms deliberately excluded from training):

| | v10 | v14 | **v15** |
|---|---|---|---|
| intent valid JSON | 9/9 | 9/9 | **7/9** |
| intent enum-clean | 8/9 | 8/9 | 6/9 |
| distinct designs | 9/9 | 9/9 | 7/9 |
| CAD held-out valid JSON | 4/7 | 2/7 | **5/7** |
| CAD schema-clean | 2/7 | 0/7 | 0/7 |
| CAD mean features | 8.7 | 1.9 | 7.3 |

Read honestly:

* **The CAD-data enrichment worked where it aimed.** 5/7 valid JSON on mechanisms the model
  never saw is the best any adapter has scored, and against v14 (2/7, mean 1.9 features from
  its wrecked resume) it is not close.
* **Intent regressed.** 2/9 intent prompts failed JSON validity. The corpus rebalanced toward
  CAD/binder rows (design_intent fell from 37% of rows to 31%), which is the most credible
  cause. In production this failure mode is caught by the schema gate and falls back to
  deterministic synthesis; it is still a regression and the next revision should rebalance.
* **schema_clean 0/7 needs a caveat:** valid JSON that violates schema detail (e.g. a missing
  optional block) still renders in the viewer; v10's 2/7 is better on this axis. Small-n
  applies everywhere — 7 and 9 prompts move by whole percentage points per flip.

## Next

1. Rebalance: hold binder/cad_qa gains but restore design_intent share (~1,700 rows) — the
   two failures were both long multi-subsystem intents.
2. Validate the re-warm resume path on a throwaway run so a session kill stops costing the
   tail of every training run.
3. Score checkpoint 500 (val 0.616) against 600 if intent validity matters for serving.
