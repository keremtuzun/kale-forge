# Kale Forge backend, off the laptop

> **LIVE since 2026-08-06.** The VM exists and is serving: **`kale-cloud`, 150.136.212.31**
> (Always-Free A1.Flex, 4 OCPU / 24 GB / 80 GB, AD-1, Ubuntu 24.04).
>
> | what | where | state |
> |---|---|---|
> | v18 inference + STEP export | `https://kale-infer.150.136.212.31.nip.io` | **live**, bearer-protected, `kaleai.vercel.app` points here |
> | recovered Kale.ai app | `https://kaleai-app.150.136.212.31.nip.io` | **LIVE** — `/app,/login,/account,/projects/*` cut over 2026-08-06, user data migrated (4 users, 4 projects), auth enforced, uses v18 |
> | laptop model stack | — | **retired**: autostart parked, `KALE_AUTO_PUBLISH=0`, processes stopped |
>
> SSH: `ssh -i ~/.ssh/kale-oracle-recovery ubuntu@150.136.212.31` · stack lives in `~/kale`
> (`sudo docker compose ps`, `... logs -f api`). Everything is `restart: unless-stopped` and
> docker is enabled at boot, so a reboot brings it all back.
>
> **Cutover done.** The whole site is one box now. The user database was migrated out of the
> old VM's `kale_kale-data` volume (via the boot-volume clone on a temporary AD-2 host, since
> the clone is AD-2 and this VM is AD-1). Watch out if you ever repeat it: that volume's
> `kale.db` was **4 KB with a 1.7 MB `-wal`**, so a plain file copy yields an *empty* database
> — consolidate with SQLite's `backup()` API first (`deploy/oracle-recovery/migrate-userdb.sh`
> does this and refuses to write until the row counts are shown).
>
> **Rollback:** `deploy/oracle-recovery/cutover-kaleai.ps1 -Rollback` points everything back at
> the old VM, which is still running with its own copy of the data. Terminate `kale-ai`
> (150.136.151.230) and the `kale-ai-recovery-clone` volume once you no longer want that safety net.

Everything the laptop's `scripts/serve-v18.py` supervises — **llama-server with the v18
GGUF, the inference API, and the cadquery STEP compiler** — packaged to run on an always-on
Linux VM behind TLS on a **stable hostname**. Point Vercel's `INFERENCE_URL` at it once and
the tunnel-hostname churn, the laptop dependency, and the "geometry server is offline"
fallbacks when the laptop sleeps are all over.

## Why this shape (and why not Supabase)

- **Supabase** is Postgres + auth + storage + small Deno edge functions. It cannot run a
  2.5 GB llama.cpp model server or OpenCascade/cadquery (native C++), and its functions are
  built for milliseconds-to-seconds, not 30–300 s CPU jobs. Wrong tool for both workloads.
- **Vercel** already hosts the site itself; the deterministic design path runs there today.
  Only the model and STEP export need a real machine.
- **Oracle Cloud "Always Free" ARM VM** (VM.Standard.A1.Flex, 4 OCPU / 24 GB) is the $0
  always-on machine — and the account already exists (the auth app lives on one). CPU
  inference is slower than nothing-else-running laptop speed but in the same class
  (llama.cpp with 4 threads; the site's timeout is raised to 120 s and it still degrades
  gracefully to deterministic synthesis if a generation runs long). STEP export is pure
  CPU geometry and takes ~30–60 s — comfortably fine.
- Paid alternatives if generation speed ever matters more than $0: **Modal / RunPod /
  Replicate** serverless GPU (pennies per design, cold starts, needs a card on file), or a
  small x86 VPS (Hetzner ~€4/mo is markedly faster per-core than A1). A free **Hugging Face
  Space** can host the API + STEP worker but its 2 vCPU makes model generations ~4 min —
  past the site's ceiling, so the model would effectively always fall back.

## What's in here

| file | role |
|---|---|
| `docker-compose.yml` | llama-server (own container — a C++ crash must not kill the API) + API + Caddy TLS |
| `Dockerfile.api` | python 3.11 + analysis deps + inference service + cadquery + the studio's CAD modules |
| `Caddyfile` | HTTPS on `$KALE_HOST` (nip.io hostname → zero DNS setup), long timeouts for STEP |
| `bootstrap.sh` | run on the VM: installs docker, opens 80/443 in Oracle's host iptables, `compose up` |
| `push-from-laptop.ps1` | run on the laptop: stages code, writes `.env` + token, scp's everything incl. the GGUF, runs bootstrap |
| `flip-vercel.ps1` | run on the laptop: verifies the cloud API, swaps `INFERENCE_URL`/`INFERENCE_TOKEN` on Vercel, redeploys + re-aliases, **disables the laptop autostart** |

Security: the API now requires `Authorization: Bearer $KALE_API_TOKEN` on every route
except `/health` (a stable URL can't rely on tunnel obscurity). The token is generated once
by the push script into `.token.local` and flows to Vercel via the flip script. The site's
clients and the STEP proxy all send it automatically when `INFERENCE_TOKEN` is set.

## The one part that needs you: create the VM (~5 minutes, once)

> The old VM at 150.136.151.230 can't be reused directly — its SSH key is lost. Either
> recover it (Oracle console → Compute → Instance → **Console connection** → Cloud Shell
> connection lets you log in without SSH and append a new key to
> `~/.ssh/authorized_keys`), or — simpler — make a fresh instance:

1. <https://cloud.oracle.com> → Compute → Instances → **Create instance**.
2. Image: **Ubuntu 24.04** (or 22.04). Shape: **Ampere → VM.Standard.A1.Flex**,
   **4 OCPU / 24 GB** (that's the full Always-Free allowance; if capacity is out, retry
   later or drop to 2 OCPU / 12 GB — still fine).
3. Networking: keep the defaults (public IP assigned).
4. **Add SSH keys → Paste public key** — paste exactly this (already generated on this
   laptop as `~/.ssh/kale-cloud`):

   ```
   ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILCRZf85lpRxnp0Rjp//wNqK1eENp3pABSAbJi3Nwzt5 kale-cloud-backend
   ```

5. Create. Note the **public IP**.
6. Open the ports in the cloud firewall: Instance → Virtual cloud network → the subnet's
   **Default Security List** → **Add Ingress Rules**: source `0.0.0.0/0`, TCP, destination
   ports `80,443` (and one more rule for UDP `443` if you want HTTP/3). SSH (22) is
   already there. *(Host-level iptables — Oracle's second, less famous firewall — is fixed
   automatically by `bootstrap.sh`.)*

## Then, on the laptop

```powershell
cd "C:\Users\Kerem\OneDrive\Desktop\Kale-Forge-Complete-2026\deploy\inference-cloud"
.\push-from-laptop.ps1 -VmIp <PUBLIC_IP>      # uploads code + 2.5 GB model, builds, starts
.\flip-vercel.ps1 -VmHost kale-infer.<PUBLIC_IP>.nip.io
```

`flip-vercel.ps1` also parks `kale-v18-autostart.cmd` out of the Startup folder and sets
`KALE_AUTO_PUBLISH=0` — otherwise the next laptop boot would tunnel up and **re-point
production back at the laptop** (the supervisor self-publishes by design). Move the parked
file back to undo.

## After the flip

- The laptop can be off. Designs: model-backed for signed-in users (deterministic fallback
  unchanged). STEP download: served by the VM.
- Update the model later: rebuild the GGUF, then `push-from-laptop.ps1 -VmIp <IP>` (it
  re-uploads only what changed) and `ssh -i ~/.ssh/kale-cloud ubuntu@<IP>
  "cd kale && sudo docker compose restart llama"`.
- Logs: `ssh -i ~/.ssh/kale-cloud ubuntu@<IP> "cd kale && sudo docker compose logs -f api"`.
- Health: `https://kale-infer.<IP>.nip.io/health` (the only route that answers without the
  token).
