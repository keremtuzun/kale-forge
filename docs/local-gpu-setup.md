# Running a self-hosted Kale model

The default inference provider is `stub`, which makes deterministic, explicitly labelled
responses without downloading or contacting any external model API. To use a real local model,
run one of the providers below on hardware you control.

## llama.cpp / GGUF

Download a compatible instruct GGUF into a local `models/checkpoints/` directory, then set:

```dotenv
KALE_INFERENCE_PROVIDER=local_llamacpp
KALE_MODEL_PATH=./models/checkpoints/qwen2.5-1.5b-instruct-q4_k_m.gguf
KALE_MODEL_VERSION=qwen2.5-1.5b-local
```

Install `llama-cpp-python` in the inference environment with the correct CPU, CUDA, or Metal
build for the host, then start `npm run dev:inference`. The model must be a licensed open-weight
instruct model approved for your intended use; Qwen2.5-Instruct is the project’s baseline.

## Transformers / LoRA

For a Hugging Face-format base model and optional adapter:

```dotenv
KALE_INFERENCE_PROVIDER=local_transformers
KALE_MODEL_PATH=./models/checkpoints/Qwen2.5-7B-Instruct
KALE_ADAPTER_PATH=./models/adapters/kale-lora-v1
KALE_MODEL_VERSION=kale-lora-v1
```

Install the GPU extras in the inference environment: `transformers`, `torch`, `peft`, and
`accelerate`. Keep weights and adapters outside git and mount them read-only in production.

## vLLM

Start a Kale-controlled vLLM process on the private network and point Kale to its OpenAI-style
compatibility endpoint:

```dotenv
KALE_INFERENCE_PROVIDER=local_vllm
KALE_VLLM_URL=http://vllm.internal:8010/v1
KALE_MODEL_VERSION=kale-production-v1
```

Kale’s vLLM provider only calls the URL specified here. Do not point it at a public or
third-party inference endpoint.

If any provider fails to load or generate, the service falls back to `stub` and records that
provider in every response. Never present a fallback answer as model output.
