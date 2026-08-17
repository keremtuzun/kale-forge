# Serving v18 to the Design Studio for free

The Design Studio at `kaleai.vercel.app` now asks the model for design intent **when one is
reachable**, and builds the design deterministically when one is not. Nothing has to be running
for the site to work — this document is about the optional half.

There is no free always-on GPU. So the model is **opportunistic**: bring it up on the Lightning
AI box when you want designs to be model-guided, point the site at it, and stop it when you are
done. Every request that arrives while it is down silently falls back to the deterministic path,
which is exactly what the site did before this was wired.

## What the site does with it

| INFERENCE_URL | signed-in `/studio?json=1` | public `/studio?fs=1` |
|---|---|---|
| unset | deterministic | deterministic |
| set, service up | **model chooses architecture**, compiler builds geometry | deterministic |
| set, service down or slow | deterministic, reason recorded in `spec.model.reason` | deterministic |

The public FeatureScript endpoint never reaches the model on purpose: it is unauthenticated, and
letting it through would let anyone spend your GPU credits.

Geometry is always deterministic. The model only picks architecture — subsystems, drivetrain
type, mechanism families — and everything it returns is schema-validated and clamped before it
reaches the compiler.

## Bring it up (Lightning AI, free tier)

Phone-verify the account first, install on the free CPU, then switch to an interruptible T4
only while you actually need it — same discipline as `training/LIGHTNING_V18.md`.

```bash
pip install -e "services/analysis[transformers]"      # torch, transformers, peft, lm-format-enforcer

cd services/inference
KALE_INFERENCE_PROVIDER=local_transformers \
KALE_MODEL_PATH=Qwen/Qwen3-4B-Instruct-2507 \
KALE_ADAPTER_PATH=../../models/adapters/kale-design-qwen3-4b-v18-cad-repair-50 \
KALE_MODEL_VERSION=kale-design-qwen3-4b-v18-cad-repair-50 \
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

`lm-format-enforcer` is not optional. The provider refuses to sample a structured request
without it rather than emit unconstrained JSON, so if it is missing every design silently falls
back to deterministic synthesis and the GPU is running for nothing. Confirm with:

```bash
curl -s http://localhost:8001/health        # provider must be "local_transformers", not "stub"
```

Expose port 8001 from the Studio, then set the URL on Vercel:

```bash
vercel env add INFERENCE_URL production           # https://<your-lightning-host>
vercel env add INFERENCE_TIMEOUT_SECONDS production   # 8
cd apps/site && vercel deploy --prod --yes && vercel alias set <deployment> kaleai.vercel.app
```

## Take it down

Stop the GPU. Nothing else is required — the site falls back on its own. Remove `INFERENCE_URL`
only if you want to skip the few wasted seconds per request spent discovering the service is
gone.

## Things worth knowing

- **The exposed port is unauthenticated.** While it is up, anyone with the URL can spend your
  GPU. Keep the URL private and stop the box when you are finished; do not leave it running.
- **Latency is the real constraint.** Vercel Hobby functions cap at 10 s by default, so the
  client budget is 6 s and the fallback fires past it. A warm T4 answers a ~200-token intent in
  roughly 4–8 s; the *first* request after start also pays model load and will almost always
  fall back. Send one throwaway prompt to warm it before you demo anything.
- **Raising the ceiling** needs Fluid compute on the Vercel project (up to 300 s on Hobby). Only
  worth it if you would rather wait than get a deterministic design.
- **Check `spec.model`** in the JSON response to see what actually happened: `used`, `provider`,
  `model_version` and, on fallback, `reason`.
- **CPU-only will not work interactively at 4B.** Measured below. The GPU is what makes the
  transformers path above worth turning on at all.

## What was actually crashing

Two separate defects, neither of them the "llama.cpp is flaky, supervise it" story the first
round of work assumed.

**1. Concurrent access to a binding that is not thread-safe.** One `llama_cpp.Llama` object
cannot be driven from two threads, and FastAPI runs every sync endpoint on a threadpool — so
two overlapping design requests entered `create_chat_completion` at the same time and corrupted
shared decode state. In-process, that aborts the API with it:

```
llama-kv-cache.cpp:791: GGML_ASSERT(!states.empty() || !success) failed
```

**2. An old vendored llama.cpp.** `llama-cpp-python` 0.3.34 bundles its own llama.cpp, and the
original aborts came from inside it — note the path, `vendor/llama.cpp/ggml/src/ggml-cpu/
repack.cpp:4238`, a workspace-sizing bug in the repack GEMM that fires when the thread count
oversubscribes the box. The standalone `llama-server` in `build/llamabin/` is build **10228**,
which does not reproduce it.

Measured, same prompts, same 4-concurrent load:

| arm | result |
|---|---|
| in-process (`local_llamacpp`), 4 concurrent | **died in 3 s, 0 completions** |
| in-process, sequential | survived 24 completions / 517 s |
| **llama-server, 4 concurrent** | **survived 48 completions / 444 s** |

So the fix is not supervision. It is running the current llama.cpp out-of-process, where slots
handle concurrency properly. `LlamaCppProvider` also now serialises on a lock, so the dev path
cannot corrupt itself either — it was never getting parallelism from those overlapping calls,
only corruption.

The earlier thread-count cap was treating a real but secondary problem. It is kept because
oversubscription is still wasteful, not because it was the cure.

## Run the model out-of-process

`llama-cpp-python` runs llama.cpp inside the API worker, so a fault in the C++ takes FastAPI
with it. v18 produced three in one afternoon — two `GGML_ASSERT` aborts in the repack kernel
(`ggml-cpu/repack.cpp:4238`) and one SIGSEGV at 8 threads, *after* the thread cap had already
reduced the rate. Capping threads made them rarer, never absent, and no amount of supervision
stops the API disappearing with the model.

Split in two, the same fault stops being an outage:

```
llama-server :8010   ← the weights, and the process that dies
API          :8001   ← stays up, reports the model unavailable, keeps answering
```

```bash
python scripts/serve-v18.py      # starts and supervises both
```

Provider `local_llamaserver` (`KALE_LLAMA_SERVER_URL`, default `http://127.0.0.1:8010/v1`).
Constrained decoding still uses llama.cpp's own JSON-schema grammar, so the contract is
unchanged — only the transport moved.

**Measured across a kill of llama-server:**

| | API | model used | design |
|---|---|---|---|
| model up | 200 | yes | 208 parts, 40 s |
| **model killed** | **200** | no | 188 parts, 2.6 s, deterministic |
| after auto-restart | 200 | yes | 208 parts |

The API never went down and no request was lost to a dead socket. Two things came free:

* **Prompt caching.** llama-server keeps the KV cache between requests, so the 818-token
  preamble is processed once — 63.9 s cold against 16.5 s warm with 817 cached tokens. A
  design over the live tunnel now returns in ~10 s.
* **The API restarts in 2 s** instead of reloading 2.5 GB of weights.

### An unavailable model is now reported as unavailable

`/v1/generate` used to answer `ProviderUnavailable` by synthesizing a `StubProvider` result.
For any schema-carrying request that object then failed validation, so a model server that was
**not running** came back as `"model output violated the constrained JSON contract"` — blaming
the model for a process that had not started. It now returns **503** with the real reason.
Callers already treat any non-2xx as unavailable and fall back deterministically, so behaviour
downstream is unchanged; only the reason recorded on the design became true.

## Supervision, and why it is still needed

`llama-cpp-python` runs llama.cpp in-process, so a crash takes the whole uvicorn worker with
it. Serving v18 produced three in one afternoon: two `GGML_ASSERT` aborts in the repack kernel
(`ggml-cpu/repack.cpp:4238`, the per-thread workspace overflowing at llama.cpp's default thread
count) and one plain SIGSEGV at 8 threads *after* the thread cap had already reduced the rate.
Capping threads made it rarer, not reliable.

Start it through the supervisor rather than uvicorn directly:

```bash
python scripts/serve-v18.py
```

It restarts the service when the process dies, and also when it stops answering `/health`
while still alive — a hang looks nothing like a crash from outside. Measured on a killed
child: crash noticed in 5 s, restarted 4 s later, serving again 6 s after that, **~15 s of
downtime**, and the Design Studio falls back to deterministic synthesis for that window rather
than erroring.

Backoff doubles to 60 s while it keeps failing immediately and resets once it has stayed up
two minutes, so a genuine one-off does not get punished like a crash loop. Defaults to 4
threads; `KALE_LLAMA_THREADS` overrides.

The health watchdog is only safe because every endpoint in `app/main.py` is a sync `def`, so
FastAPI serves `/health` from a threadpool and it stays responsive through a 30 s completion.
**If those are ever changed to `async def`, the watchdog will start killing healthy services
mid-generation.**

This is a workaround, not a fix. The real fix is running llama.cpp out-of-process — a
`llama-server` the API talks to over HTTP — so a crash cannot take the API down at all.

## What survives what

| event | model | public URL | site |
|---|---|---|---|
| llama-server crashes | restarted by the supervisor | unchanged | deterministic for ~15 s |
| API restarted | back in ~2 s, no weight reload | unchanged | brief fallback |
| terminal closed | survives (processes are detached) | survives | fine |
| **reboot** | **gone** | **gone, and the hostname changes** | deterministic until restarted |

Autostart alone does not fix the reboot case, and this is the part worth understanding: a
`trycloudflare` quick tunnel gets a **new random hostname every time it starts**. Bringing the
model back automatically would still leave `INFERENCE_URL` pointing at a dead hostname, so the
site would stay deterministic until someone re-pointed it and redeployed.

### Autostart at logon (installed)

`scripts/kale-v18-autostart.cmd` is copied into the per-user Startup folder
(`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`). It launches the supervisor, which
now brings up **all three** processes — llama-server, the API and the tunnel — and writes the
current public hostname to `build/tunnel-url.txt`.

Per-user Startup rather than a scheduled task because `schtasks /Create` requires elevation.

Three traps this hit, all now handled in the code:

* **`pythonw` has no stdout.** The supervisor logs to `build/supervisor.log` itself. Without
  that, a failing autostart is completely silent and looks exactly like success.
* **`sys.executable` under `pythonw` is `pythonw`.** Handing that to the API child made uvicorn
  exit 1 immediately (it writes a startup banner to a stdout that does not exist), producing a
  restart loop. Children are launched with the console interpreter.
* **Two supervisors fight.** A stale one from an earlier session kept restarting its own
  children while a newly started one could not bind and silently looped. The supervisor now
  refuses to start if something is already serving the API port.

Verified by tearing the stack down and running the Startup entry: all three up in ~15 s.

### The hostname problem is closed: the supervisor publishes it

When the tunnel comes up with a new hostname, the supervisor now pushes it to Vercel itself —
`INFERENCE_URL` updated, deployed, alias re-pointed (`publish_tunnel_url` in
`scripts/serve-v18.py`; disable with `KALE_AUTO_PUBLISH=0`). `build/published-url.txt` records
the last pushed hostname so an unchanged one skips the deploy.

Verified end to end: full stack teardown → autostart → **new hostname live on
kaleai.vercel.app in 38 s, zero human steps**.

One landmine found on the way: the Microsoft Store Python is MSIX-packaged, and its container
holds an empty private copy of `%APPDATA%\npm` that shadows the real directory — the global
`vercel` CLI is invisible to the supervisor and every child it spawns, while `ls` in a normal
shell shows it plainly. The CLI is therefore vendored at `build/vercel-cli/`
(`cd build && npm install vercel --prefix vercel-cli`), a path MSIX does not virtualize.

What still takes the model down: the laptop itself sleeping, closing, or losing power/network.
Auto-publish heals the *address*; it cannot heal the machine being off. That, and only that, is
what a named tunnel + always-on box would solve:

1. **A named Cloudflare tunnel** (`cloudflared tunnel create`) gives a stable hostname that
   survives restarts, so `INFERENCE_URL` is set once and never again. Free, but needs a
   Cloudflare account and a domain on it.
2. **Autostart the supervisor** at logon, which only helps once the hostname is stable:

   ```
   schtasks /Create /TN "KaleForge v18" /SC ONLOGON /F ^
     /TR "python C:\path\to\Kale-Forge-Complete-2026\scripts\serve-v18.py"
   ```

Do (1) before (2). Autostarting a service whose address changes on every boot just automates
the production of a stale URL.

## The always-on alternative: quantised GGUF on a free CPU Space

The path above only runs while you are watching the GPU meter. A quantised GGUF runs on a free
always-on box instead, which is the difference between "v18 is live" and "v18 is live when I
remember to start it". Build it with `scripts/build-v18-gguf.sh` (merge is a CPU operation — no
GPU needed) and serve it with `Dockerfile.space` on a Hugging Face Space, hardware *CPU basic*.

### This path is measured, not assumed

The chain was run end to end on a Windows laptop CPU with a stand-in model
(Qwen2.5-0.5B-Instruct Q4_K_M) driving the **real** intent schema through the **real**
`local_llamacpp` provider and `build_robot_spec(use_model=True)`:

| | Result |
|---|---|
| Schema-valid intents | 5/5 under grammar-constrained decoding |
| Generation rate | **15.5 tok/s** (0.5B Q4_K_M, 2 threads) |
| Intent length | 30–162 completion tokens (818-token prompt) |
| Wall clock per design | 1.7–12.2 s |
| Service up | `model.used=True`, `provider='local_llamacpp'`, `model_version` recorded |
| Service down | falls back deterministically, reason recorded in `spec.model.reason` |

So the architecture is proven on free CPU. Swapping in a v18 GGUF is a **weights change, not an
architecture change** — that no longer has to be discovered by spending GPU hours.

### But read this before building the 4B GGUF

Generation is memory-bandwidth bound, so a 4B at the same quantisation runs at roughly an
eighth of the rate measured above — call it **~2 tok/s on this laptop and 1–1.5 tok/s on a free
Space's 2 vCPU**. A 141-token intent is then 90–140 s. Vercel Hobby caps functions at 10 s by
default and 300 s with Fluid compute, so a 4B on free CPU either always falls back or makes
every design a two-minute wait.

The honest conclusion is not "GGUF fixes the free tier". It is:

- **The free CPU path is real, and the binding constraint is model size, not the plumbing.**
- In production the model only ever picks *architecture* — subsystems, drivetrain family,
  mechanism types — from an enumerated vocabulary with clamped numeric ranges. It never emits
  geometry. That is a far smaller job than the CAD generation v18 was trained for, and a 1.5 B
  fine-tune would plausibly do it while staying interactive (~5 tok/s, ~30 s per design).
  `training/configs/mlx-design-1.5b.yaml` already exists.
- If you want v18-the-4B specifically, use the GPU path above and accept that it is
  opportunistic, or pair the Space with Fluid compute and accept the wait.
