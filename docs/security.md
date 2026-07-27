# Kale Forge — Security

Kale Forge ingests untrusted engineering files and runs a self-hosted language model over their
contents. This document summarizes the implemented controls and the honest residual risks.

## Threat model

| Asset | Threat | Primary control |
|---|---|---|
| Host filesystem | Malicious upload / ZIP path traversal, zip bombs | `parsers/zip_safe.py` (allowlist, traversal + symlink + ratio checks, size caps) |
| Analysis process | Code/command execution via uploaded files | Files are parsed as data only; nothing is executed |
| SPICE handling | Simulator command injection | Netlists are parsed, never run through any SPICE engine |
| Model output integrity | Prompt injection from uploaded text | Fenced user content + schema validation + citation gate |
| Other users' data | Cross-tenant access | Per-user project ownership checks on every route |
| Admin operations | Unauthorized model/training changes | `X-Admin-Token` gate independent of user auth |
| Training data | Leaked secrets/PII into datasets | `kale_training/secret_scan.py` + consent gates |

## Implemented controls

### Upload pipeline
- **Extension allowlist** (`ALLOWED_UPLOAD_EXTENSIONS`): `.kicad_sch .kicad_pcb .kicad_pro
  .zip .net .cir .spice .csv .pdf`. Anything else is rejected (`validate_upload`).
- **Filename sanitization** (`sanitize_filename`): reduces to a basename, strips control
  characters and disallowed bytes, collapses `..` runs, caps length. Same-named uploads are
  disambiguated, never overwritten (`_unique_filename`) — originals are preserved.
- **Size limits**: per-file `MAX_UPLOAD_MB` (default 50 MB) enforced before storage.
- **ZIP safety** (`safe_extract_zip`): rejects absolute paths, `..` traversal, Windows drive
  paths, and symlink entries; enforces per-file and total uncompressed caps and a max
  compression ratio (zip-bomb defense) and a max file count; flattens to sanitized basenames;
  verifies every resolved path stays inside the destination directory.
- **No execution**: uploaded files are stored verbatim and only ever read by parsers. No
  SPICE simulator, no shell, no eval is invoked on their contents.

### Prompt-injection isolation
- Uploaded text reaches the model only inside a fenced `PROJECT DATA` block
  (`fence_user_content`, sentinels `<<<KALE_PROJECT_DATA_BEGIN/END>>>`). Any embedded copy of
  the sentinel is stripped so an upload cannot forge a fence boundary or escape into the
  system-instruction region.
- System instructions and the required output schema are supplied separately from user content.
- **Citation/hallucination gate** (`models/ai_review.validate_ai_review`): every model claim
  must cite components/nets that exist in the parsed project; unknown citations are dropped and
  items left with no valid citation are removed. `confirmed_findings` may only restate real
  deterministic rule findings — otherwise they are demoted to `possible_findings`. Output that
  does not parse against the `AIReview` schema is repaired once, then rejected.
- The model is instructed never to assert safety/compliance; the app additionally attaches
  stronger warnings for high-voltage / battery / medical / automotive contexts.

### Authentication & authorization
- **User auth**: `Authorization: Bearer <api_token>` matched against `User.api_token`. Local
  development can set `AUTH_DISABLED=true` to use a demo user — this must never be true in
  production and does not bypass admin checks.
- **Project isolation**: every project route resolves via `_project_for_user`, returning 404
  (not 403) for another user's project so existence is not disclosed.
- **Admin gate**: model selection/rollback and all `training/*` endpoints require
  `X-Admin-Token == ADMIN_TOKEN`, checked independently of user auth.
- **Bootstrap admin**: a single first administrator can be provisioned from
  `BOOTSTRAP_API_TOKEN` / `BOOTSTRAP_ADMIN_EMAIL` at startup, keeping initial credentials out
  of the UI and API payloads; token rotation takes effect on restart.

### Training-data hygiene
- `secret_scan.py` flags AWS keys, private-key blocks, bearer tokens, high-entropy strings,
  emails, and phone numbers; failing rows are quarantined, not trained on.
- User feedback never auto-flows into training: promotion requires explicit consent → PII/secret
  scan → dedup → license validation → human review (`promote_feedback.py`).
- Dataset rows are license-tagged; only allowlisted licenses enter training splits.

### Secrets & configuration
- No secrets in source. All configuration via environment; `.env.example` documents every
  variable and marks the ones that must be set for non-local deployments.
- Compose files require `POSTGRES_PASSWORD`, `ADMIN_TOKEN`, and `BOOTSTRAP_API_TOKEN` to be set
  explicitly (`${VAR:?...}`), failing fast rather than booting with defaults.

### No external AI dependency (security-relevant)
All inference, embeddings, and reranking are self-hosted. Uploaded engineering data — which may
be confidential or export-controlled — is never sent to a third-party AI API. External SaaS is
used only for non-AI infrastructure (Postgres, object storage, auth, analytics, payments).

## Residual risks & TODO

- **No antivirus scan** on uploaded PDFs/archives — add ClamAV (or equivalent) in production if
  accepting untrusted uploads at scale.
- **Parser hardening**: the S-expression parser is memory-bounded only by the upload size cap;
  extremely deeply nested inputs could stress the recursion/stack. Consider an explicit depth
  limit before exposing the service publicly.
- **Rate limiting / quotas** are not implemented at the app layer — enforce at the reverse proxy.
- **In-process job queue** runs analyses in a thread in the API process; a crafted file that
  makes a parser loop could degrade the instance. Move to the Redis worker backend and add
  per-job timeouts before untrusted multi-tenant use.
- **PDF text is stored but not sandboxed-rendered**; datasheet parsing beyond text extraction is
  out of MVP scope.
- **Secret scanning is heuristic**, not exhaustive — human review remains the gate before any
  user-derived data enters a training set.
- **Local validation gap**: run `npm run check` and `docker compose config` on the target host
  before release (the development host here lacks the Docker CLI).
