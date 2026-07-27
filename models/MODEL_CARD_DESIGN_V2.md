# Kale Forge Design Model v2

## Status

Guarded candidate. Approved for schema-validated preview use only; not an autonomous source of
fabrication, structural-safety, electrical-safety, certification, or current-season FRC compliance
claims.

## Base and adapter

- Base: `mlx-community/Qwen2.5-1.5B-Instruct-4bit`
- Adapter: `models/adapters/kale-design-v2/adapters.safetensors`
- Method: 4-bit LoRA, 8 adapted layers, prompt masking, gradient checkpointing
- License: Apache-2.0 base; internal synthetic/template-generated adapter corpus

## Corpus

- Version: `datasets/processed/design-v2`
- 480 conversations: 288 FRC and 192 broader hardware designs
- Coverage: FRC mechanisms and chassis, PCBs, robotic motion, gantries, thermal hardware,
  molded and fabricated enclosures, fixtures, mobile robots, gimbals, and lifting mechanisms
- No copied third-party CAD, meshes, prose, or user uploads
- Deterministic generation seed and SHA-256 split hashes are in `manifest.json`

## Evaluation

| Gate | Result |
| --- | ---: |
| Untuned 1.5B original benchmark overall | 0.056 |
| v2 held-out generated set overall (12 prompts) | 0.986 |
| v2 held-out JSON validity | 12/12 |
| v2 held-out required-contract coverage | 1.000 |
| v2 independent hand-written stress overall (6 prompts) | 0.577 |
| v2 independent stress JSON validity | 5/6 |

The hand-written stress set is the release constraint. A response must pass JSON parsing, required
top-level-key validation, repetition detection, and unsafe-claim checks. Invalid or incomplete model
output must fall back to the deterministic planner; it must not be shown as a completed design.

## Rejected experiment

`Qwen3-4B-Instruct-2507` LoRA was trained and evaluated but rejected: independent stress JSON
validity was 1/6 and overall score was 0.143. The larger parameter count did not compensate for its
structured-output regression under this corpus and decoder.

## Known limitations

- This model generates engineering plans, not finished CAD geometry, PCB layout, simulation, or test evidence.
- Exact dimensions and component selections remain provisional until interfaces, loads, environment,
  fabrication capability, and current official rules are supplied and verified.
- FRC legality must be checked against the current official manual.
- High-consequence designs require analysis, prototyping, controlled testing, and qualified review.

