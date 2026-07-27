# Kale Forge — MVP Scope

## In scope

**Core flow:** create project → upload KiCad ZIP / `.kicad_sch` / `.kicad_pcb` / `.kicad_pro`
/ SPICE (`.net`, `.cir`, `.spice`) / BOM CSV / datasheet PDFs → parse to normalized JSON →
deterministic rule engine → power-tree analysis → self-hosted model review (structured JSON)
→ dashboard, findings UI, chat with evidence, exportable HTML report → feedback capture.

1. **KiCad parsing** — own S-expression tokenizer/parser (not regex-driven); extracts
   components, values, footprints, MPNs, positions/orientation, pins, pin→net mapping, power
   symbols, global/hierarchical labels, traces, vias, zones, board outline/layers, design
   rules, unconnected pins, differential-pair names. Parser warnings are recorded, originals preserved.
2. **Deterministic rule engine** — 40+ modular rules in four categories (connectivity,
   components, power, PCB layout), each with id/version/severity/evidence/fix/confidence/source.
3. **Power analysis** — power tree (sources → regulators → rails → loads), editable current
   estimates, dissipation/efficiency/thermal-risk/battery-runtime estimates that always show
   formulas, assumptions, missing data, and uncertainty. Estimates are never presented as guaranteed.
4. **Component intelligence** — spec database with provenance per field
   (verified/user-provided/extracted/inferred/missing); no invented specs.
5. **Self-hosted model service** — provider abstraction (llama.cpp / Transformers / vLLM),
   registry, versioning, LoRA adapters, structured JSON output, logging, token/latency tracking.
6. **Retrieval** — structured filters + keyword + local BGE embeddings + local reranker.
   Only relevant slices of a project go into prompts.
7. **Training-data pipeline** — JSONL SFT format, synthetic-example generator from rule
   findings and example circuits, consent-gated feedback ingestion, license tracking.
8. **Fine-tuning pipeline** — LoRA/QLoRA SFT scripts (Transformers+PEFT+TRL+Accelerate),
   YAML configs, seeds, checkpointing/resume, MLflow-compatible local tracking.
9. **Evaluation suite** — domain benchmarks (decoupling caps, LED resistors, flyback diodes,
   regulator I/O, floating inputs, footprints, power tree, hallucination resistance,
   uncertainty, citations, JSON validity, safety refusals) comparing rules-only vs base vs
   fine-tuned vs rules+fine-tuned.
10. **Web app** — dashboard, project detail (findings, components, nets, power tree,
    visualizations via React Flow/Recharts), design chat with Evidence sections, report export,
    feedback controls, model-version labels, dark/light mode.
11. **8 labeled example projects** (LED w/o resistor, MCU w/o decoupling, bad regulator caps,
    relay w/o flyback, floating input, wrong footprint, sourceless rail, valid circuit).

## Out of scope (explicitly deferred)

- PCB auto-routing, autoplacement, or design *generation*
- Full electromagnetic / SPICE simulation (we parse SPICE netlists; we never run simulators)
- Training a foundation model from scratch
- Altium/Eagle/OrCAD parsers (KiCad + SPICE only)
- Recreating the KiCad editor; visualizations are graphs/tables, not a board viewer
- Multi-tenant billing; payments integration
- Automated datasheet parameter extraction beyond storing PDFs and indexing text sections

## Non-negotiable behaviors

- Rules run before and independently of the model; AI never replaces deterministic checks.
- Model output is schema-validated and citation-checked; uncited claims are dropped.
- The product never claims a design is safe/production-ready or standards-compliant
  (UL/CE/FCC/IEC/ISO/IPC/automotive/medical) — it recommends professional review, with
  stronger warnings for mains/high-voltage/lithium/medical/automotive/aerospace/RF-power designs.
- Global disclaimer shown in app and reports:
  > "Kale Forge provides automated design-review assistance and may miss errors or produce
  > incorrect recommendations. It is not a substitute for professional electrical engineering
  > review, laboratory testing, simulation, regulatory certification, or manufacturer design
  > guidance."

## Success criteria for the MVP

1. Upload any of the 8 example projects → correct expected rule findings (automated tests).
2. End-to-end analysis completes locally with no GPU (llama.cpp small model or stub).
3. Fine-tuning pipeline runs on the seed dataset (LoRA, single GPU) and produces an adapter.
4. Evaluation harness reports precision/recall/F1, JSON validity, citation accuracy, and
   unsupported-claim rate for rules-only vs base vs fine-tuned configurations.
5. Report export contains findings, power analysis, evidence, assumptions, limitations,
   model + rule-engine versions, and the disclaimer.
