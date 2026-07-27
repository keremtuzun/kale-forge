# Kale Forge — Architecture

Kale Forge is a specialized AI design-review platform for electrical engineers. It analyzes
schematics and PCB projects, detects likely design mistakes, explains circuit behavior, and
helps engineers fix issues faster using a proprietary fine-tuned hardware-engineering model.

**Hard constraint:** no external hosted AI APIs. All inference, embeddings, and reranking run
on infrastructure Kale Forge controls (locally during development, self-hosted GPU in production).

## System overview

```
┌────────────────────┐        ┌───────────────────────────────┐
│  apps/web          │  HTTP  │  services/analysis (FastAPI)   │
│  Next.js dashboard ├───────►│  upload · parse · rules ·      │
│  chat · reports    │        │  power · retrieval · chat ·    │
└────────────────────┘        │  reports · feedback · registry │
                              └──────────────┬────────────────┘
                                             │ HTTP (internal)
                              ┌──────────────▼────────────────┐
                              │  services/inference (FastAPI)  │
                              │  self-hosted model server      │
                              │  providers: local_llamacpp,    │
                              │  local_transformers, local_vllm│
                              └──────────────┬────────────────┘
                                             │ loads
                              ┌──────────────▼────────────────┐
                              │  models/  (base + LoRA         │
                              │  adapters, versioned registry) │
                              └────────────────────────────────┘

services/training    — dataset builder, SFT/LoRA/QLoRA scripts (offline)
services/evaluation  — domain benchmark harness (offline)
```

## Repository layout

```
apps/web                  Next.js 15 + TypeScript + Tailwind + shadcn-style UI + React Flow + Recharts
services/analysis         FastAPI: parsing, rule engine, power analysis, retrieval, chat orchestration
services/inference        FastAPI: self-hosted model server (provider abstraction, registry, adapters)
services/training         Training-data pipeline + fine-tuning scripts (Transformers/PEFT/TRL)
services/evaluation       Domain evaluation suite (rules vs base vs fine-tuned)
packages/shared-types     JSON Schemas + TypeScript types shared web ↔ services
datasets/                 raw / processed / reviewed / synthetic training data (DVC-tracked)
training/configs          small-local.yaml, lora-default.yaml, qlora-default.yaml, production.yaml
models/                   adapters/, checkpoints/, evaluations/ (never committed to git)
examples/                 8 labeled example circuits (source + normalized JSON + expected findings)
docs/                     architecture, scope, model strategy, training, evaluation, deployment
```

## Core data contracts

These are the load-bearing interfaces. All modules import them; do not fork them.

### 1. NormalizedProject (`services/analysis/app/models/normalized.py`, mirrored in `packages/shared-types`)

Every parser (KiCad schematic, KiCad PCB, SPICE, BOM) emits this single structure:

```jsonc
{
  "project": { "name": "", "source_format": "kicad|spice|mixed", "files": [], "parser_warnings": [] },
  "components": [ { "reference": "R1", "value": "10k", "footprint": "", "mpn": null,
                    "lib_id": "", "dnp": false, "position": {"x":0,"y":0,"rotation":0},
                    "pins": [ {"number":"1","name":"","type":"passive","net":"VCC"} ],
                    "fields": {}, "source_file": "" } ],
  "nets": [ { "name": "VCC", "pins": [{"component":"R1","pin":"1"}],
              "is_power": true, "is_ground": false, "inferred_voltage": 5.0 } ],
  "board": { "width_mm": null, "height_mm": null, "outline": [], "layers": [],
             "traces": [], "vias": [], "zones": [], "design_rules": {} },
  "power_symbols": [], "global_labels": [], "hierarchical_labels": [],
  "unconnected_pins": [ {"component":"U1","pin":"7"} ]
}
```

### 2. RuleFinding (`services/analysis/app/rules/base.py`)

Every deterministic rule returns `RuleFinding` objects: `rule_id`, `rule_version`, `category`
(connectivity | components | power | pcb), `severity` (info | warning | error | critical),
`title`, `description`, `affected_components`, `affected_nets`, `evidence[]`,
`suggested_fix`, `confidence` (0–1), `source` (engineering provenance of the rule).

Rules are one-class-per-file under `app/rules/{category}/`, discovered by
`app/rules/registry.py`. Rules never call the model; the model never replaces rules.

### 3. AIReview (`services/analysis/app/models/ai_review.py`)

The model must return this JSON and nothing else; responses are schema-validated before
display or storage, with one repair retry, then rejected:

```json
{ "summary": "", "confirmed_findings": [], "possible_findings": [], "recommendations": [],
  "questions_for_engineer": [], "evidence": [], "confidence": 0.0, "limitations": [] }
```

`confirmed_findings` may only restate deterministic rule findings; anything model-originated
goes to `possible_findings`. Every entry must cite component references/net names that exist
in the project, or it is dropped by the validator (hallucination gate).

### 4. TrainingExample (`packages/shared-types/schemas/training_example.schema.json`)

JSONL rows: `instruction`, `input` (normalized data + rule findings), `output` (AIReview
shape), `metadata` (source, license, reviewed, safety classification).

## services/analysis — module contracts

```
app/parsers/    sexpr.py            S-expression tokenizer+parser (no regex-only parsing)
                kicad_sch.py        parse_schematic(text, filename) -> partial NormalizedProject
                kicad_pcb.py        parse_pcb(text, filename)       -> board data
                kicad_pro.py        parse_project_file(text)        -> design rules/meta
                spice.py            parse_spice(text, filename)     -> components + nets
                bom.py              parse_bom_csv(text)             -> component field enrichment
                zip_safe.py         safe ZIP extraction (path-traversal proof, size/type limits)
                normalizer.py       merge parser outputs -> NormalizedProject
app/rules/      base.py, registry.py, helpers.py (NetGraph, power/ground heuristics)
                connectivity/ components/ power/ pcb/   one rule per file
app/calculations/ power_tree.py     build_power_tree(project, overrides) -> PowerTree
                  estimates.py      current/dissipation/efficiency/battery math (formulas + assumptions exposed)
app/retrieval/  embeddings.py       local embedder (sentence-transformers BGE; deterministic
                                    hash fallback for dev — flagged `provider: "hash-fallback"`)
                store.py, keyword.py, rerank.py, retriever.py
app/services/   storage.py          Storage ABC: LocalStorage (dev), S3Storage (prod)
                jobs.py             JobQueue ABC: InProcessQueue (dev), RedisQueue (prod)
                inference_client.py HTTP client to services/inference; stub provider fallback
                                    (deterministic, rule-derived, labeled provider="stub" — never presented as AI)
                analysis_service.py orchestrates parse -> rules -> power -> persist
                chat_service.py     retrieval -> prompt assembly -> inference -> validate
                report.py           HTML report generation
app/models/     normalized.py (pydantic) · db.py (SQLAlchemy) · schemas.py (API DTOs) · ai_review.py
app/api/        routers (see API table below)
```

### Database

PostgreSQL in production, SQLite locally (`DATABASE_URL`). SQLAlchemy models are the source
of truth for service-owned tables. The web app talks only to the analysis API, so it does not
need a second ORM schema. Tables: User, Project, UploadedFile, AnalysisJob,
Component, Net, Connection, RuleFinding, ModelFinding, AIReview, ChatConversation, ChatMessage,
ComponentSpecification, ExportedReport, UserFeedback, TrainingExample, DatasetVersion,
ModelVersion, FineTuningRun, EvaluationRun, EvaluationResult.

### API surface (analysis service, prefix `/api`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/projects` | create project |
| GET | `/projects` | list projects (+status, counts, versions) |
| GET/DELETE | `/projects/{id}` | detail / delete |
| POST | `/projects/{id}/files` | upload (multipart; ZIP or individual files) |
| POST | `/projects/{id}/analyze` | start analysis job |
| GET | `/projects/{id}/jobs/{job_id}` | job status |
| GET | `/projects/{id}/data` | normalized project JSON |
| GET | `/projects/{id}/findings` | rule + model findings |
| GET/PATCH | `/projects/{id}/power` | power tree / edit current estimates |
| POST | `/projects/{id}/chat` | ask a question (returns answer + evidence) |
| POST | `/findings/{id}/feedback` | rate/correct a finding |
| PATCH | `/components/{id}` | update component metadata/specs |
| POST | `/projects/{id}/report` | generate HTML report |
| GET | `/models` · POST `/models/select` | model registry (select = admin) |
| GET | `/evaluations` | evaluation results |
| POST | `/training/export` · `/training/finetune` · GET `/training/runs/{id}` | admin only |

Admin routes require `X-Admin-Token: $ADMIN_TOKEN`. `AUTH_DISABLED=true` bypasses user auth
locally, never admin-token checks.

## services/inference — self-hosted model server

Provider abstraction (`app/providers/base.py`): `generate(prompt, json_schema, params) ->
{text, usage, latency_ms, model_version}`. Providers: `local_llamacpp`, `local_transformers`,
`local_vllm` (points at a Kale-controlled vLLM/TGI process). No external commercial providers
exist in the codebase. Features: model registry + versioning, LoRA adapter loading, structured
JSON generation (grammar/schema-guided where the backend supports it, validate+retry otherwise),
request logging, latency/token tracking, rollback (registry re-points to a previous version).

## Security model

- All uploads validated: extension allowlist, size caps, sanitized filenames, ZIP extraction
  with path-traversal and zip-bomb protection. Uploaded files are stored verbatim, never executed.
- SPICE files are parsed as data only; no SPICE simulator is invoked.
- Prompt-injection defense: uploaded text is only ever placed inside a fenced `PROJECT DATA`
  block in prompts; system instructions are separate; model output is schema-validated and
  citation-checked against real components/nets before display.
- Datasets are scanned for secrets/PII before entering training (`services/training`).
- Secrets via environment only; `.env.example` documents every variable.

## Versioning

Every analysis stores `rule_engine_version` and `model_version`. Every AI answer carries its
model version. Dataset versions are content-hashed (DVC). Fine-tuning runs record config,
seed, dataset version, and base model for reproducibility.
