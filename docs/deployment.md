# Kale Forge deployment

Kale runs its model, retrieval, and analysis stack on infrastructure you control. The supplied
Compose file is a single-host deployment reference, not a substitute for a network, backup,
or secret-management policy.

## Before starting

Copy the environment template and set non-default secrets:

```bash
cp .env.example .env
```

Set at least these values in `.env`:

```dotenv
POSTGRES_PASSWORD=<long-random-password>
ADMIN_TOKEN=<long-random-admin-token>
BOOTSTRAP_ADMIN_EMAIL=admin@example.com
BOOTSTRAP_API_TOKEN=<long-random-api-token>
AUTH_DISABLED=false
CORS_ORIGINS=https://kale.example.com
NEXT_PUBLIC_ANALYSIS_API_URL=https://api.kale.example.com
```

`BOOTSTRAP_API_TOKEN` is only for the first administrator. It creates no account when empty.
Use it as `Authorization: Bearer <token>` for normal API calls; use `X-Admin-Token` only for
model and training administration. Rotate both values through your secret manager; the
configured bootstrap administrator is updated at service startup. Do not expose port 8000 directly to the public internet; terminate TLS and apply
request-size/rate limits at a reverse proxy.

## Start the stack

```bash
docker compose up --build -d
docker compose ps
curl http://localhost:8000/health
curl http://localhost:8001/health
```

The web UI is on port 3000, analysis on port 8000, and inference on port 8001. Compose leaves
the inference server on the deterministic `stub` provider until a self-hosted model is mounted
and configured. See [local-gpu-setup.md](local-gpu-setup.md) for provider settings.

Postgres and uploads use named volumes. Back up `postgres-data` and `analysis-data` together;
the database records reference the stored original files. Redis is provisioned for future worker
deployment, but the included Compose profile deliberately uses the reliable in-process queue.
Run more than one analysis replica only after deploying a durable worker implementation.

## Production checklist

- Keep `AUTH_DISABLED=false`; the service will require a bearer token.
- Set a public `CORS_ORIGINS` value and a matching browser-visible
  `NEXT_PUBLIC_ANALYSIS_API_URL` before building the web image.
- Use an S3-compatible upload backend for multi-host deployment and limit credentials to the
  project bucket/prefix.
- Mount model weights read-only, restrict inference to the private network, and monitor
  `/v1/stats` plus service logs.
- Back up Postgres and object storage, test restore, pin image versions, and put the services
  behind TLS/rate limiting.
- Do not use generated review output as a safety or compliance decision.
