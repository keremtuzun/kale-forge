# Kale Forge Design Model — adapter v17

> **Status: REJECTED. v15 remains the serving adapter.**

v17 was the design-v15 corpus (grounded kale-cad-2.2 targets, intent rebalanced to 36.4%,
elite-2026 goals) trained for 800 total iterations. It produced the **best validation loss
ever recorded in this project (0.396)** — and failed the generation eval outright:

| | v10 | **v15** | v17 |
|---|---|---|---|
| intent valid JSON | 8/9 | 8/9 | 8/9 |
| CAD held-out valid JSON | 3/7 | **5/7** | **1/7** |
| CAD mean features | 4.9 | **10.7** | 2.9 |
| eval wall time | 1,161 s | 1,195 s | **5,088 s** |

The 4× eval time is the tell: v17's generations run long and rarely terminate cleanly.
Teacher-forced prediction improved while free-running generation degraded.

## What actually went wrong

The run was not one run. Session restarts killed training at iterations ~360, ~500 (twice at
the same checkpoint), and the tail was finished in a fourth process — so the final weights
saw **three separate Adam restarts** (fresh zero moments at 1e-4-warmed, then 4e-5, then
4e-5) plus one NaN'd false start. Each segment looked healthy by loss; the sum did not
preserve generation behaviour. Val loss measured next-token prediction on in-distribution
rows and kept improving all the way down to 0.396 while the thing we actually ship —
schema-complete free-running JSON — fell apart.

Two lessons, both now policy:

1. **Val loss never gates promotion.** Only the generation scorecard does. (This card is the
   second time a "best-ever" training metric shipped nothing — v11's truncation story was
   the first.)
2. **Fragmented training is not training.** A future run on this host must either survive
   the session (launchd / caffeinate / detached daemon verified BEFORE starting) or not
   start. Checkpoint-and-resume protects compute, not model quality.

## What is still good

- The **design-v15 corpus is sound** and unconsumed: grounded CAD targets, restored intent
  share, elite-2026 coverage, zero truncation. The next uninterrupted 800-iteration fresh
  run should use it as-is.
- The warmed-cosine fresh-start schedule (after the flat-LR NaN at iteration 40) is
  validated through iteration 360 and belongs in the next attempt's config.

## Artifacts

Adapter kept at `models/adapters/kale-design-qwen3-4b-v17` for reference only.
Scorecard: `models/evaluations/kale-design-v17-cad.json`. Registry status: rejected.
