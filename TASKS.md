# Kale Forge — Implementation Tasks

Status legend: [ ] todo · [x] done · [~] partial (limitation documented)

## Phase 0 — Planning (done first, per development requirements)
- [x] Inspect repository (empty `Kale/` dir confirmed as project root)
- [x] docs/architecture.md — system design + module contracts
- [x] docs/mvp-scope.md — in/out of scope, success criteria
- [x] docs/model-strategy.md — open-weight model evaluation, license notes, selection (Qwen2.5)
- [x] docs/training-data-strategy.md — sources, JSONL schema, pipeline, consent gates
- [x] docs/evaluation-plan.md — domain benchmarks, metrics, promotion criteria
- [x] TASKS.md (this file)
- Missing dependencies identified: Docker CLI absent locally (compose files provided for prod);
  pnpm absent (npm workspaces used); GPU absent (llama.cpp/stub path for local dev).

## Phase 1 — Foundation
- [x] Monorepo setup (npm workspaces, root scripts, `.env.example`, setup/check scripts)
- [x] Next.js frontend shell (layout, theme, nav, dark/light)
- [x] Database models (SQLAlchemy authoritative) — all 21 entities
- [x] Project creation + listing API & UI
- [x] File upload (multipart, validation, sanitization, ZIP safety) + local/S3 storage abstraction
- [x] FastAPI analysis service skeleton (config, DB session, routers, error handling)
- [x] KiCad S-expression tokenizer + parser (`sexpr.py`)
- [x] `.kicad_sch` / `.kicad_pcb` / `.kicad_pro` parsers
- [x] SPICE netlist parser (.net/.cir/.spice) + BOM CSV parser
- [x] Normalizer → NormalizedProject JSON

## Phase 2 — Deterministic analysis
- [x] Rule engine core (base classes, registry, NetGraph helpers, versioning)
- [x] Connectivity rules (9) · Component rules (12) · Power rules (9) · PCB rules (12)
- [x] Findings API + findings UI (severity badges, evidence expanders)
- [x] 8 example projects with normalized JSON + expected findings + ideal answers + labels
- [x] Automated rule tests (pytest) against examples

## Phase 3 — Self-hosted AI
- [x] Inference service (FastAPI): provider abstraction, llama.cpp/Transformers/vLLM providers,
      stub provider for tests, registry, adapters, logging, token/latency tracking
- [x] Local embeddings (BGE via sentence-transformers, hash fallback) + keyword + rerank retrieval
- [x] Structured AI review (schema validation, citation gate, repair fallback)
- [x] Design chat with Evidence sections
- [x] Prompt assembly with injection isolation

## Phase 4 — Training
- [x] Training-example schema + JSONL writer/validator
- [x] Synthetic-example generator (fault-injection mutations of example circuits)
- [x] Feedback collection API/UI + consent-gated promotion script
- [x] SFT scripts (Transformers/PEFT/TRL/Accelerate) with LoRA + QLoRA
- [x] training/configs: small-local.yaml, lora-default.yaml, qlora-default.yaml, production.yaml
- [x] Dataset versioning (manifests, splits, seeds, checkpoint/resume, MLflow-local tracking)

## Phase 5 — Evaluation & registry
- [x] Domain evaluation suite (13 categories, metrics per evaluation-plan.md)
- [x] rules-only vs base vs finetuned vs rules+finetuned comparison harness
- [x] Model registry + version selection API (admin) + rollback
- [x] Evaluation dashboard UI

## Phase 6 — Product completion
- [x] Power-tree analysis + editable current estimates + calculations (formulas/assumptions shown)
- [x] Visualizations (component/net tables, severity breakdown, power tree, component-net graph via React Flow, dataset/eval charts via Recharts)
- [x] HTML report export (printable; PDF via browser print)
- [x] Security review pass (upload paths, injection isolation, user ownership, bootstrap admin, secret scan)
- [~] Test sweep (pytest, vitest, Playwright smoke) + lint/format/typecheck
- [x] Docker compose (postgres, redis, services, web) + deployment docs + local GPU setup guide

## After each phase
Run format → lint → typecheck → tests; fix errors before continuing; document unresolved
limitations at the bottom of this file.

## Known limitations (running list)
- No Playwright browser-smoke suite is included; API, unit, and component tests cover the MVP flow.
- Redis is provisioned for production infrastructure, but the included Compose deployment uses the
  in-process analysis queue. Do not horizontally scale analysis workers until a durable worker is deployed.
- Datasheet PDFs are stored as evidence but are not automatically converted into component specifications.
- Local validation was blocked by a host-level Python import read stall and Docker CLI is not installed;
  run `npm run check` and `docker compose config` on the target development host before release.
