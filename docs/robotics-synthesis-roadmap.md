# Kale Forge — Robotics-Synthesis Model Roadmap

Scoping document for evolving Kale Forge from a **design-review + concept-generation** product
into an **AI robotics design engineer**: a system that synthesizes original, manufacturable,
fully editable robots from natural-language requirements by applying learned engineering
principles rather than reproducing memorized designs.

This is a research program, not an MVP feature. It reuses the honest, license-gated foundations
already in the repo — `services/training/kale_training/*`, [model-strategy.md](model-strategy.md),
[training-data-strategy.md](training-data-strategy.md), [evaluation-plan.md](evaluation-plan.md) —
and extends them along three subsystems the goal implies: **data pipeline → training service →
validation harness**.

> Positioning discipline (inherited from `model-strategy.md`): we describe capabilities exactly
> as they exist. Nothing below authorizes marketing a capability before its validation gate is
> green. Phases 0–1 are buildable now; Phase 2+ is genuine R&D with real unsolved problems.

---

## 1. Goals and non-goals

**Goals**
- Interpret a requirement ("a 24 kg warehouse AMR that climbs a 15° ramp, 8-hour runtime, under $X")
  and synthesize a *novel* robot that satisfies it.
- Emit **native, feature-based parametric CAD** — sketches, extrudes/revolves/lofts/sweeps,
  fillets/chamfers/patterns, assemblies, mates, configurations, editable feature history —
  Onshape-first, then SolidWorks / Fusion / Blender / STEP.
- Learn transferable engineering principles (statics, dynamics, mechanism synthesis, machine
  elements, materials, manufacturing, controls integration) so outputs generalize across domains:
  FRC/FTC/VEX, industrial, warehouse, agricultural, medical, underwater, aerospace, research,
  humanoid, quadruped, AMR, service, combat.
- **Validate every design automatically** and revise-until-passing before it reaches the user.

**Non-goals (explicit, to keep the program honest)**
- ❌ Training a foundation model from scratch. We continue the fine-tune-an-open-base strategy;
  the moat is dataset + representation + validation loop, not base weights.
- ❌ Ingesting proprietary CAD we don't have the rights to. "Legally obtained" is a hard gate,
  not a preference (§3.1). The dataset allowlist governs this exactly as it does today for KiCad.
- ❌ Reproducing recognizable robots or specific CAD assemblies. Anti-memorization is a
  first-class, *measured* requirement (§6), not an aspiration.
- ❌ Replacing a human engineer's sign-off. Manufacturability/safety claims remain advisory;
  the `DISCLAIMER` and "verification required before fabrication" language stay.

---

## 2. Where we are vs. where this goes

| Capability | Today (in repo) | Target (this roadmap) |
|---|---|---|
| Robot output | `robot_spec` JSON → procedural OBJ mesh + BOM + FeatureScript stub | Native parametric feature tree + assembly + mates, editable |
| Knowledge | Enumerated FRC catalog (`app/services/frc_parts.py`) + technique library | Learned engineering principles across many robot domains |
| Generation | Deterministic synthesis + optional fine-tuned Qwen2.5 intent model | Multi-representation model that emits CAD *programs* |
| Validation | Power tree, mass estimate, breaker/wire sizing, drivetrain math | Full harness: kinematics, dynamics, FEA, tip-over, manufacturability, clearance |
| Novelty | Content-hash dedup of *training rows* | Runtime novelty check of *generated designs* vs. corpus |

The jump from "row 1 procedural OBJ" to "row 1 parametric feature tree" is the central technical
bet. Everything else (data breadth, curriculum, RL) is in service of making that jump reliable.

---

## 3. Subsystem A — Data pipeline

Extends the existing seven-stage pipeline in [training-data-strategy.md](training-data-strategy.md)
(collect → filter → normalize → dedup → review → split → version). New concerns below.

### 3.1 Legal sourcing is the binding constraint

CAD from Onshape/SolidWorks/Fusion/Inventor/Creo/CATIA/NX is, by default, **someone else's
copyrighted work**. The vision's "legally obtained" clause is the whole ballgame. Allowed inputs,
in priority order:

| Tier | Source | Rights basis | Gate |
|---|---|---|---|
| 1 | **Kale-authored** parametric designs + our procedural generators | We own it | automatic |
| 2 | **Openly licensed** CAD/mechanism libraries (CC0/CC-BY/MIT/Apache, CERN-OHL, permissively-licensed GrabCAD/Onshape public docs where terms allow) | SPDX id recorded per asset | license validation + review |
| 3 | **Textbook/paper knowledge** on machine design (statics, FEA, gears, bearings, screws, drives, tolerances, failure analysis) as *derived facts*, not verbatim text | fair-use summary, cited | reviewer sign-off, no verbatim reproduction |
| 4 | **Licensed data partnerships** (competition orgs, vendors) with explicit written terms | contract | legal review before ingest |
| 5 | **User-contributed** designs | per-item opt-in consent, as today | consent → PII/secret scan → dedup → review |

Refused, hard: scraped proprietary CAD, terms-of-service-violating exports, NC-licensed content,
and anything from external AI APIs (poisons provenance and violates the self-hosted constraint).
A `rights_basis` field joins the existing `license` field; rows without a clean basis are
quarantined, never trained.

### 3.2 CAD representation — the core research problem

Meshes are lossy; the product needs *editable features*. We standardize on an internal
**CAD-program IR**: a serialized, deterministic sequence of parametric operations (sketch →
constraints → feature → mate → parameter) that round-trips to Onshape FeatureScript and to STEP
via B-Rep. Training targets this IR, not raw geometry, so the model learns to *author* CAD, not
draw pictures of it. Ingest adapters (STEP/Parasolid/IGES → B-Rep → feature inference where
possible; Onshape/SW feature histories → IR directly) normalize every tier-1/2 source into it.

### 3.3 Multi-representation linking

Each design carries aligned views so the model learns one mechanism through many lenses:
IR (parametric) · drawing (dimensioned) · simulation result · rendered image · short video/GIF ·
BOM · natural-language rationale. Alignment is by shared `design_id`; missing views are allowed
but flagged (a design with no sim view can't earn a "dynamics-validated" label).

### 3.4 Procedural augmentation (memorization reducer #1)

The single biggest lever against memorization is volume of *structured variation*. Parametric
generators (extending `generate_design_corpus.py` / `generate_synthetic.py`) sweep: chassis
geometry, wheelbase, drivetrain type, suspension, frame dims, arm/elevator stages, intake/roller
spacing, gear ratios, motors, electronics placement, sensors, structural members, mounts,
materials, CoG, weight limits, manufacturing method, and objective. Each variation is
re-validated so its labels are ground truth, not guesses. Paired with **synthetic prompts** over
the requirement space (speed, torque, packaging, modularity, repairability, autonomy, defense,
climbing, traversal, efficiency, rigidity, cost, CNC/sheet/carbon/additive, field serviceability).

### 3.5 Deliverables (Subsystem A)
- `rights_basis` schema field + allowlist validator (extends secret_scan/license gates)
- CAD-program IR spec + round-trip codec (IR ↔ FeatureScript, IR → STEP)
- Ingest adapters per format; a "features recovered / geometry-only" quality flag
- Parametric + prompt augmentation generators wired into `datasets/processed/<version>`
- Provenance + license report per dataset version (blocks promotion on any un-cleared row)

---

## 4. Subsystem B — Training service

Extends `services/training` (LoRA/QLoRA SFT already present in `scripts/train_sft.py`,
configs in `training/configs/`). Same base-model discipline as `model-strategy.md`.

### 4.1 Curriculum (fundamentals → systems → RL)

Staged so the model builds principles before products:

1. **Engineering fundamentals** — statics, dynamics, strength of materials, FEA intuition.
2. **Machine elements** — gears, bearings, belts/chains, shafts, couplings, planetary/harmonic/
   cycloidal drives, lead/ball screws, rack-and-pinion, linear rails, pneumatics/hydraulics.
3. **Parametric CAD authoring** — emit valid IR: sketches, constraints, features, patterns.
4. **Robot mechanisms** — drivetrains, manipulators, end-effectors, intakes, elevators, climbers.
5. **Integrated systems** — electronics, power, sensors, controls, packaging, serviceability.
6. **Physics-grounded fine-tune** — train against validation-harness outputs (§5) as labels.
7. **RL from simulation** — reward = weighted engineering performance + constraint satisfaction
   + novelty; penalize invalidity and near-duplication. Uses accepted-vs-revised design pairs
   analogous to the existing DPO feedback path.

### 4.2 Model shape

Start with a Qwen2.5-Coder base (CAD-program IR is code-like) plus, later, a lightweight
geometry/vision encoder for the multi-representation objective. Adapters are versioned and
hot-loadable through the existing inference provider interface — no new serving path.

### 4.3 Deliverables (Subsystem B)
- Curriculum dataset splits + per-stage configs under `training/configs/`
- SFT on CAD-program IR (extends `train_sft.py`); IR-validity metric in the loop
- RL harness that consumes validation-harness rewards (Subsystem C)
- Model registry entries with honest names ("Kale Forge Synthesis vX = <base> + <adapter> vX")

---

## 5. Subsystem C — Validation harness

Every generated robot runs the harness *before the user sees it*; failures trigger an automated
**revise → re-test** loop, and the model is trained on those transcripts. Build order is
cheapest-and-most-decisive first; some checks already exist in `services/analysis`.

| Check | Method | Status |
|---|---|---|
| Assembly validity / clearance | IR consistency + interference test on B-Rep | new |
| Collision detection | broad-phase + narrow-phase on assembly | new |
| Center-of-gravity + tip-over | mass properties from IR + stability polygon | partial (mass estimate exists) |
| Forward/inverse kinematics | joint graph solver | new |
| Drivetrain + motor sizing | torque/speed/current curves | partial (drivetrain math exists) |
| Battery / current budget | power tree | ✅ exists (`calculations/power_tree.py`) |
| Structural / stress | reduced-order FEA, escalate to full FEA on flagged members | new |
| Tolerance / fit | stack-up analysis on mates | new |
| Manufacturability | per-process rules (CNC/sheet/carbon/additive) | new |

Reward signal for §4.1 step 7 is a weighted aggregate of these plus requirement-match and
novelty. A design that fails a hard gate (collision, tip-over, over-current) is never returned as
valid — it is revised or refused, mirroring the existing "refuse to certify" discipline.

### 5.1 Deliverables (Subsystem C)
- Harness service exposing one internal API (`validate(design_ir) → report`), reused by both
  runtime and RL training
- Deterministic checks first (assembly/clearance/CoG/kinematics/power), then FEA and
  manufacturability
- Revise-loop policy (max iterations, refusal on unrecoverable violation) + transcript logging

---

## 6. Anti-memorization (measured, not assumed)

The goal explicitly forbids reproducing recognizable robots. Enforced at three points:
1. **Corpus** — near-duplicate detection (geometry + IR fingerprint) removes clones before training.
2. **Objective** — RL novelty penalty against nearest corpus neighbor.
3. **Runtime** — a novelty gate flags any output too close to a training design and forces a
   re-synthesis; recognizable-IP outputs are blocked.

Tracked as a first-class eval metric alongside validity and requirement-match in
[evaluation-plan.md](evaluation-plan.md): *novelty distance distribution* and *nearest-neighbor
leakage rate* per model version. A version that memorizes does not get promoted.

---

## 7. Phased roadmap

| Phase | Theme | Exit criterion |
|---|---|---|
| **0 — Foundations** (now) | `rights_basis` gate, CAD-program IR spec + round-trip codec, deterministic validation checks (assembly/clearance/CoG) | IR round-trips a Kale-authored robot to Onshape and back; checks run in CI |
| **1 — Authoring** | Parametric augmentation at scale; SFT on IR; novelty dedup | Model emits schema-valid, edit­able IR for in-domain prompts; validity ≥ target; leakage ≤ target |
| **2 — Principles** | Curriculum stages 1–5; multi-representation linking | Generalizes to a held-out domain without domain-specific templates |
| **3 — Physics** | Full harness (FEA/kinematics/manufacturability) + revise loop | Returned designs pass all hard gates; revise loop converges |
| **4 — RL** | Simulation-reward RL + preference pairs | Beats Phase-3 model on performance + novelty on the eval suite |

Each phase gates the next on the eval suite; nothing ships to the product's honest positioning
until its gate is green.

---

## 8. Risks and open questions

- **CAD representation is unsolved at product quality.** IR round-trip fidelity across CAD kernels
  is the top technical risk; Phase 0 is deliberately a go/no-go on it.
- **Legal sourcing may cap dataset breadth.** If licensed partnerships don't materialize, tiers
  1–3 must carry the program; augmentation volume compensates but domain coverage narrows.
- **Simulation fidelity bounds RL.** RL is only as good as the harness; a wrong reward teaches
  wrong engineering. Validation must be trustworthy *before* it becomes a training signal.
- **Compute.** Curriculum + RL over multi-representation data is materially heavier than today's
  LoRA SFT; capacity planning is a Phase-2 prerequisite.
- **Scope honesty.** The website and model metadata must track *actual* phase, never the roadmap.

---

## 9. Immediate next steps (Phase 0, actionable now)

1. Write the CAD-program IR spec (`packages/shared-types/schemas/cad_program.schema.json`) and a
   round-trip codec against one Kale-authored robot.
2. Add `rights_basis` to the training schema + allowlist validator; back-fill existing rows.
3. Stand up the validation-harness service skeleton with the three deterministic checks that have
   the most leverage (assembly/clearance, CoG/tip-over, kinematics) behind one internal API.
4. Extend `generate_design_corpus.py` with the parametric sweep in §3.4, emitting IR + validation
   labels.
5. Add novelty-leakage and IR-validity metrics to the eval suite.

See [architecture.md](architecture.md) for how these services wire into the existing monorepo.
