# Kale Forge Design Model — adapter v10

LoRA adapter over `mlx-community/Qwen3-4B-Instruct-2507-4bit`. v10 exists to answer one
question: does the model *understand* how an FRC mechanism goes together, or has it memorised
the examples it was shown?

The answer, measured rather than asserted: **not yet.** v10 fixes v9's intent regression and
produces substantially richer geometry, but it does not generalise to mechanism types it has
never seen. That is the honest state, and the reason is in the numbers below.

## What changed from v9

**The v9 corpus was memorisable, and measurably so.** Counting distinct feature-name sequences
per assembly — what a model actually learns to reproduce — v9's CAD family was one template
repeated:

| assembly | v9 | **v10** |
|---|---|---|
| chassis | 33 targets / **1** structure | 28 / **10** |
| swerve module | 35 / **1** | 6 / **2** |
| control system | 39 / **1** | 6 / **2** |
| intake | 34 / 3 | 89 / **36** |
| elevator | 6 / 3 | 73 / **29** |

Two changes produced that. `frc_cad.Choices` makes the decisions that change a *part list* —
crossmember count from unsupported span and superstructure load, belt vs chain vs gear power
paths, plate vs tube supports, rigging and carriage variants, shoulder drive, hood actuation,
feeder type — resolve from the design seed, so two robots differ in construction and not only
in dimensions. And the generator caps repeats of any one structure at three: a swerve module
genuinely *is* a fixed COTS part list, so the fix there is not variety but not emitting it 384
times. Result: 289 CAD targets across **111 distinct structures, none repeated more than 3×**.

**Five mechanism types are held out of CAD training entirely** (`CAD_HELD_OUT`): ground-to-feeder
tunnel intake, variable-hood flywheel shooter, telescoping box elevator, four-bar linkage arm,
winch-driven carriage climb. They still appear in `design_intent`, so the model knows the names;
it has simply never seen their geometry.

**Other fixes:** telescoping stages now come off a real nesting ladder
(2x2x0.125 → 1.5x1.5 → 1x1, or 2x1 → 1.5x0.5) instead of a section shrunk arithmetically into
sizes no supplier stocks; the CAD system prompt is generated from the catalog rather than
hand-typed, which previously listed three allowed sections while the targets used six; and the
enumerated vocabulary is now presented in the prompt at both training and inference.

Corpus `design-v10`: 4,067 rows, verified to regenerate byte-identically from the runtime
modules. Training: LoRA, 800 iters, num_layers 6, batch 1 × grad-accum 4, lr 1e-4, max_seq 2048.

Validation loss: 3.564 → 0.317 (200) → 0.453 (400) → 0.106 (600) → **0.071 (800)** — the best of
any design adapter, on a corpus with ~6× the structural diversity.

## Eval — v7 / v9 / v10

`eval_cad_adapter.py`, temp 0.3, 8 intent + 8 CAD + 5 held-out prompts per adapter. Every check
is mechanical: does it parse, is every feature type one the renderer knows, is every structural
member a catalog section, does each gear's pitch diameter equal teeth ÷ DP.

| | intent valid | enum-clean | distinct | CAD valid | CAD clean | CAD mean feats |
|---|---|---|---|---|---|---|
| v7 | 8/8 | 8 | 8 | 7/8 | 1 | 1.0 |
| v9 | 5/8 | 5 | 5 | 3/8 | 2 | 4.2 |
| **v10** | **7/8** | **7** | **7** | 5/8 | 1 | **7.0** |

**The intent regression is fixed.** v9 dropped to 5/8 valid and 5 enum-clean; v10 recovers to
7/8 and 7, close to v7's 8/8 while also doing the CAD task. The cause was a train/serve
mismatch, not capacity: only the llama.cpp provider constrains decoding to the schema, so under
the transformers provider the model had to *recall* exact enum strings it had never been shown.
Presenting the vocabulary turns recall into selection — which is also more portable, since a
mechanism type added next season works with no retraining.

**v7 cannot do the CAD task at all.** Its 1.0 mean features is a single placeholder feature; its
one "clean" score is an artifact of a one-feature output passing the checks trivially. Read the
feature count before the cleanliness column.

## Generalisation — and why the held-out column is not a fair comparison

| | held-out valid | clean | mean feats |
|---|---|---|---|
| v7 | 5/5 | 0 | 1.0 |
| v9 | 5/5 | 2 | 9.6 |
| **v10** | **2/5** | **0** | 5.2 |

**v9's 5/5 is not generalisation.** `CAD_HELD_OUT` was introduced with the v10 corpus; v9 was
trained on every mechanism type, so these five prompts are in-distribution for it. v9 is
recalling, v10 is extrapolating, and the column is measuring two different things. Only v10's
number says anything about generalisation.

**And v10's number is bad.** Investigated per prompt rather than inferred from the total: the
arm and climber prompts do parse under a larger budget (30 and 14 features) but are not
schema-clean; the variable-hood shooter runs past 1,600 tokens without ever closing its JSON.
So this is a genuine capability gap, not the token-budget artifact that produced two earlier
false readings.

**Conclusion: the de-templating made the corpus honest and fixed the intent regression, but 289
CAD examples across 111 structures are not enough to learn geometry that transfers to an unseen
mechanism.** The memorisation was removed; the understanding has not yet replaced it.

## Where to go next

- **More CAD rows, not more variety per row.** Diversity is now adequate (3 repeats max); volume
  is not. The obvious experiment is 1,500–3,000 CAD targets at the same diversity cap.
- **Train the two tasks as separate adapters.** Intent and CAD have different schemas and
  different output lengths, and they visibly compete.
- **Stop the runaway.** One held-out prompt never terminates. Worth a length penalty or an
  explicit feature-count budget in the prompt.

## Honest limitations

- **Dimensioned concept geometry, not manufacturing CAD.** Nothing is checked against a vendor
  drawing, a stress case, or the current game manual.
- **Does not generalise to unseen mechanism types** — the headline finding above.
- **The live demo does not use these weights.** `kale-demo.vercel.app` runs the deterministic
  synthesis (`use_model=False`): stdlib only, no network, no AI. v10 improves the model artifact
  and the inference service, not the demo's output.
- Never claims rule compliance, structural safety or fabrication readiness.

## Provenance

`training/configs/mlx-design-4b-v10.yaml`, corpus `datasets/processed/design-v10`
(`kale_training.generate_frc_corpus`, seed 10001). Trained with `scripts/train-design-adapter.sh`,
which checkpoints into the repo every 100 iterations and resumes from the newest — three earlier
v10 runs were lost entirely to session exits and a `/private/tmp` wipe.

Scorecard: `models/evaluations/kale-design-v10-cad-fixed.json`. The superseded
`kale-design-v10-cad.json` from the same weights is kept deliberately: it reported v10 at 4/8
intent with `distinct_designs: 1`, which looked like total collapse and was in fact the eval
harness building a prompt shape the model was never trained on. `intent_user_message()` is now
the single function training, inference and evaluation all call, so they cannot disagree again.
