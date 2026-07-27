# Kale Forge — Model Strategy

## Constraint

Kale Forge performs **all** AI inference (generation, embeddings, reranking) on infrastructure it
controls. No OpenAI, Anthropic, Gemini, Cohere, hosted Hugging Face inference, or any other
commercial inference API appears anywhere in the product. External SaaS is allowed only for
non-AI infrastructure (Postgres hosting, object storage, auth, analytics, payments).

Kale Forge does **not** train a foundation model from scratch in the MVP. We fine-tune an
open-weight base model into a proprietary specialized model. The proprietary value is the
training dataset, fine-tuning pipeline, rule data, feedback loop, retrieval, benchmarks, and
domain-specific structured outputs — not the base weights.

## Base-model evaluation

Criteria: license, commercial-use restrictions, size options, hardware needs, context length,
structured-output reliability, fine-tuning ecosystem (PEFT/TRL/vLLM support), technical
reasoning, local inference performance (GGUF availability for llama.cpp).

| Family | License | Commercial notes | Sizes | Context | Structured output | Fine-tune support | Verdict |
|---|---|---|---|---|---|---|---|
| **Qwen2.5 / Qwen2.5-Coder** | Apache 2.0 (0.5B–14B, 32B; *not* 3B/72B) | Unrestricted at Apache sizes | 0.5B–32B | 32K–128K | Strong JSON adherence; good grammar-decode results | Excellent (PEFT/TRL/vLLM/GGUF) | **Selected** |
| Llama 3.1/3.2 | Llama Community License | OK below 700M MAU; branding & acceptable-use terms; output-use restrictions | 1B–70B | 128K | Good | Excellent | Strong runner-up; license friction |
| Mistral 7B / Nemo | Apache 2.0 | Unrestricted | 7B, 12B | 32K–128K | Good | Very good | Viable; fewer small sizes for dev |
| DeepSeek-V2/R1 distills | MIT (R1) / DeepSeek license | R1-distills MIT; reasoning-heavy, verbose outputs | 1.5B–70B | 64K+ | Medium (chain-of-thought leaks into JSON) | Good | Reasoning style fights structured output |
| Gemma 2 | Gemma Terms of Use | Commercial OK but use-restriction policy Google may update | 2B–27B | 8K | Good | Good | Short context; mutable terms |

## Selected models

| Role | Model | Why |
|---|---|---|
| Dev / CI inference | **Qwen2.5-0.5B / 1.5B-Instruct** (GGUF via llama.cpp) | Runs on a laptop CPU; same family as production so prompts/adapters transfer |
| Fine-tuning target (MVP) | **Qwen2.5-7B-Instruct** | Apache 2.0, 128K context, strong technical/code reasoning, LoRA fits on a single 24 GB GPU (QLoRA on 12 GB) |
| Scale-up option | Qwen2.5-14B-Instruct / Qwen2.5-Coder-14B | Same license & tokenizer; drop-in when eval shows headroom |
| Embeddings | **BAAI/bge-small-en-v1.5** (MIT), upgrade path bge-m3 | Self-hosted via sentence-transformers; 384-dim, fast on CPU |
| Reranker | **BAAI/bge-reranker-v2-m3** (Apache 2.0) | Self-hosted cross-encoder |

Rationale for Qwen2.5 over Llama: equivalent technical quality at the 7B scale, but Apache 2.0
removes MAU thresholds, branding requirements ("Built with Llama"), and output-use ambiguity —
which matters because we generate synthetic training data with our own hosted model.

Rejected: anything requiring a hosted API; Phi (research-oriented license history, weaker
ecosystem for our stack); Gemma (8K context too small for large netlists).

## Serving plan

| Stage | Stack | Hardware |
|---|---|---|
| Local dev | llama.cpp (`local_llamacpp`) with Q4_K_M GGUF; Ollama acceptable for early experiments only, behind the same provider interface | Any laptop |
| Self-hosted staging | Transformers (`local_transformers`) | 1× 24 GB GPU |
| Production | vLLM (`local_vllm`) with LoRA adapter hot-loading, optional TGI | 1–2× A10/L4/A100 |

The inference service exposes one internal HTTP API regardless of provider; the provider is
configuration (`KALE_INFERENCE_PROVIDER`), never code changes. A deterministic **stub
provider** exists for tests and cold-start dev environments; its responses are derived from
rule findings, labeled `provider: "stub"`, and are never presented as model output.

## Fine-tuning path

1. **SFT with LoRA/QLoRA** on the Kale training format (see `docs/training-data-strategy.md`):
   structured design-review outputs, evidence citation, refusal/uncertainty behavior.
2. **Preference optimization (DPO)** later, using accepted-vs-corrected review pairs from the
   human feedback system.
3. Every checkpoint is evaluated on the domain suite (`docs/evaluation-plan.md`); a model
   version is promoted only when it beats the incumbent on F1 and hallucination metrics.
4. The registry supports rollback: versions are immutable, selection is a pointer.

## Honest positioning

The MVP model is "Kale Review Model vX = Qwen2.5-7B-Instruct + Kale LoRA adapter vX". We say
exactly that in docs and model metadata. We never claim a from-scratch foundation model, and
the UI always shows the model version used for an analysis.
