FROM node:22-bookworm-slim AS web-builder

WORKDIR /app
COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/package.json
COPY packages/shared-types/package.json packages/shared-types/package.json
RUN npm ci

COPY apps/web apps/web
COPY packages/shared-types packages/shared-types
ENV NEXT_PUBLIC_USE_SAME_ORIGIN=true \
    ANALYSIS_INTERNAL_URL=http://127.0.0.1:8000
RUN npm run build:web

FROM node:22-bookworm-slim AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 python3-venv curl ca-certificates build-essential cmake \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip

WORKDIR /app
COPY services/analysis /app/services/analysis
RUN /opt/venv/bin/pip install --no-cache-dir /app/services/analysis \
    && /opt/venv/bin/pip install --no-cache-dir \
       --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu \
       llama-cpp-python==0.3.16

COPY services/inference /app/services/inference
COPY models /app/models
COPY scripts/space-start.sh /app/scripts/space-start.sh

COPY --from=web-builder /app/package.json /app/package.json
COPY --from=web-builder /app/package-lock.json /app/package-lock.json
COPY --from=web-builder /app/node_modules /app/node_modules
COPY --from=web-builder /app/apps/web /app/apps/web
COPY --from=web-builder /app/packages/shared-types /app/packages/shared-types

RUN mkdir -p /app/models/checkpoints \
    && curl --fail --location --retry 5 --retry-delay 5 \
       https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf \
       --output /app/models/checkpoints/qwen2.5-1.5b-instruct-q4_k_m.gguf \
    && chmod +x /app/scripts/space-start.sh

ENV PORT=7860 \
    NEXT_PUBLIC_USE_SAME_ORIGIN=true \
    ANALYSIS_INTERNAL_URL=http://127.0.0.1:8000 \
    DATABASE_URL=sqlite:////data/kale/kale.db \
    STORAGE_BACKEND=local \
    STORAGE_LOCAL_DIR=/data/kale/storage \
    AUTH_DISABLED=true \
    CORS_ORIGINS=http://localhost:7860 \
    INFERENCE_URL=http://127.0.0.1:8001 \
    INFERENCE_TIMEOUT_SECONDS=240 \
    KALE_INFERENCE_PROVIDER=local_llamacpp \
    KALE_MODEL_PATH=/app/models/checkpoints/qwen2.5-1.5b-instruct-q4_k_m.gguf \
    KALE_MODEL_VERSION=qwen2.5-1.5b-instruct-q4-k-m \
    LOG_DIR=/data/kale/logs

EXPOSE 7860
CMD ["/app/scripts/space-start.sh"]
