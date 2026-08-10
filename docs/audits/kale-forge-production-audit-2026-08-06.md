# Kale Forge — production hardening audit, 2026-08-06

Scope: the Design Studio deployed at `kaleai.vercel.app`. This document records what was
found, what was repaired, what proves it, and — importantly — what was **not** done.

## 1. The architecture, as it actually is

This matters because it constrains every repair below.

| Layer | Reality |
|---|---|
| Studio app | **One Vercel Python function**, `apps/site/api/studio.py`, `BaseHTTPRequestHandler`. Not Next.js/React. |
| Dependencies | **Standard library only.** The function deploys with no `requirements.txt`; a pydantic import once took the live site down (`FUNCTION_INVOCATION_FAILED`). So: no Zod/Pydantic, no TypeScript, no build step. |
| Frontend | Vanilla JS embedded as a string in that same file; three.js via import map from jsDelivr. |
| Design engine | `apps/site/app/services/*` — `robot_spec` (parse + synthesise), `frc_cad` (geometry), `cad_contract` (validation), `frc_featurescript` (Onshape source), `frc_season` (rule data). |
| Module copies | The same modules exist **three times** (`apps/site`, `services/analysis`, `apps/kale-demo`) and must stay byte-identical — enforced by `test_site_bundle_parity.py`. |
| Persistence | **None on the studio path.** Designs are deterministic functions of (prompt, season); there is no design/revision table reachable from the Vercel function. |
| Auth | Session cookie verified by calling the Kale.ai app's `/api/auth/me`; that app now runs on the same cloud VM. |
| STEP export | Proxied to the inference VM's cadquery worker. |
| Tests | pytest under `services/analysis/app/tests`. No Playwright, no JS test runner. |

## 2. Defects, repairs and proof

### D1 — FeatureScript generated for anyone, with wildcard CORS **[FIXED]**
* **Confirmed:** `GET /studio?fs=1&prompt=robot&season=2026-rebuilt` → `200` + full source, `Access-Control-Allow-Origin: *`.
* **Root cause:** a deliberate but wrong decision, recorded in a code comment: *"it is generated output from a public prompt, so there is nothing here to protect."* It is an unauthenticated compute endpoint, and the prompt travelled in the URL.
* **Repair:** all generation/export moved to authenticated `POST /api/designs*`. `?fs=1` now answers `401`; `?json=1`/`?step=1` answer `410`. No code path emits any CORS header.
* **Coverage:** `test_no_wildcard_cors_anywhere_in_the_handler`; live check returns 401.

### D2 — prompts in URLs, history and logs **[FIXED]**
* **Repair:** every design action is a POST with a JSON body. `_log()` drops `prompt`, `revision`, `source`, `access_key`, `secret_key`, `cookie` and logs request id / design size / duration / error category instead.
* **Coverage:** `test_prompt_is_never_placed_in_a_url_by_the_client`, `test_logging_never_receives_prompt_or_credentials`; server access log verified to contain zero prompt words.

### D3 — "4 meter elevator robot" built a normal robot **[FIXED]**
* **Root cause:** no stage rejected impossible requirements; the parser clamped heights into a legal range and continued.
* **Repair:** new `design_gate.py` runs **before** geometry. Metric/imperial/misspelled measurements are converted to inches and checked against the season's own published limits. Over-height requests are **rejected**, never clipped, quoting the rule.
* Live: `422 DESIGN_RULE_VIOLATION` — *"R107 caps total height at 30 in for 2026 REBUILT, and the request asks for 4 meter (157.5 in) — over by 127.5 in."*
* **Coverage:** `test_over_height_requests_are_rejected_not_clipped` (4 variants incl. `4 meteres` / `400 cm`).
* **Deliberate exception:** frame **perimeter** is *not* rejected. An over-perimeter frame has a correct legal answer — shrink to the budget — which `frame_budget` already does and reports in `constraints.frame_note`. Height has no such answer.

### D4 — "no mechanisms, only a 27 inch chassis" built ~121 parts **[FIXED]**
* **Root cause:** "no"/"only" were never represented; `doMechanisms : true` was hardcoded.
* **Repair:** the gate derives scope (`chassis`) and `include_drivetrain` / `include_electrical`; `build_cad` honours them; FeatureScript defaults are derived from the spec.
* Live: **39 parts, `['chassis']` only**, `doDrivetrain/doMechanisms/doElectrical : false`.
* **Coverage:** `test_chassis_only_builds_a_chassis`, `test_chassis_only_featurescript_disables_mechanisms`.

### D5 — "robot" produced a ~258-part robot **[FIXED]**
* **Repair:** vague prompts return `clarification_required` with five high-value questions (season, scope, drivetrain, mechanisms, constraints).
* **Care taken:** the vagueness test is vocabulary-aware — "fuel cycler" names a gamepiece and an archetype and still builds. An earlier, narrower version wrongly refused it and broke an existing test.
* **Coverage:** `test_vague_prompts_ask_before_building`, `test_a_specific_prompt_still_builds`.

### D6 — wrong design title **[FIXED]**
* **Repair:** naming is scope-aware and set in `robot_spec` (previously only the web wrapper named designs, so direct callers got none). Chassis requests are named "27 × 27 in chassis | 2026 REBUILT".
* **Coverage:** `test_chassis_request_is_not_named_after_an_intake_robot`.

### D7 — untrusted values through `innerHTML` **[FIXED]**
* **Repair:** 4 of 5 sinks replaced with `textContent` / element construction (`season <option>`, generation error, picked-part readout, Onshape link). The dossier keeps a developer-authored HTML *template*, but every *value* is escaped once at the boundary by `escapeDeep(rawSpec)`.
* Onshape URLs are validated **server- and client-side** against an HTTPS + hostname allowlist, rejecting `javascript:`, `data:`, http, look-alike hosts and credential-bearing URLs; links get `rel="noopener noreferrer external"`.
* **Coverage:** `test_xss_payloads_survive_as_inert_data` (4 payloads), `test_onshape_url_allowlist` (8 cases); browser run with `<img onerror>`, `<svg onload>` and a `javascript:` anchor produced **0 injected elements and no execution**.

### D10 — CAD fidelity overstated **[PARTIALLY ADDRESSED]** — see §4.

### Additional defects found during this work (not in the brief)
1. **The generator produced illegal robots.** With rules enforced against real geometry, the default 2026 robot measured **32.1 in** against a 30 in cap. Cause: the mechanism height budget ignored the 2 in rail-top mount and 1.125 in ground clearance. Fixed; all 18 prompt×season combinations now pass.
2. **The arm was modelled through the floor** — the gripper sat up to **5.8 in below the wheel contact plane**. Fixed by choosing a stow angle that keeps the chain above the floor; a ground-clearance check now reports any body below it.
3. **Height was being measured from the wrong datum.** Using the lowest solid conflated a modelling fault with height. The floor is now the **drive-wheel** contact plane (intake rollers and gripper wheels are also `wheel` features and must not define it).
4. **A closing `</script>` inside the inline script** — introduced by me while writing the XSS comment, caught immediately by the new test. It would have terminated the script element and executed injected markup on every page load. Removed, with a warning comment and a regression test.
5. **The dev server never delegated POST**, answering 404 for everything except `/capture`, which made the whole POST API untestable locally. Fixed.

## 3. Security posture now

* **Auth required** for `/api/designs`, `.../exports/featurescript`, `.../exports/step`, `.../onshape`, and for all legacy query modes.
* **Origin checked** on state-changing requests (server-side, cookie still required).
* **CSP with per-response nonces** — `script-src 'self' 'nonce-…' https://cdn.jsdelivr.net`, no `unsafe-inline`, no `unsafe-eval`, `object-src 'none'`, `base-uri 'none'`, `frame-ancestors 'none'`; plus HSTS, Referrer-Policy, Permissions-Policy, `X-Content-Type-Options`, `Cache-Control: no-store, private`.
  Verified in a real browser: all three inline scripts run under the nonce, and an injected inline script + inline event handler **both fail to execute**.
* **Limits:** 4 000-char prompt, 64 KB body, 12 requests/minute per client.
* **Errors:** one machine-readable envelope `{error:{code,message,requestId,…}}`; internal detail stays in the log tied to the request id. Onshape failures never echo the exception (it can quote the credential).
* **Onshape keys** are used for one request, never persisted, and dropped in a `finally`.

## 3b. Second pass, 2026-08-07 — the remaining phases

### D10 — CAD fidelity overstated **[FIXED]**
* **What was actually wrong** (measured, not assumed): the browser viewer already cut
  real gear teeth — but the **STEP export** shipped every gear/sprocket/pulley as a
  smooth disc (its own docstring said so) and **skipped belts, chains and ropes
  entirely**; the viewer drew belts as two floating bars; and nothing anywhere stated
  what fidelity a part was modelled at.
* **Repair, data layer:** `transmission_geom.py` (new, synced ×3) is the single
  source for tooth outlines, belt wrap loops and mesh math. `build_cad` stamps every
  belt with the pitch radii + axis of the pulleys its endpoints land on. Two reports
  attach to every spec: `fidelity` (per-part levels: detailed / concept / envelope /
  layout, overall = "mixed" when levels differ — a design must not summarise itself
  by its best part) and `transmission` (measured gear-mesh distances, belt-endpoint
  resolution).
* **Repair, renderers:** the viewer draws belts/chains as true wrapped loops (two
  tangent runs + arcs at the resolved radii, oriented by the pulley axis); the STEP
  worker extrudes teeth as one closed outline (no per-tooth booleans), builds
  belt/chain loops as solids, and appends "(catalog envelope)" to envelope-level part
  names so the CAD tree itself is honest. Verified on the VM: a full robot exports
  274 parts, 19.9 MB, with tooth geometry, chain loops and envelope naming present.
* **The validator found real defects the day it ran:** the swerve idler and azimuth
  pinion floated ~0.5 in from the gears they claimed to drive; the gear-drive intake
  had a 3.6 in gear (a helper doubled tooth counts) physically overlapping its
  neighbours, with one 24 t idler asked to bridge 5.4 in of air; elevator rigging was
  driven on the left side only, the right run attached to nothing; the shooter feeder
  belt ended in mid-air. All fixed by computing centres from pitch radii
  (`_meshed()`), relocating the intake gear train to the roller bar, driving the
  rigging symmetrically, and adding the feeder's second pulley.
  **18/18 prompt×season builds now measure zero transmission issues** — pinned by
  `test_no_generated_design_has_transmission_issues`.

### Phase 7 — persistence, revisions, ownership **[IMPLEMENTED]**
* `forge_designs` table + `/api/forge/designs` router live in the VM app (patches
  versioned in `deploy/inference-cloud/kaleai-src-patches/`). The prompt **chain**
  lives server-side, capped at 30 steps / 6 000 chars; appends require
  `expected_revision` (optimistic concurrency → 409); restore truncates the chain;
  ownership is enforced on every route (another account's design answers 404, listing
  shows only your own). Verified live on the VM with two throwaway accounts
  (created via the API, removed after; the 4 real users and 4 projects are intact,
  and the DB was backed up with the sqlite backup API first).
* The studio frontend now sends `{designId}` — the function composes the prompt from
  the stored chain — so **revisions no longer grow the request**; the store being
  unreachable degrades to stateless generation with history visibly absent, never a
  broken studio. Undo is a server-side restore + rebuild.
* **Route collision found and fixed at this step:** the recovered app already owned
  `/api/designs` (its web UI calls it), so the first deploy's rewrites had shadowed
  that feature. Every studio endpoint moved to `/api/studio/designs*`; the bare
  `/api/designs` namespace was returned to the app.

### Phases 8 + 12 — lifecycle and accessibility **[IMPLEMENTED]**
* One state machine (`idle/generating/succeeded/failed/cancelled`) drives Generate,
  the spinner and a real **Cancel** (aborts the request AND invalidates the ticket so
  a late response cannot repaint the page). Refusals render their clarification
  questions **with the server's examples** as a list; rule violations render their
  details. Verified in-browser: vague prompt → 5 questions with examples; illegal
  height → R107 sentence; cancel mid-flight → "Generation cancelled." and idle
  controls.
* Dialogs carry `role="dialog"`, `aria-modal`, labelled titles, a focus trap, Escape,
  and focus restoration. Inputs have real labels (`.sr-only`). The generation panel
  is `role="status" aria-live="polite"`. Reduced-motion users get snap transitions
  instead of eased ones (content never withheld); the render loop pauses entirely
  while the tab is hidden. Found and fixed in passing: the highlight handler cloned a
  material per part-click and never disposed it — a GPU memory leak for the life of
  the page — and my own first status write deleted the spinner element beside it.

### Phases 11 + 14 — Onshape honesty, performance **[IMPLEMENTED where possible]**
* Onshape: API keys were already per-request and never persisted; the modal says so.
  **OAuth is not implemented** — it requires registering an OAuth application on
  Onshape's developer portal, which only the account owner can do. Documented as the
  upgrade path.
* Season limits now carry `source` provenance (document, URL, transcription date).
* The public season list is CDN-cacheable (`public, max-age=3600`); everything else
  stays `no-store, private`. Viewer: hidden-tab pause + material-leak fix above.

### Cloud deploys in this pass
* VM `kaleai` image rebuilt with the forge router (web build cache hit; ~1 min).
* VM `api` image rebuilt with the new CAD modules + STEP worker; smoke-tested
  in-container (chassis 98 parts, full robot 274 parts).

## 4. Remaining limitations — explicitly not done

Updated 2026-08-07 after the second pass. Still not done, and why:

1. **Onshape OAuth** — requires the account owner to register an OAuth app on
   Onshape's developer portal; code cannot conjure the client id/secret. The
   per-request API-key flow (never logged, never persisted) remains.
2. **Rate limiting is per-instance** (serverless), so it is a floor, not a global
   quota. A shared quota would need the store to arbitrate it.
3. **Tooth profiles are display-honest trapezoids, not involutes**, and bevels stay
   true-pitch discs (their teeth are conical; a flat outline would lie). This is
   stated in the fidelity report rather than hidden.
4. **Gear meshing is validated on parallel-axis pairs**; bevel pairs and
   chain-wrap interference are not yet measured.
5. **No idempotency keys** on design creation — a double-click can create two
   designs (each still owned and consistent). Optimistic concurrency covers the
   destructive path (revisions).
6. **The Playwright matrix** is delegated and reported separately in §7; whatever
   it has not covered by deploy time is listed there.
7. **No `4 m` rejection for seasons without an always-on height cap** (2025 has
   none) — correct behaviour, but worth knowing.

## 5. How to reproduce the verification

```bash
# unit + integration + security + CAD regression
cd services/analysis && python -m pytest app/tests -q          # 324 passed, 2 skipped

# live server
cd apps/site && KALE_DEV=1 python dev_server.py 8899
curl -i "localhost:8899/studio?fs=1&prompt=robot"                       # 401, no CORS
curl -X POST localhost:8899/api/designs -H 'Content-Type: application/json' \
     -d '{"prompt":"4 meter elevator robot","season":"2026-rebuilt"}'   # 422 R107
curl -X POST localhost:8899/api/designs -H 'Content-Type: application/json' \
     -d '{"prompt":"no mechanisms, only a 27 inch chassis","season":"2026-rebuilt"}'
```

`KALE_DEV=1` stubs the auth check for local work — it must never be set in production.

## 6. Environment variables

No new ones. Existing: `INFERENCE_URL`, `INFERENCE_TOKEN`, `INFERENCE_TIMEOUT_SECONDS`,
`KALE_AUTH_ME_URL`, `MODEL_GEOMETRY`, `KALE_DEV` (local only).
Set in Vercel production: only the three `INFERENCE_*` vars. `KALE_AUTH_ME_URL` and
`MODEL_GEOMETRY` use code defaults; `KALE_DEV` is confirmed absent.

## 7. Deployment record — 2026-08-07

Deployed as `dpl_8hojYm6mMeUPAcKVD9kKn8dFzhh4`, serving `https://kaleai.vercel.app`
(alias followed the deploy automatically and was verified, not assumed).

Two defects were found **at deploy time** — both would have shipped broken behaviour:

1. **The `/api/:path*` catch-all rewrite would have swallowed the new design API.**
   `vercel.json` proxies `/api/*` to the VM app, and Python functions only match their
   exact file path, so `POST /api/designs` would have 404'd on the VM. Fixed by adding
   `/api/designs` and `/api/designs/:path*` rewrites to `/api/studio` **before** the
   catch-all (first match wins). Proven live: unauthenticated `POST /api/designs` returns
   the studio function's `AUTH_REQUIRED` envelope, not the VM's 404.
2. **The auth default pointed at the pre-migration VM** (`kale.150.136.151.230.nip.io`).
   `KALE_AUTH_ME_URL` is unset in production, so server-side session checks would have
   gone to the old VM and rejected every session issued since the migration. Default now
   `https://kaleai-app.150.136.212.31.nip.io/api/auth/me` — the same host the edge
   rewrites target — verified reachable with a valid certificate (401 in 0.56 s).

Live verification performed against the domain:

| Check | Result |
|---|---|
| `GET /studio?fs=1&prompt=…` | **401** `AUTH_REQUIRED`, no CORS header (old build answered 200 + source) |
| `GET /studio` | 200; CSP header nonce matches the 3 stamped `<script nonce>` tags; `__CSP_NONCE__` count 0; fresh nonce per response |
| Page boot in a real browser | title correct, seasons loaded (3), **zero console errors / CSP violations**, sign-in gate shown when signed out |
| `POST /api/designs` no cookie / junk cookie | 401 envelope in ≤0.5 s (auth backend reached; no timeout path) |
| `GET /studio?seasons=1` | 200, public as designed |
| `GET /` and `GET /api/auth/me` | homepage intact; auth still proxied to the VM app |

**Update 2026-08-07 — the authenticated path IS now verified in production.**
Deployment `kale-9r4xvs5ry` (aliased, verified). A disposable account created through
the public API ran the whole matrix against `https://kaleai.vercel.app` and was then
removed (users back to 4, no stray rows):

| Live check | Result |
|---|---|
| `GET /api/designs` | back with the recovered app (FastAPI 401) — its design feature un-shadowed |
| `POST /api/studio/designs` unauth | studio 401 envelope |
| login → create design → generate by `designId` | 200; fidelity `mixed`, transmission `ok`, 204 parts |
| revision "remove the elevator" via server chain → regen | elevator absent from the rebuilt design |
| stale `expected_revision` | **409** |
| illegal revision ("4 meters tall") on the stored chain | regen → **422 R107** |
| restore to revision 2 → regen | 200 — undo works in production |
| FeatureScript export by `designId` | 200, 169 KB source |
| seasons endpoint | `Cache-Control: public, max-age=3600` |
| homepage / studio copy | "Editable concept CAD"; "never a claim beyond what was modelled" |

## 8. External review response — 2026-08-07 (deployment `kale-iiwpno77w`)

An external review found 3 critical, 10 high and ~15 medium issues. All were real.
Fixed and verified in production with a disposable account (removed after; 4 users intact):

| Critical finding | Fix | Live proof |
|---|---|---|
| Exports rebuilt from `currentPrompt` (only the edit text after a revision) — a different robot than shown | every export sends `designId + baseRevision`; the raw prompt only for never-saved designs | export with stale baseRevision → **409** |
| Rejected revisions persisted (client wrote the store BEFORE validation) | all store writes moved server-side, committed only AFTER gate+build succeed (`_CommitFailed` aborts the reply) | "4 meters tall" revision → 422 AND store still at revision 1, chain length 1 |
| Undo flipped the store before the rebuild | restore is one transactional request: build the truncated chain, then rewind | restore → 200, design block `{revision: 1}` |

Highs: per-operation AbortControllers (design/export-fs/export-step/onshape) so an export
can never cancel a generation and Cancel invalidates the only request that exists; the
permanent `storeAvailable` latch removed (a 5-second outage no longer degrades the whole
session); seasons now FAIL CLOSED (rule-checked generation pauses; off-season is an
explicit choice, never inferred); Onshape keys wiped from the DOM after use and on close;
viewer comments now distinguish the WebGL preview's visual pockets from the STEP's true
cut bores; "Structural audit — nothing floats" renamed to Assembly connectivity; the
"four things a robot can get wrong" caveat corrected; STEP-import copy no longer promises
one-Part-Studio-per-assembly.

Mediums: short prompts/edits get visible messages (announced by the live region);
account controls survive a missing name; the sign-in gate uses the same focus-trapped
dialog helpers; dossier arrow and sign-out meet 44px; the canvas has an accessible name;
STEP downloads get their own controller/timeout and refuse non-`ISO-10303` bodies;
object URLs revoked; bumper-number CanvasTextures disposed; `preserveDrawingBuffer` off
(capture renders explicitly); bumper numbers only render when a team number was given;
the history panel lists every revision with restore-to-any; the prompt box starts empty
(example moved to placeholder); homepage headline now "Parametric source stays with the
design", hero alt text matches the swerve record. Edge: `/models` → 308, `/api/auth/*`
→ `private, no-store`, homepage gets a CSP (inline boot script hash-allowlisted).

Delegated (in progress): the recovered app's nav fixed in its React source instead of a
MutationObserver, Kale Forge branding on /app, and server-side no-store on auth/me.
Known remaining: bundling three.js locally to drop the jsDelivr CSP allowance, and
removing `'unsafe-inline'` styles — both need a build step the stdlib-only function
deliberately avoids; single-Part-Studio Onshape OAuth remains user-gated.

Tests after this pass: **354 passed, 2 skipped** (13 new/updated behavior pins).
