# Kale Forge Design Model — adapter v11

LoRA adapter over `mlx-community/Qwen3-4B-Instruct-2507-4bit`. v11 is the **season** revision.

v10 answered "does the model know what a mechanism is made of." v11 asks a harder question:
**does it know what a mechanism is *for*** — can it read a game's constraints and design to
them, rather than reproducing the robots that game produced?

> **Status: NOT PROMOTED. v10 remains the serving adapter.**
>
> v11 kept the season revision's intent quality and **regressed badly on CAD generation** —
> 3/9 valid JSON against v10's 6/9. The cause was a training-window defect, not a flaw in the
> season idea: 47.7% of the CAD training targets were silently truncated. See
> [Evaluation](#evaluation) and [What went wrong](#what-went-wrong). The fix is v12.

## Why a season revision

Training on a current season is where memorization pressure is highest. The season has a known
best answer, every public source converges on it, and a model that reproduces it looks
excellent right up until the game changes. Reproducing this year's robots is the failure mode,
not the goal.

The structural answer is `services/analysis/app/services/frc_season.py`, which makes the season
an **input** rather than a fact. A season carries its field as dimensioned elements, its scoring
table and ranking bonuses, and the construction rules that differ between years. Everything a
mechanism is sized against is then *derived* from those:

| | 2025 REEFSCAPE | 2026 REBUILT |
|---|---|---|
| Frame perimeter (R104) | 120 in | **110 in** |
| Starting height (R104) | 42 in | **30 in** |
| Extension (R105) | 18 in | **12 in** |
| Propulsion motors (R502) | 4 | 4 |
| Gamepiece | CORAL / ALGAE | FUEL, 5.91 in |
| Scoring geometry | REEF L1–L4, 18–72 in | HUB opening at 72 in |
| Climb | CAGE 3.5 / 29.4 in | TOWER rungs 27 / 45 / 63 in |

"The perimeter budget is 110 in, so a square frame is at most 27.5 and 28 × 28 fails
inspection" transfers to a season nobody has played. "The 2026 robot is 27 in wide" does not.
That distinction is the whole design of this revision.

## What changed

**Corpus `design-v11`** — 2,858 rows, 10 families. Distinct-answer counts in parentheses show
how much of each family is genuinely different rather than one target repeated:

| family | rows | distinct | what it teaches |
|---|---|---|---|
| `design_intent` | 900 | 900 | the real inference task, now with the season block |
| `season_rules` | 446 | 362 | the field and rulebook as derivations + sampled sizing problems |
| `cad_geometry` | 340 | 334 | a subsystem as dimensioned parts |
| `cad_qa` | 263 | 227 | cut lists, travel, exit velocity, winch torque, legality |
| `binder` | 141 | 139 | requirement → options → calculation → validation → risks |
| `electrical` | 120 | 114 | channel, breaker and wire planning |
| `parts_qa` | 74 | 74 | dimensioned catalog facts |
| `technique` | 67 | 67 | why a build technique exists |
| `diagnosis` | 15 | 15 | symptom → cause → fix |
| `strategy` | 12 | 12 | archetype choice with the scoring arithmetic |
| merged `design-v2` | 480 | 479 | general hardware ability, so FRC does not narrow the model |

**The family that carries the generalisation** is the ~420 sampled sizing problems inside
`season_rules`. Frame budget, launch angle and exit velocity, climb reach and hopper capacity,
with every input drawn at random:

> *"We want to score into the HUB from 28 ft with a 18 in release height. What does the shooter
> need?"* → the cheapest launch angle is 45° + ½·atan(Δh/d) = 49.6°; required exit velocity
> 32.5 ft/s; that is 65 ft/s of surface speed on a single hooded wheel or 32.5 with a
> counter-rotating pair; on a 5 in wheel, ~2,979 RPM.

There is no answer to memorize. The method is the only thing that survives.

**New subsystem: `hopper`.** Bulk gamepiece handling is first-class — spindexer, belt-floor,
twin-lane, funnel-to-tower, serpentine, paddle-wheel. Sized volumetrically (floor area × 80% of
wall height × 60% random-pour packing ÷ one piece), one exit lane out however many go in, gated
on a beam-break rather than a timer. Full CAD assembly, and turret geometry on hooded shooters
including the energy chain — a wire path that is not modelled is one that gets zip-tied on at
the event.

**Frames that do not fit are resized, with the reason.** A third of `design_intent` examples ask
for an over-budget frame, and the target is the resized frame plus the rule that caused it.
Learning "that is over the budget, here is what fits" is worth more than learning any frame size.

**Held out of CAD training entirely** (`CAD_HELD_OUT`) — still named in `design_intent`, so the
model knows the names and has never seen the geometry:

- ground-to-feeder tunnel intake
- variable-hood flywheel shooter
- telescoping box elevator
- four-bar linkage arm
- winch-driven carriage climb
- **serpentine tunnel indexer** ← 2026
- **paddle-wheel agitator hopper** ← 2026

The last two are the point. Training on this season is worthless if it only produced a model
that can rebuild this season's robots.

## Training

LoRA, 900 iters (up from v10's 800 — the corpus has fewer rows but the binder and sampled-math
targets are long and structured), num_layers 6, batch 1 × grad-accum 4, lr 1e-4, max_seq 2048,
`mask_prompt: true`. Everything else is v10's recipe unchanged, because changing one thing at a
time is the only way the comparison means anything.

Config: [`training/configs/mlx-design-4b-v11.yaml`](../training/configs/mlx-design-4b-v11.yaml)

**Validation loss:** 2.649 → 0.305 (200) → 0.300 (400) → 0.210 (600) → **0.095 (800)** → 0.279 (900).

The final checkpoint is three times worse than iteration 800, and training loss was still
bouncing between 0.126 and 0.663 on adjacent reports. Raising iterations from v10's 800 to 900
on a flat 1e-4 schedule overshot. Both the 800 and 900 checkpoints were promoted and scored
rather than picking one from the loss curve — with `val_batches: 8` the validation estimate is
too noisy to select on, which is its own finding and is fixed in v12.

## Evaluation

`eval_cad_adapter.py` was updated alongside the corpus, and that update was not optional: v11
trains with the season block in the prompt, so scoring it without one would have measured the
harness and reported a fake regression. This exact mistake invalidated a v10 reading once
already. Changes: every eval prompt carries its season; `sensor` and `chain_track` added to
`KNOWN_TYPES` so correct output is not scored as hallucination; `hopper_type` added to the
intent vocabulary; a hopper CAD prompt added; the two 2026 hopper archetypes added to the
held-out set.

Every check is mechanical — does it parse, is every feature type one the renderer knows, is
every structural member a catalog stock section, does each gear's pitch diameter equal
teeth ÷ DP.

**The number that matters** is the gap between the trained-mechanism CAD score and the held-out
CAD score. A model that memorised scores well on the first and collapses on the second.

| | intent valid | enum-clean | distinct | CAD valid | CAD clean | CAD mean feats | held-out valid | held-out clean |
|---|---|---|---|---|---|---|---|---|
| **v10** | 9/9 | 9 | 9 | **6/9** | **5** | **9.9** | **4/7** | **3** |
| v11 @800 | 8/9 | 8 | 8 | 3/9 | 1 | 4.6 | 5/7 | 1 |
| v11 @900 | 9/9 | 9 | 9 | 3/9 | 2 | 5.6 | 2/7 | 0 |

Read honestly: **the season conditioning did not hurt intent** — v11@900 matches v10 at 9/9
valid, 9 enum-clean, 9 distinct, while now also handling the season block and a hopper
vocabulary v10 never saw. Everything downstream of CAD generation regressed, and the held-out
score fell with it. This is not a "learned vs memorised" result. It is a broken-output result,
and the held-out numbers carry no information about generalisation while the model cannot close
a JSON object.

## What went wrong

**47.7% of `design-v11`'s `cad_geometry` training rows exceeded `max_seq_length: 2048` and were
silently truncated** — longest 2,934 tokens, median 2,020, sitting exactly on the limit. Every
other family measured 0% over. A truncated target teaches the model to emit JSON that never
closes, which is precisely the observed failure mode.

| corpus | CAD rows over 2048 | median tokens |
|---|---|---|
| design-v10 | 37.3% | 1,814 |
| design-v11 | **47.7%** | **2,020** |

So this was **a latent defect in v10 that v11 made worse**. Adding the season line to CAD
prompts, plus the richer assemblies the season revision introduced — the hopper is 35 features,
the turret adds ~8 to the shooter — pushed the median onto the ceiling. MLX prints the warning
at startup (`Some sequences are longer than 2048 tokens… will be truncated`) and it was read
past on both runs.

Two process lessons worth keeping:

1. **Token-length distribution is a corpus property that must be checked against the training
   window before launching**, not after a bad eval. It is a thirty-second measurement guarding
   a multi-hour run.
2. **`val_batches: 8` is too noisy to select a checkpoint on.** Iterations 800 and 900
   disagreed by 3x, and the eval showed neither was good — the loss curve was not measuring
   what mattered.

Neither of these is visible from the loss curve alone, which is why the eval exists and why a
regression gets a model card rather than a deletion.

## Limitations

- Everything the model emits is **dimensioned concept geometry**. Numbers are consistent with
  each other and with the parts catalog; none has been checked against a vendor drawing, a
  stress case, or the current manual.
- Season figures are the published values restated as engineering inputs. **Rules move by team
  update during a season** — the manual is the only authority, and the model says so.
- The shot solution is a vacuum trajectory: no drag, no spin, no Magnus. Real range is shorter.
  It is an optimistic ceiling for sizing, not a firing solution.
- Hopper capacity is volumetric and optimistic. The real limit is usually what the exit lane
  clears without jamming.
- The rule check covers four things a synthesised robot can actually get wrong (perimeter,
  stowed height, propulsion motor count, estimated weight). Passing means nothing was caught.
  **It is not an inspection.**
