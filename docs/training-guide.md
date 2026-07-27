# Kale Forge training guide

Training is offline and opt-in. Only use data whose license, review status, and consent permit
model training. The pipeline rejects examples containing obvious secrets before export.

## Build a seed dataset

From the repository root:

```bash
PYTHONPATH=services/analysis:services/training \
  .venv/bin/python -m kale_training.generate_synthetic \
  --out datasets/synthetic
```

Validate and split the resulting JSONL before training. Dataset exports create a content-hashed
manifest with the split seed, source mix, and row count; retain this next to the immutable
dataset version.

Feedback must have `consent_to_train=true` before it can be promoted. The analysis API’s
`/training/export` endpoint is admin-gated and preserves each example’s provenance.

## Dry-run and train

```bash
PYTHONPATH=services/analysis:services/training \
  .venv/bin/python services/training/scripts/train_sft.py \
  --config training/configs/lora-default.yaml --dry-run
```

On a GPU host, remove `--dry-run`, set the dataset version/path in the selected YAML config,
and pass `--resume-from` for a compatible checkpoint. The script records the exact config,
seed, model, dataset version, and output adapter. Use the `qlora-default` config for constrained
VRAM and `production` only after a full evaluation run passes promotion criteria.

## Evaluate before promotion

```bash
PYTHONPATH=services/analysis:services/training:services/evaluation \
  .venv/bin/python -m kale_eval run --config rules+finetuned
```

Compare the generated report with the incumbent using `python -m kale_eval compare`. Promote
only when the criteria in [evaluation-plan.md](evaluation-plan.md) are met; in particular, do
not trade safety refusal, citation accuracy, or structured-output validity for a small F1 gain.

## Design-model iterations on this Mac

The design adapters (v6 → v9) are trained with MLX rather than the script above. The whole
loop, using v9 as the worked example:

```bash
# 1. Corpus. The generator hardcodes services/analysis onto sys.path, whose security.py drags
#    in FastAPI; pre-import the stdlib-only copies from apps/kale-demo so it resolves to those
#    instead. /private/tmp/gen_v9.py is the wrapper. Takes seconds.
/usr/local/bin/python3 /private/tmp/gen_v9.py

# 2. Train, on LOCAL DISK. Copy dataset + config to /private/tmp, point the config at them.
cd /private/tmp/kale-train-v9
/usr/local/bin/python3 -m mlx_lm.lora --config config.yaml

# 3. Score the adapter against the incumbent.
/usr/local/bin/python3 services/evaluation/kale_eval/eval_cad_adapter.py \
  --adapters models/adapters/kale-design-qwen3-4b-v7 \
             models/adapters/kale-design-qwen3-4b-v9 \
  --out models/evaluations/kale-design-v9-cad.json
```

**Never read a dependency tree off `~/Desktop`.** It is iCloud-synced, and evicted files
re-download at roughly 6 MB per 10 minutes with the process pinned at 0% CPU and *shrinking*
RSS — which looks exactly like a hang. A v9 training run sat for 26 minutes without importing
off `work/mlx-training-env`; `pip install mlx-lm` into `/usr/local/bin/python3`, whose
site-packages are on local disk, took 12 seconds and gave the identical mlx-lm 0.31.3. The
same applies to pytest/pydantic/httpx when running the analysis tests, and to serving
`apps/kale-demo` locally — copy it to `/private/tmp` first. Model weights under
`~/.cache/huggingface` are outside iCloud and load normally.

**Keep the corpus reproducible.** Every CAD family target is generated from `frc_cad.py`, so
any change to that module changes the training data. After touching it, re-run the generator
into a scratch directory and diff `manifest.json`'s `sha256` against the shipped corpus — if
they differ, either revert the change or regenerate and retrain. Adding a *new* key to
`cut_list()` rows is safe; changing its sort order is not, because the `cad_qa` family prints
the first six rows.
