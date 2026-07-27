# Kale Forge Design Model — adapter v9

LoRA adapter specializing `mlx-community/Qwen3-4B-Instruct-2507-4bit` into the Design Studio's
design model. v9 is the first iteration that teaches the model **CAD**: not just which mechanism
to build, but the dimensioned parts that mechanism is made of.

v6 grounded the model in hardware. v7 gave it mechanism depth for the intake. v9 gives the whole
robot — chassis, drivetrain, intake, shooter, elevator, arm, climber, control system — a part
tree, and trains the model to emit it.

> There is no adapter v8. `datasets/processed/design-v8` was generated on 2026-07-26 and never
> trained; v9 supersedes it with the same intent recipe plus the two CAD families. It is left in
> place rather than overwritten.

## What changed from v7

Everything is data-side and derived from the modules the runtime uses, so training data and
runtime knowledge cannot drift apart.

1. **New module `frc_cad.py`.** Expands a robot spec into a hierarchical, dimensioned CAD tree:
   tubes with a catalog section, wall and length; plates with pockets cut through them; hex
   shafts; flanged bearings; HTD pulleys; gears whose pitch diameter follows from tooth count and
   diametral pitch; belts; wheels; motors at their catalog envelope; fastener patterns. Roughly
   **200 features across 11 assemblies** for a full robot, all in one coordinate system (inches,
   +X right, +Y up, −Z forward, origin at the frame centre on the bellypan).
2. **Two new corpus families.**
   * `cad_geometry` (284 rows) — a request and one subsystem in, that assembly's parts out as
     JSON. Covers the hand-written team asks *and* a walk of every enumerated mechanism type, so
     the model sees the geometry that belongs to each named type rather than generalising from
     whichever intake it saw most often.
   * `cad_qa` (200 rows) — cut lists, elevator travel and overlap, shooter exit velocity, winch
     torque, arm joint geometry. Every number is computed from the CAD tree, not remembered.
3. **Superstructure spec depth**, matching what v7 did for the intake: shooter gains surface
   speed, exit velocity, flywheel mass and spin-up; the elevator gains travel, per-stage travel,
   overlap at full extension, carriage speed multiple, upright span and drum diameter; the arm
   gains shoulder height; the climber gains rope tension, drum torque, reduction and travel.
4. **Techniques 39 → 57, references 9 → 11.** New craft for shooter (exit velocity is half the
   surface speed with one wheel; compression sets energy transfer; staging a barrel), elevator
   (stage overlap carries the moment; cascade multiplies travel and speed together), arm (split a
   long reach; hard stops before software limits; bearing blocks in pairs), climber (size the
   winch on drum torque; two load paths beat one), and CAD (model to stock not to shape; one datum
   per mechanism; centre distances are the design; fastener access is geometry).

Corpus `design-v9`: **4,084 rows** (design_intent 2,600 · electrical 380 · cad_geometry 284 ·
cad_qa 200 · parts_qa 68 · technique 57 · diagnosis 15 · merged design-v2 480). Verified to
regenerate **byte-identically** from the current runtime modules — no train/serve drift.

Training: LoRA, num_layers 6, batch 1 × grad-accum 4, lr 1e-4, max_seq 2048, mask_prompt,
grad_checkpoint. The config is written for 900 iters — up from v7's 600, because the corpus is
~1.5× larger and a CAD target is far longer and more structured than an intent JSON — but the
**shipped adapter is the iter-800 checkpoint**. The run was stopped there deliberately to leave
time for evaluation inside a fixed session budget, not because of a training problem. To
reproduce the full schedule, run `training/configs/mlx-design-4b-v9.yaml` unchanged.

Validation loss: 3.946 (iter 1) → 0.819 (200) → 0.133 (400) → 0.162 (600) → **0.094 (800)**.

## Eval — v7 vs v9

`eval_cad_adapter.py`, temp 0.3, 6 intent + 6 CAD prompts. Everything scored is a mechanical
check, not a judgement: does it parse, is every feature type one the renderer knows, is every
structural member a catalog stock section, does each gear's pitch diameter equal teeth ÷ DP.

### CAD geometry — the capability v9 adds

| Metric | v7 | **v9** |
|---|---|---|
| Parses as one JSON assembly | 5/6 | **6/6** |
| Mean features per assembly | 0.8 | **12.7** |
| Schema-clean (known types, stock sections, consistent PD) | 0/6 | **4/6** |

**v7 cannot do this task at all.** It returns a well-formed envelope with a single placeholder
feature — no positions, no sections, `"t": "structural"` or `"t": null`. v9 returns real
assemblies: 10–16 dimensioned features with stock sections, tooth counts and consistent pitch
diameters. The shooter, arm, chassis and elevator assemblies come back fully clean.

The two v9 failures are worth naming precisely: the intake emitted a `"fastened"` feature type
the renderer does not know and a 1.1 × 1.1 tube that is not stock; the climber emitted a section
that fails the same check. Both are exactly the class of error the runtime would have to clamp,
and both are the frontier for v10.

### Design intent — the existing capability

| Metric | v7 | **v9** |
|---|---|---|
| Valid JSON | 6/6 | 5/6 |
| Distinct designs | 6/6 | 5/6 |
| Enum-clean (strict exact match) | 3/6 | 1/6 |

**v9 is behind v7 on intent, and the raw first run looked much worse than it was.** That run
scored v9 at 3/6 valid JSON. Re-running the failures with a larger token budget showed all of
them were **truncation in the harness, not the model** — every completion ended on a balanced
`}` once given room, because v9 writes longer intent objects than v7 does. The harness defaults
have been raised (2000 tokens for intent, 2600 for CAD) so this cannot recur; the table above is
after that correction, and one genuine failure remains.

Enum discipline is the real regression: 1/6 against v7's 3/6, with near-misses like
`shooter_type: "dual flywheel hooded shooter"` (the enum is `"dual independent flywheel hooded
shooter"`) and an `arm_type` filled with a shooter value. Adding 484 long, structured CAD targets
to a 4,084-row corpus appears to have pulled capacity away from holding the intent vocabulary
exactly. The runtime's `_sanitize_intent` / `_pick_type` clamp these to valid enums in
production, so the Design Studio's behaviour is protected — but this is a real trade, not a wash,
and it is the thing to fix in v10 (candidates: weight the intent family higher, or train the two
tasks as separate adapters).

Scorecard: `models/evaluations/kale-design-v9-cad.json`.

## Honest limitations

- **Dimensioned concept geometry, not manufacturing CAD.** Sections, bores and centre distances
  are consistent with each other and with the parts catalog. None of it has been checked against
  a vendor drawing, a stress case, or the current game manual.
- **Telescoping stages are not orderable stock.** The elevator and climber inner stages are
  modelled as sections cut down to nest inside the tube outboard of them (1.64×0.8, 1.28×0.8,
  1.55×1.55). Real teams buy a nesting ladder instead. The cut list now separates stock from
  fabricated sections and says so, but the geometry itself still needs the substitution — and one
  `cad_qa` answer in the v9 corpus still says "every member is a catalog stock section", which is
  wrong for those rows. Fix the generator wording before v10.
- **Enum discipline remains the frontier**, as it was for v5–v7. The runtime's `_sanitize_intent`
  / `_pick_type` clamp near-miss strings to valid enums in production; the raw model is not
  violation-free.
- **The live demo does not use these weights.** `kale-demo.vercel.app` runs the deterministic
  synthesis with `use_model=False` — stdlib only, no network, no AI. v9 improves the model
  artifact and the inference service, not the demo's output.
- Never claims rule compliance, structural safety or fabrication readiness.

## Provenance

`training/configs/mlx-design-4b-v9.yaml`, corpus `datasets/processed/design-v9`
(`kale_training.generate_frc_corpus`, seed 9001). Trained on local disk (`/private/tmp`) with
mlx-lm 0.31.3 installed into `/usr/local/bin/python3`; artifacts copied back to
`models/adapters/kale-design-qwen3-4b-v9`. No vendor prose, CAD, meshes or user data copied.

Eval harness: `services/evaluation/kale_eval/eval_cad_adapter.py`.
Property tests: `services/analysis/app/tests/test_cad.py`.
