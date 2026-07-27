# Kale Forge Design Model — adapter v7

LoRA adapter specializing `mlx-community/Qwen3-4B-Instruct-2507-4bit` into the Design Studio's
intent model. v7 is the first iteration focused on **mechanism depth**, specifically a much more
detailed intake, on top of v6's drivetrain/hardware grounding.

## What changed from v6

All changes are data-side, derived from the same modules the runtime uses (no train/serve drift):

1. **Intake vocabulary 5 → 10** in `robot_spec.INTAKE_TYPES`: added `dual-roller over-bumper`,
   `pivoting compliant-wheel intake`, `ground-to-feeder tunnel intake`,
   `horizontal series-roller intake`, `active floor sweeper with indexer`, each with prompt
   keyword mappings.
2. **+5 detailed intake techniques** (`frc_robot_knowledge.TECHNIQUES["intake"]`): surface-speed
   beats approach speed; center distance sets compression (not eyeballing); durometer picks the
   wheel; two-roller handoff into an indexer; over- vs under-bumper geometry.
3. **+3 intake diagnoses**, **+3 catalog intake parts** (compliant wheel, roller tube, static
   intake gearbox → catalog STRUCTURE 10 → 13), **+1 intake CAD reference**.
4. **Richer `spec.intake` block** (roller_count, roller_center_distance_in, compliant_wheel,
   gear_reduction, computed roller_surface_speed_fps, deploy + hard stops, indexer, power_path) —
   surfaced in the dossier and the per-design 3D viewer.

Corpus `design-v7`: 2,720 rows (design_intent 1,800 · parts_qa 66 · electrical 320 · technique 39
· diagnosis 15 · merged design-v2 480). Training mirrors v6: LoRA, 600 iters, num_layers 6,
batch 1 × grad-accum 4, lr 1e-4, max_seq 2048, mask_prompt. **Final validation loss 0.085.**

## Eval (8 stress prompts, greedy, intent JSON)

| Metric | v6 | **v7** |
|---|---|---|
| Valid JSON | 8/8 | **8/8** |
| Distinct designs | 8/8 | **8/8** |
| **Intake mapping** (named intake → correct type) | 5/7 | **7/7** |
| Enum-clean (strict exact-match) | 5/8 | 4/8 |

The headline: **v7 maps every named intake correctly (7/7)**, including the five types it newly
learned. v6 scores 5/7 because it never saw `ground-to-feeder tunnel` or `series-roller` /
`sweeper` intakes and falls back to `under-bumper roller`. v7 also correctly handles
`pivoting compliant-wheel`, `dual-roller over-bumper`, and `fixed over-bumper roller`.

## Honest limitations

- **Enum discipline is a wash, not an improvement.** Both models emit near-miss enum strings under
  the strict exact-match check ("under-bumper roller intake" vs the enum "under-bumper roller",
  "turret shooter", "deep-cage hook", `arm_type=cascade`). The runtime's `_sanitize_intent` /
  `_pick_type` clamp these to valid enums in production, but the raw model is not violation-free —
  the same frontier v5/v6 flagged. The larger intake vocabulary slightly widened the surface for
  these near-misses.
- Produces design intent, not native CAD. Never claims rule compliance or structural safety.
- Trained off-repo on local disk (`/private/tmp`, MLX env) to dodge iCloud read-latency; artifacts
  copied back to `models/adapters/kale-design-qwen3-4b-v7`.

## Provenance

`training/configs/mlx-design-4b-v7.yaml`, corpus `datasets/processed/design-v7`
(`kale_training.generate_frc_corpus`, seed 7001). No vendor prose, CAD, meshes or user data copied.
