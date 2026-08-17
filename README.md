# Kale Forge

**Kale Forge is a specialized AI design-review platform for electrical engineers.** It analyzes
schematics and PCB projects, detects likely design mistakes, explains circuit behavior, and
helps engineers fix issues faster using a proprietary fine-tuned hardware-engineering model.

Kale Forge runs **entirely on self-hosted AI**: an open-weight base model (Qwen2.5-Instruct,
Apache 2.0) fine-tuned with LoRA on Kale's hardware-engineering dataset, served by
infrastructure Kale controls (llama.cpp locally, vLLM on GPU in production), with local BGE
embeddings and reranking. **No external AI APIs are used for anything** — see
[docs/model-strategy.md](docs/model-strategy.md).

> Kale Forge provides automated design-review assistance and may miss errors or produce
> incorrect recommendations. It is not a substitute for professional electrical engineering
> review, laboratory testing, simulation, regulatory certification, or manufacturer design
> guidance.

## What it does

1. Upload a KiCad project (ZIP, `.kicad_sch`, `.kicad_pcb`, `.kicad_pro`), SPICE netlist
   (`.net`, `.cir`, `.spice`), BOM CSV, or supporting datasheet PDFs.
2. A purpose-built S-expression parser normalizes everything into one JSON representation.
3. A **deterministic rule engine** (40+ modular checks: connectivity, components, power, PCB
   layout) finds structural problems — before any AI is involved.
4. A **power-tree analysis** maps sources → regulators → rails → loads with editable current
   estimates; every number ships with its formula, assumptions, and uncertainty.
5. The **self-hosted Kale review model** explains the findings, prioritizes fixes, and answers
   design questions in chat — with schema-validated JSON output and a citation gate that drops
   any claim referencing components or nets that don't exist.
6. Feedback on findings can (with explicit consent) become training data for the next model
   version; a domain evaluation suite measures whether each fine-tune actually improved.

### Design Studio — prompt to hardware

The Design Studio generates engineering artifacts from a prompt, at whatever scale the request
is written at:

| Ask for | You get |
| --- | --- |
| `Design a bearing block for a 1/2 in hex shaft.` | one machined part, dimensioned from the bearing outward |
| `Design a gearbox plate for two Kraken X60 motors.` | a plate with real motor face patterns |
| `Design an elevator subsystem with two stages.` | one mechanism |
| `Design a swerve robot for the 2026 season.` | a complete robot |
| `Design a CAN sensor PCB with 4 CAN connectors and 12V input.` | a schematic-level board |

One pipeline runs all of them:

```
request → intent classifier → engineering spec → design router → engine → validation → result
```

`app/services/design_intent.py` classifies the request and decides whether an FRC season is
even relevant — a bearing housing never asks for one. `app/services/design_router.py` picks
the engine. Every engine emits the same closed CAD vocabulary, so the 3D viewer, the
FeatureScript export, the STEP worker and the BOM work on a single part and a 400-part robot
without knowing the difference.

Every number in a result is labelled with where it came from: **verified** (a catalog part),
**user provided**, **calculated**, **inferred**, **assumed**, or **unresolved**. Nothing
critical is chosen in silence, and no manufacturer part number is ever invented — an
unresolved component is stated as a requirement instead.

**The season only appears when it governs the design**: a complete robot, a game-dependent
subsystem, or a request that names a season. See
[the season model](#the-season-model--what-a-game-asks-for) below.

Robot synthesis is layered, most authoritative first:

1. **Explicit prompt facts.** "MK4n modules on Krakens at L2+ with a 3-stage elevator" is a
   specification, not a hint — named hardware and stated numbers always win.
2. **The self-hosted model.** Everything left open goes to Kale's own inference service,
   constrained to an enumerated vocabulary with clamped numeric ranges, so a model answer can
   never push a dimension outside what the parts catalog and the rules allow.
3. **Prompt-seeded defaults.** With no weights loaded, remaining choices resolve from a hash
   of the prompt — different requests still diverge instead of collapsing onto one robot.

Output includes replicated COTS envelopes for the parts every robot carries — PDH/PDP, 120 A
main breaker, battery, roboRIO, radio, RSL and the selected swerve module with its real
footprint, drop, ratios and motors — plus a channel-by-channel power budget (breaker, wire
gauge, worst-case bus sag), a mass roll-up against the weight target, and a dossier
explaining every choice. Catalog: `services/analysis/app/services/frc_parts.py`.

> Every dimension is a **nominal published envelope** for packaging and first-order sizing.
> Confirm each part against its vendor drawing, and each constraint against the current game
> manual, before machining anything.

### The season model — what a game asks for

`services/analysis/app/services/frc_season.py` holds each season as four things: the field as
dimensioned elements, the scoring table and ranking bonuses, the construction rules that differ
between years, and **design targets derived from those three rather than stored**.

That last part is the point. A model told "the 2026 robot is 27 inches wide" has memorised a
number. A model told "the perimeter budget is 110 inches, so a square frame is at most 27.5 and
28 × 28 fails inspection" has learned the constraint, and can apply it to a season nobody has
played yet. Everything downstream works the same way:

- **The frame budget outranks a stated frame.** 28 × 28 in is 112 in of perimeter: legal in 2025
  (120 in), illegal in 2026 (110 in). Ask for one in a 2026 design and it is scaled to 27.5 ×
  27.5 with the reason recorded on the spec, rather than quietly built.
- **The shot is solved, not looked up.** From the goal opening height and a working distance,
  the cheapest launch angle is 45° + ½·atan(Δh/d); that gives a required exit velocity, which
  gives a required flywheel surface speed — halved or not depending on whether the gamepiece is
  squeezed against a fixed hood or a counter-rotating pair. The design is then checked against it.
- **A rule check runs over the finished spec** — perimeter, stowed height, propulsion motor
  count and estimated weight — and is *reported, not enforced*. Passing means nothing was
  caught, not that the robot is legal.
- **Bulk gamepiece handling is a first-class subsystem.** The `hopper` (spindexer, belt-floor,
  funnel-to-tower and friends) is sized volumetrically, feeds one lane out however many go in,
  and is gated on a beam-break rather than a timer.

Seasons carry their own archetypes and scoring arithmetic, so "what should we build" has an
answer with numbers behind it. Every figure is the published value restated as an engineering
input, always with the verification caveat attached — rules move by team update, and the manual
is the only authority.

### CAD fidelity — the parts a design is made of

The spec says a robot is a three-stage cascade elevator on MK4i modules. `services/analysis/
app/services/frc_cad.py` says what that is *made of*: every tube with its section, wall and
length, every plate with its pockets, every hex shaft, flanged bearing, HTD pulley, gear whose
pitch diameter follows from its tooth count, belt, wheel and motor — each positioned in one
coordinate system (inches, +X right, +Y up, −Z toward the front, origin at the frame centre on
the bellypan). A full robot comes out at roughly 200 features across a dozen assemblies.

One tree drives everything downstream: `viewer.js` renders it part for part with a real section
plane and per-assembly explode, the dossier lists the assembly breakdown, and `cut_list()` rolls
the tube features into a cut list split into **stock sections you can order** and **fabricated
sections you cannot**. The model is trained on the same tree — the `cad_geometry` and `cad_qa`
corpus families are generated from `frc_cad` itself, so the geometry the model learns to emit is
byte-identical to the geometry the viewer reads. `pytest services/analysis/app/tests/test_cad.py`
checks the properties consumers depend on: every feature positioned, structural members on
catalog sections, derived dimensions consistent with their inputs, and the tree tracking the spec.

> This is **dimensioned concept geometry**, not manufacturing CAD. The numbers are consistent
> with each other; none of them has been checked against a vendor drawing, a stress case or the
> current manual. Do not machine from it.

## Repository

```
apps/web              Next.js dashboard, findings UI, power tree, chat, reports
services/analysis     FastAPI: parsers, rule engine, power analysis, retrieval, chat
services/inference    Self-hosted model server (llama.cpp / Transformers / vLLM providers)
services/training     Training-data pipeline + LoRA/QLoRA fine-tuning scripts
services/evaluation   Domain benchmark suite (rules vs base vs fine-tuned)
packages/shared-types Shared JSON Schemas + TypeScript types
examples/             8 labeled example circuits with expected findings
training/configs      Fine-tuning configs (small-local, lora, qlora, production, FRC)
datasets/ · models/   Versioned data and model artifacts (not in git)
docs/                 Architecture, scope, model/training/evaluation strategy, deployment
```

## Quick start (local, no GPU, no Docker)

```bash
./scripts/setup.sh            # venv + service deps + npm install + .env
npm run dev:inference         # self-hosted model server :8001 (stub provider by default)
npm run dev:analysis          # analysis API :8000 (SQLite + local storage)
npm run dev:web               # web app :3000
```

Then open http://localhost:3000, create a project, and upload one of the
[examples](examples/) (e.g. `01-led-no-resistor`).

Without model weights, the inference service runs its deterministic **stub provider** — the
UI labels this clearly; nothing stub-generated is presented as model output. To run a real
model locally, download an open-weight GGUF (e.g. Qwen2.5-1.5B-Instruct Q4_K_M) and set
`KALE_INFERENCE_PROVIDER=local_llamacpp`, `KALE_MODEL_PATH=...` — see
[docs/local-gpu-setup.md](docs/local-gpu-setup.md).

## Tests

```bash
npm run test:py               # pytest: parsers, rules, power, API, inference
npm run test:web              # vitest component tests
npm run check                 # lint/format, tests, typecheck, production web build
```

## Documentation

| Doc | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | System design, data contracts, API table |
| [docs/mvp-scope.md](docs/mvp-scope.md) | What's in/out, success criteria |
| [docs/model-strategy.md](docs/model-strategy.md) | Base-model evaluation, licenses, serving plan |
| [docs/training-data-strategy.md](docs/training-data-strategy.md) | Dataset sources, JSONL schema, consent gates |
| [docs/robotics-synthesis-roadmap.md](docs/robotics-synthesis-roadmap.md) | Roadmap to the robotics-synthesis model: data pipeline → training → validation |
| [docs/evaluation-plan.md](docs/evaluation-plan.md) | Domain benchmarks, metrics, promotion criteria |
| [docs/training-guide.md](docs/training-guide.md) | End-to-end fine-tuning walkthrough |
| [docs/deployment.md](docs/deployment.md) | Docker compose, production setup |
| [docs/local-gpu-setup.md](docs/local-gpu-setup.md) | Running real models locally / on GPU |
| [TASKS.md](TASKS.md) | Phase-by-phase implementation status |
