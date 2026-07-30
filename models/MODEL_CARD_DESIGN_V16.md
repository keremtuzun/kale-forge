# Kale Forge Design Model — adapter v16

> **Status: REJECTED. Do not serve. v15 remains the serving adapter.**

v16 was v15's final 200 iterations (checkpoint 600 → 800), finished on the rebalanced
design-v14 corpus (design_intent restored to 36.5%, binder/cad_qa gains kept). The intent was
to point the tail of training at the one axis v15 regressed on. The mechanics worked — the
re-warm resume held, no divergence, train loss stayed in the 0.36–0.76 band — and the model
still came out worse on everything:

| | v10 | v15 | **v16** |
|---|---|---|---|
| intent valid JSON | 9/9 | 7/9 | **5/9** |
| intent enum-clean | 9/9 | 7/9 | **0/9** |
| CAD held-out valid JSON | 3/7 | 4/7 | **0/7** |
| CAD mean features | 4.7 | 4.9 | **0.0** |

(Same-run comparison, `models/evaluations/kale-design-v16-cad.json`. Note the run-to-run
variance on the incumbents — v15's CAD was 5/7 last night, 4/7 here — which makes v16's 0/7
with zero features unambiguous: that is structural damage, not noise.)

## What actually went wrong

The resume schedule was written as "warm up over 60 iterations, then cosine-decay to 1e-5."
What MLX executed spread the warmup across most of the run: the learning rate was still
**climbing** at iteration 180 (7.4e-5) and the run ended at 8.2e-5 — near peak, never
annealed. Combined with Adam restarting from zero moments, the final 200 iterations were
effectively a short, hot, un-annealed shock to a converged adapter. Train loss stayed calm;
generation quality did not. Val told the truth early: 0.322 at resume → 0.540 at the end,
never recovering its baseline.

## What to do differently

1. A short continuation on a converged adapter should run at a **flat, low LR (~1e-5)** — no
   re-warm to full rate. The re-warm design is for resuming an interrupted schedule mid-run,
   not for a 200-iteration finishing pass.
2. Verify what `lr_schedule` MLX actually executes — print the LR at iteration 1, 60, and the
   end before trusting a schedule comment.
3. The intent rebalance itself is untested, not refuted: design-v14 is generated, validated
   (longest row 2,937/3,072), and ready for a full 800-iteration fresh run as the next
   revision.

## Artifacts

Adapter kept at `models/adapters/kale-design-qwen3-4b-v16` for reference only. Registry
status: rejected.
