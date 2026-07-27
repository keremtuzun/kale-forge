# Kale Forge — Training-Data Strategy

The dataset is the moat. Every example is structured, licensed, provenance-tracked, and
review-gated before it can enter a training split.

## Sources (in priority order)

| Source | How | License field | Review gate |
|---|---|---|---|
| Synthetic circuits | `services/training/scripts/generate_synthetic.py` mutates known-good templates to inject specific faults (remove LED resistor, drop decoupling cap, float an input, …), runs the rule engine to produce ground truth, and emits instruction/response pairs | `internal` | auto-generated → spot-review sample |
| Manually reviewed circuits | The 8 `examples/` projects and future curated boards, with expert-written ideal answers | `internal` | must be `reviewed: true` |
| Deterministic rule findings | Rule outputs become `confirmed_findings` labels — the model learns to restate, prioritize, and explain them, not to re-derive them | `internal` | automatic |
| Expert-written explanations | Engineers write ideal answers for hard cases and failure cases found by evaluation | `internal` | reviewer sign-off |
| Public engineering examples / open-source KiCad projects | Only licenses on the allowlist (CC0, CC-BY, MIT, Apache-2.0, TAPR/CERN-OHL where text reuse permitted); license recorded per example | actual SPDX id | license validation + review |
| User corrections | Only with explicit per-item consent; stored separately from training data until promoted | `user-consented` | consent → PII/secret scan → dedup → review |
| Verified component information | Fields marked `verified` in the component DB | `internal` | verification status check |
| FRC parts + techniques corpus | `services/training/kale_training/generate_frc_corpus.py` derives examples from Kale's own catalog (`app/services/frc_parts.py`) and technique library (`app/services/frc_robot_knowledge.py`) | `internal` | generated from a reviewed catalog; regenerate after any catalog change |

### FRC parts-and-techniques corpus

Generated from the *same modules the runtime uses*, so training data and runtime knowledge
cannot drift apart. Regenerate it whenever the catalog changes:

```
.venv/bin/python -m services.training.kale_training.generate_frc_corpus \
    --out datasets/processed/design-v3 --merge datasets/processed/design-v2
```

Five families:

| Family | What it teaches | Why it exists |
|---|---|---|
| `design_intent` | Team request → one schema-valid design intent JSON | Mirrors the Design Studio's actual inference call byte for byte (`robot_spec.SYSTEM_DESIGN` + `INTENT_SCHEMA`). This is the family that makes different prompts produce different robots |
| `parts_qa` | Envelope, mass, ratios, channels and mounting for every catalog part | So the model names real hardware instead of "a motor controller" |
| `electrical` | Channel assignment, breaker sizing, wire gauge, bus sag | Kale's electrical strength applied to the robot side |
| `technique` | Why a build technique exists and what it prevents | Design judgement, not just part lookup |
| `diagnosis` | Competition symptom → likely cause and fix | The questions teams actually ask at 2 am |

`--merge` folds in the general design corpus so the model keeps its broader hardware ability;
training on FRC alone would trade one narrowness for another.

## Example format (JSONL, one object per line)

`packages/shared-types/schemas/training_example.schema.json` is authoritative:

```json
{
  "instruction": "Review this LED circuit for design issues.",
  "input": {
    "project": {"name": "…", "source_format": "kicad"},
    "components": [], "nets": [], "rule_findings": [],
    "power_tree": null, "question": null
  },
  "output": {
    "summary": "", "confirmed_findings": [], "possible_findings": [],
    "recommendations": [], "evidence": [], "questions_for_engineer": [],
    "confidence": 0.0, "limitations": []
  },
  "metadata": {
    "id": "…", "source": "synthetic|manual|rules|expert|public|user_feedback",
    "license": "internal", "reviewed": true, "safety_class": "general|power|safety_critical",
    "assumptions": [], "missing_information": [], "created_at": "…", "dataset_version": "…"
  }
}
```

Design decisions baked into the format:

- **Output mirrors the production AIReview schema** so SFT directly teaches the runtime
  contract (JSON validity and citation discipline are trained, not just prompted).
- **`confirmed_findings` must be a subset of `input.rule_findings`** — the generator enforces
  this, teaching the model never to promote its own hypotheses to confirmed status.
- **Negative/uncertainty examples are mandatory**: ≥15% of the dataset is (a) clean circuits
  where the ideal answer reports no issues, (b) under-specified circuits where the ideal
  answer asks `questions_for_engineer` instead of guessing, and (c) safety-critical prompts
  where the ideal answer refuses to certify and recommends professional review.
- **Every claim in `output` carries evidence** referencing real refs/nets/rule ids from `input`.

## Pipeline stages (`services/training`)

1. **Collect** → `datasets/raw/` (immutable originals, provenance JSON sidecars)
2. **Filter** → secret scan (regex + entropy), PII scan, license allowlist check; failures quarantined
3. **Normalize** → convert to the JSONL schema; schema-validate every row
4. **Deduplicate** → content-hash on (input, instruction) pairs
5. **Review** → `reviewed: false` rows are excluded from all training splits
6. **Split** → deterministic train/val/test by hash (80/10/10), stratified by benchmark category
7. **Version** → `datasets/processed/<version>/` with manifest (row counts, source mix,
   content hash); DVC tracks the directory; `DatasetVersion` DB row records lineage

## Feedback loop

`UserFeedback` rows (ratings, corrected explanations, corrected fixes, reported misses) live
in their own table, never auto-flow into training. Promotion path: explicit consent flag →
privacy filtering → human review → dedup → license validation → new dataset version. The
promotion script is `services/training/scripts/promote_feedback.py`.

## What we refuse to include

- Anything from external AI APIs (would violate the no-external-model constraint and poison provenance)
- Unlicensed or NC-licensed third-party content
- Uploaded user projects without explicit consent
- Examples whose "ideal answer" asserts safety/compliance claims
