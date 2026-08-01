# Free Lightning AI v18 training

The account owner must first create or sign in to a Lightning AI account and complete phone
verification. Use the free CPU while installing dependencies, then switch to an interruptible
T4 GPU only for training and evaluation.

From a Studio terminal in the repository root:

```bash
python -m pip install -r training/requirements-cuda-v18.txt
python services/training/scripts/train_sft.py \
  --config training/configs/qlora-design-4b-v18.yaml \
  --dry-run
```

After the dry run passes, switch the Studio to an interruptible T4 and run:

```bash
python services/training/scripts/train_sft.py \
  --config training/configs/qlora-design-4b-v18.yaml
```

The run is bounded to 600 optimizer steps and saves resumable checkpoints every 100 steps in
`models/adapters/kale-design-qwen3-4b-v18`. Stop the GPU after the final adapter is saved.
