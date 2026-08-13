"""Rams Forge Design Studio, self-contained Vercel Python function.

GET  /                                        → the Design Studio page.
GET  /studio?seasons=1                        → season metadata (public, no computation).
POST /api/studio/designs                             → generate a design (auth required)
POST /api/studio/designs/exports/featurescript       → FeatureScript source (auth required)
POST /api/studio/designs/exports/step                → STEP assembly (auth required)
POST /api/studio/designs/onshape                     → publish to the caller's Onshape (auth required)

Every generation and export path requires a signed-in session and takes its prompt in a JSON
body: prompts are user content and must not appear in URLs, browser history or access logs.
The old `?json=1` / `?fs=1` / `?step=1` query modes now answer 401/410 rather than
generating — `?fs=1` in particular used to return the whole FeatureScript to anyone, with a
wildcard CORS header.

The synthesis is the repo's own stdlib modules (frc_parts / frc_season /
frc_robot_knowledge / robot_spec), bundled under ./app so this deploys as one function. It
always runs the deterministic path (use_model=False): no external AI, no network. Every
generated design carries its own 3D viewer, built from that
design's real spec.

`season` picks the game the robot is designed for and is the highest-authority input after
the prompt's explicit facts: it sets the gamepiece, the goal height, the climb reach and the
perimeter budget, so the same prompt yields a genuinely different robot in a different year.
Omit it and the season is read out of the prompt, falling back to the current one.
"""
from __future__ import annotations

import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# make the bundled `app` package importable
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from app.config import get_settings  # noqa: E402
from app.services.robot_spec import build_robot_spec  # noqa: E402
from app.services.frc_parts import MOTORS  # noqa: E402
from app.services.frc_season import SELECTABLE, season_options  # noqa: E402
from app.services.frc_featurescript import build_featurescript  # noqa: E402

# the per-design 3D viewer, served statically at /studio-viewer.js
try:
    with open(os.path.join(_ROOT, "studio-viewer.js"), encoding="utf-8") as _fh:
        VIEWER_JS = _fh.read()
except OSError:
    VIEWER_JS = "export function buildScene(){throw new Error('viewer.js missing')}"


def _viewer_model(spec: dict) -> dict:
    """Resolve the few catalog dimensions the 3D viewer needs but the spec doesn't inline."""
    dt = spec.get("drivetrain") or {}
    mk = dt.get("motor_key", "kraken_x60")
    sk = dt.get("steer_motor_key", mk)
    m = MOTORS.get(mk, MOTORS["kraken_x60"])
    s = MOTORS.get(sk, m)
    return {
        "motor": {"key": mk, "name": m["name"], "dia_in": m["diameter_in"], "len_in": m["length_in"]},
        "steer_motor": {"key": sk, "name": s["name"], "dia_in": s["diameter_in"], "len_in": s["length_in"]},
    }


def _name(spec: dict) -> str:
    """A title that describes what was asked for and what was built.

    "4 meter elevator robot" was coming back titled "SDS MK4i Intake robot" — a name
    assembled from a module and a mechanism the request never mentioned. A chassis-only
    request must not be named after an intake it does not have.
    """
    season = (spec.get("season") or {}).get("label") or (spec.get("profile") or {}).get("season", "")
    if spec.get("scope") == "chassis":
        frame = spec.get("frame") or {}
        size = f"{frame.get('width_in', 0):g} × {frame.get('length_in', 0):g} in "
        return f"{size}chassis" + (f" | {season}" if season else "")
    dt = spec.get("drivetrain") or {}
    module = dt.get("module") or (dt.get("type", "swerve").title() + " drivetrain")
    subs = spec.get("subsystems") or []
    lead = subs[0].title() if subs else "Drivebase"
    return f"{module} {lead} robot" + (f" | {season}" if season else "")


def make_spec(prompt: str, season: str = "", *, use_model: bool = False,
              design_type: str = "auto") -> dict:
    """Build one design, of whatever kind was asked for.

    This used to call the robot compiler directly, which is why every prompt came back as a
    robot. It now goes through the router: the request is classified, an engineering spec is
    resolved, and the matching engine runs. A robot request reaches exactly the same compiler
    it always did — `design_router` wraps it rather than replacing it — so nothing about
    robot generation changes.

    `use_model` is opt-in per call rather than global on purpose. The model is only ever asked
    on the signed-in JSON path: `?fs=1` is public and unauthenticated, so letting it reach the
    inference service would let anyone spend the GPU behind it. Geometry is deterministic
    either way — the model only chooses architecture, and `build_robot_spec` falls back to
    deterministic synthesis whenever the service is slow, asleep or unset.
    """
    from app.services.design_router import route  # noqa: PLC0415

    model_wanted = use_model and bool(get_settings().inference_url)
    spec = route(prompt, requested_type=design_type, season=season, use_model=model_wanted)
    # The viewer and the display name are only meaningful for artifacts that produced
    # geometry; a PCB has neither and must not be given an empty robot's.
    if spec.get("cad"):
        spec["viewer"] = _viewer_model(spec)
    if spec.get("engine") == "robot":
        spec["name"] = _name(spec)
    return spec


# The studio requires a signed-in Rams Forge account. Sessions are issued by the main app's
# auth API (proxied at /api on the same domain, so the browser's cookie flows here too); the
# function verifies the forwarded cookie against that API before generating anything.
_AUTH_ME_URL = os.environ.get("KALE_AUTH_ME_URL",
                              "https://kaleai-app.150.136.212.31.nip.io/api/auth/me")
# The design store (revision chains, ownership) lives in the same app as the session,
# so one cookie covers both. KALE_STORE_URL overrides for a different deployment.
_STORE_URL = os.environ.get(
    "KALE_STORE_URL",
    _AUTH_ME_URL.rsplit("/api/", 1)[0] + "/api/forge/designs")
_DESIGN_ID = re.compile(r"[0-9a-fA-F-]{8,36}")


class _CommitFailed(Exception):
    """A post-build store write failed; the reply must be an error, not a design."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


def _cookie_is_signed_in(cookie_header: str) -> bool:
    if os.environ.get("KALE_DEV"):
        # Local dev only: the auth API lives on the production domain and its cookie can
        # never be first-party on localhost, so the gate would make local testing impossible.
        return True
    if not cookie_header:
        return False
    import urllib.request  # noqa: PLC0415
    request = urllib.request.Request(_AUTH_ME_URL, headers={"Cookie": cookie_header})
    try:
        with urllib.request.urlopen(request, timeout=6) as resp:
            return resp.status == 200
    except Exception:
        return False


_ONSHAPE_HOSTS = frozenset({"cad.onshape.com", "www.onshape.com", "onshape.com"})


def _safe_onshape_url(url: str) -> bool:
    """A URL is only rendered as a link if it is HTTPS on a known Onshape host.

    Guards against a `javascript:`/`data:` payload or a credential-bearing URL arriving in a
    backend response and being turned into a clickable link by the page.
    """
    try:
        from urllib.parse import urlparse as _u  # noqa: PLC0415
        p = _u(url or "")
    except Exception:
        return False
    return (p.scheme == "https" and p.hostname in _ONSHAPE_HOSTS
            and "@" not in (p.netloc or "") and not p.username)


def _onshape_publish(access_key: str, secret_key: str, name: str, source: str,
                     base_url: str = "https://cad.onshape.com") -> dict:
    """Create an Onshape document and write the design into it as an editable Feature Studio.

    Same two-call flow as the main app's Onshape connection (there is no single "create a
    Feature Studio with these contents" call): create the element, then write its contents.
    Publishes FeatureScript source, never a mesh — the design arrives editable. Credentials
    are used for this one request and never stored anywhere.
    """
    import base64
    import urllib.request

    auth = base64.b64encode(f"{access_key}:{secret_key}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json",
               "Accept": "application/json;charset=UTF-8; qs=0.09"}

    def call(path: str, payload: dict) -> dict:
        request = urllib.request.Request(base_url.rstrip("/") + path,
                                         data=json.dumps(payload).encode(),
                                         headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=60) as resp:
            return json.loads(resp.read().decode())

    document = call("/api/v10/documents", {"name": name})
    did = document["id"]
    wid = document["defaultWorkspace"]["id"]
    studio = call(f"/api/v10/featurestudios/d/{did}/w/{wid}",
                  {"name": f"{name} — editable source"})
    eid = studio["id"]
    call(f"/api/v10/featurestudios/d/{did}/w/{wid}/e/{eid}/content", {"contents": source})
    return {"document_id": did, "workspace_id": wid, "element_id": eid,
            "url": f"{base_url.rstrip('/')}/documents/{did}/w/{wid}/e/{eid}",
            "mode": "featurescript-created", "editable": True, "flattened": False}


# ─────────────────────────────────────────────────────────────────────────────
# Request plumbing: identity, limits, redaction, machine-readable errors.
# ─────────────────────────────────────────────────────────────────────────────
MAX_PROMPT_CHARS = 4000
MAX_BODY_BYTES = 64_000
# Per-process, per-identity throttle. A serverless function has many instances, so this is a
# floor rather than a global quota — the real ceiling is the inference box's own limit. It is
# still worth having: it stops one tab hammering a single warm instance.
_RATE: dict[str, list[float]] = {}
RATE_LIMIT_PER_MIN = 12


def _client_key(headers) -> str:
    fwd = (headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return fwd or (headers.get("X-Real-IP") or "anon")


def _rate_ok(key: str, limit: int = RATE_LIMIT_PER_MIN) -> bool:
    import time  # noqa: PLC0415
    now = time.time()
    hits = [t for t in _RATE.get(key, []) if now - t < 60.0]
    hits.append(now)
    _RATE[key] = hits[-64:]
    return len(hits) <= limit


def _request_id() -> str:
    import uuid  # noqa: PLC0415
    return uuid.uuid4().hex[:16]


def _log(event: str, **fields) -> None:
    """Structured log with NO prompt text, credentials or generated source in it.

    Prompts are user content and were previously visible in URLs and therefore in request
    logs. Only identifiers, sizes, durations and categories are recorded here.
    """
    safe = {k: v for k, v in fields.items()
            if k not in {"prompt", "revision", "source", "access_key", "secret_key", "cookie"}}
    try:
        print(json.dumps({"event": event, **safe}, default=str)[:2000])
    except Exception:
        pass


# The page carries three inline scripts (the sign-in gate, the import map and the viewer
# module). Rather than weaken the policy with 'unsafe-inline' — which would also permit an
# injected inline handler — each response mints a nonce, stamps it on those three tags and
# names it here. An attacker-injected <script> has no nonce and does not run.
def _csp(nonce: str) -> str:
    return _CSP_TEMPLATE.replace("__NONCE__", nonce)


_CSP_TEMPLATE = ("default-src 'self'; "
                 "script-src 'self' 'nonce-__NONCE__' https://cdn.jsdelivr.net; "
                 # inline styles only; no inline script is permitted without the nonce
                 "style-src 'self' 'unsafe-inline'; "
                 "img-src 'self' data: blob:; "
                 "connect-src 'self'; "
                 "font-src 'self' data:; "
                 "object-src 'none'; base-uri 'none'; form-action 'self'; "
                 "frame-ancestors 'none'")
_SECURITY_HEADERS = {
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Frame-Options": "DENY",
}


def _new_nonce() -> str:
    import base64  # noqa: PLC0415
    import secrets  # noqa: PLC0415
    return base64.b64encode(secrets.token_bytes(16)).decode()


class handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, content_type: str,
              extra: dict[str, str] | None = None, nonce: str = "") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Security-Policy", _csp(nonce or _new_nonce()))
        for key, value in _SECURITY_HEADERS.items():
            self.send_header(key, value)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        # no-store is the safe default (designs and errors are per-user); a route that is
        # genuinely public and static — the season list — supplies its own policy instead.
        if "Cache-Control" not in (extra or {}):
            self.send_header("Cache-Control", "no-store, private")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, code: int, error_code: str, message: str,
              request_id: str = "", **extra) -> None:
        """One error shape for every failure, so the client can branch on `code`."""
        payload = {"error": {"code": error_code, "message": message,
                             "requestId": request_id or _request_id(), **extra}}
        self._send(code, json.dumps(payload).encode(), "application/json")

    # ── guards ───────────────────────────────────────────────────────────────
    def _require_auth(self, rid: str) -> bool:
        if _cookie_is_signed_in(self.headers.get("Cookie") or ""):
            return True
        self._fail(401, "AUTH_REQUIRED", "Sign in to generate or export designs.", rid)
        return False

    def _same_origin(self, rid: str) -> bool:
        """State-changing requests must come from this site.

        Checked server-side: a frontend token alone proves nothing.
        """
        origin = self.headers.get("Origin") or ""
        if not origin:
            return True  # non-browser client (curl, CI); the session cookie is still required
        host = (self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "").lower()
        try:
            from urllib.parse import urlparse as _u  # noqa: PLC0415
            parsed = _u(origin)
            if parsed.scheme in ("https", "http") and parsed.netloc.lower() == host:
                return True
        except Exception:
            pass
        self._fail(403, "ORIGIN_REJECTED", "Request origin is not allowed.", rid)
        return False

    def _read_json(self, rid: str) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length > MAX_BODY_BYTES:
            self._fail(413, "BODY_TOO_LARGE",
                       f"Request body exceeds {MAX_BODY_BYTES} bytes.", rid)
            return None
        try:
            raw = self.rfile.read(min(length, MAX_BODY_BYTES)).decode("utf-8", "replace")
            body = json.loads(raw or "{}")
        except (ValueError, TypeError):
            self._fail(400, "MALFORMED_JSON", "Request body is not valid JSON.", rid)
            return None
        if not isinstance(body, dict):
            self._fail(400, "MALFORMED_JSON", "Request body must be a JSON object.", rid)
            return None
        return body

    def _store(self, method: str, suffix: str, payload: dict | None, rid: str):
        """One call to the design store, authenticated as the signed-in caller.

        The caller's own session cookie is forwarded, so the store's ownership
        check — not this function — decides what is readable or writable.
        """
        import urllib.error  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Cookie": self.headers.get("Cookie") or ""}
        if data:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(_STORE_URL.rstrip("/") + suffix, data=data,
                                     method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as err:
            try:
                return err.code, json.loads(err.read().decode("utf-8") or "{}")
            except Exception:
                return err.code, {}
        except Exception:
            return 0, {}

    def _resolve_design(self, body: dict, season: str, rid: str):
        """Work out the prompt to build AND the store write that must follow.

        Returns (prompt, commit). `commit` is None or a callable(spec) that runs
        ONLY after the gate, the build and the validators all succeed — so a
        rejected revision, a failed restore or a refused prompt can never enter
        the store, and the store can never claim a revision the user was told
        was rejected. A commit that itself fails raises _CommitFailed and the
        whole reply becomes an error: the store and the screen stay in step.
        """
        design_id = str(body.get("designId") or "").strip()
        revision_text = str(body.get("revision") or "").strip()
        restore_to = body.get("restoreTo")
        base = body.get("baseRevision")

        if not design_id:
            prompt = self._prompt_text(body, rid)
            if prompt is None or not body.get("persist"):
                return prompt, None

            def commit_create(spec):
                status, saved = self._store("POST", "", {
                    "prompt": prompt, "season": season,
                    "name": str(spec.get("name") or "")[:200]}, rid)
                if status == 201 and saved.get("id"):
                    return {"id": saved["id"], "revision": int(saved.get("revision") or 1)}
                # Creation is non-destructive: an unsaved design is a degraded mode
                # the client shows, never a divergence between store and screen.
                _log("store.create_failed", requestId=rid, status=status)
                return {"storage": "unavailable"}
            return prompt, commit_create

        if not _DESIGN_ID.fullmatch(design_id):
            self._fail(422, "DESIGN_ID_INVALID", "That design reference is not valid.", rid)
            return None, None
        status, design = self._store("GET", "/" + design_id, None, rid)
        chain = [str(s).strip() for s in (design.get("chain") or []) if str(s).strip()]
        if status != 200 or not chain:
            self._fail(404, "DESIGN_NOT_FOUND",
                       "That design is no longer available. Start a new one.", rid)
            return None, None
        current = int(design.get("revision") or len(chain))
        if base is not None and int(base) != current:
            self._fail(409, "REVISION_CONFLICT",
                       f"This design is at revision {current}, not {base}. Reload it "
                       "before editing or exporting.", rid)
            return None, None

        def compose(steps):
            return steps[0] + "".join(f"\n\nRevision request: {s}" for s in steps[1:])

        if restore_to is not None:
            k = int(restore_to)
            if not 1 <= k <= len(chain):
                self._fail(422, "REVISION_INVALID",
                           f"This design has {len(chain)} revisions.", rid)
                return None, None

            def commit_restore(_spec):
                st, saved = self._store("POST", f"/{design_id}/restore",
                                        {"revision": k}, rid)
                if st != 200:
                    raise _CommitFailed(503, "STORE_WRITE_FAILED",
                                        "The rewound design built, but its history could "
                                        "not be updated. Nothing was changed — try again.")
                return {"id": design_id, "revision": int(saved.get("revision") or k)}
            return compose(chain[:k]), commit_restore

        if revision_text:
            if len(revision_text) > 2000:
                self._fail(422, "PROMPT_TOO_LONG",
                           "Keep the edit under 2000 characters.", rid)
                return None, None

            def commit_revision(_spec):
                st, saved = self._store("POST", f"/{design_id}/revisions",
                                        {"revision": revision_text,
                                         "expected_revision": current}, rid)
                if st == 409:
                    raise _CommitFailed(409, "REVISION_CONFLICT",
                                        "This design changed elsewhere while the edit was "
                                        "building, so the edit was not applied. Reload first.")
                if st != 200:
                    raise _CommitFailed(503, "STORE_WRITE_FAILED",
                                        "The edit built, but could not be saved to the "
                                        "design's history, so it was not applied. Try again.")
                return {"id": design_id, "revision": int(saved.get("revision") or current + 1)}
            return compose(chain + [revision_text]), commit_revision

        return compose(chain), None

    def _prompt_text(self, body: dict, rid: str) -> str | None:
        prompt = (body.get("prompt") or "").strip()
        if len(prompt) < 4:
            self._fail(422, "PROMPT_TOO_SHORT",
                       "Describe what you want to engineer in a few more words.", rid)
            return None
        if len(prompt) > MAX_PROMPT_CHARS:
            self._fail(422, "PROMPT_TOO_LONG",
                       f"Keep the description under {MAX_PROMPT_CHARS} characters.", rid)
            return None
        return prompt

    def _spec_or_error(self, prompt: str, season: str, rid: str, *, use_model: bool,
                       design_type: str = "auto"):
        """Build a design, converting a refusal into the right HTTP answer.

        A request the gate refuses is NOT a 500: it is a 422 carrying the questions or the
        rule that was broken, so the UI can ask instead of pretending to have built it. The
        same is true of a mechanical request naming a part this library does not generate —
        it comes back as an UNSUPPORTED_REQUEST listing what IS generated, which is a far
        more useful answer than a robot nobody asked for.
        """
        from app.services.design_router import DesignNotSupported  # noqa: PLC0415
        from app.services.robot_spec import DesignNotBuildable  # noqa: PLC0415
        try:
            return make_spec(prompt, season, use_model=use_model,
                             design_type=design_type), None
        except DesignNotSupported as exc:
            payload = exc.payload
            self._fail(422, "UNSUPPORTED_REQUEST", payload.get("message", "Not supported."),
                       rid, state="unsupported", designType=payload.get("designType"),
                       supported=payload.get("supported", []),
                       questions=payload.get("questions", []))
            _log("design.unsupported", requestId=rid, designType=payload.get("designType"))
            return None, True
        except DesignNotBuildable as exc:
            g = exc.gate
            code = {"rejected": "DESIGN_RULE_VIOLATION",
                    "clarification_required": "CLARIFICATION_REQUIRED",
                    "unsupported": "UNSUPPORTED_REQUEST"}.get(g["state"], "INVALID_REQUEST")
            self._fail(422, code, g.get("reason", "This request cannot be built as written."),
                       rid, state=g["state"], questions=g.get("questions", []),
                       violations=g.get("violations", []))
            _log("design.refused", requestId=rid, state=g["state"])
            return None, True
        except Exception as exc:
            # Detail stays in the log, tied to the request id; the caller gets a generic message.
            _log("design.error", requestId=rid, error=type(exc).__name__, detail=str(exc)[:300])
            self._fail(500, "GENERATION_FAILED",
                       "The design could not be generated. Quote the request id if it persists.",
                       rid)
            return None, True

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path in ("/favicon.ico", "/robots.txt"):
            self._send(200, b"", "text/plain")
            return
        if parsed.path == "/viewer.js" or parsed.path == "/studio-viewer.js":
            # Re-read from disk when running locally so edits show up without a restart;
            # on Vercel the import-time copy is used (read-only filesystem, cold start).
            body = VIEWER_JS
            if os.environ.get("KALE_DEV"):
                try:
                    with open(os.path.join(_ROOT, "studio-viewer.js"), encoding="utf-8") as fh:
                        body = fh.read()
                except OSError:
                    pass
            self._send(200, body.encode(), "application/javascript; charset=utf-8")
            return
        if qs.get("fs", ["0"])[0] == "1":
            # FIXED (2026-08-06): this used to generate and return the whole FeatureScript to
            # anyone, with `Access-Control-Allow-Origin: *`, because "it is generated output
            # from a public prompt, so there is nothing here to protect". That was wrong on
            # two counts: it is an unauthenticated compute endpoint anyone could spend, and
            # the prompt travelled in the URL. Generation now lives on POST /api/studio/designs.
            # The legacy shape is kept only to answer honestly instead of 404-ing.
            self._fail(401, "AUTH_REQUIRED",
                       "FeatureScript export moved to POST /api/studio/designs/exports/featurescript "
                       "and requires a signed-in session.", _request_id())
            return
        if qs.get("step", ["0"])[0] == "1" and qs.get("prompt"):
            # Same story as fs=1: the prompt used to ride in the query string. Exports are
            # POST-only now so nothing private lands in history or access logs.
            self._fail(410, "ENDPOINT_MOVED",
                       "STEP export moved to POST /api/studio/designs/exports/step.", _request_id())
            return
        if qs.get("step", ["0"])[0] == "1":
            # The design as an editable STEP download — the no-account-needed way into
            # Onshape (or any CAD): drag the file into a document and every part arrives
            # as a named, coloured, editable solid. Generated by the model server's
            # geometry worker; signed-in only, because it spends real compute.
            if not _cookie_is_signed_in(self.headers.get("Cookie") or ""):
                self._send(401, json.dumps({"error": "Sign in first."}).encode(),
                           "application/json")
                return
            prompt = (qs.get("prompt", [""])[0] or "").strip()
            if len(prompt) < 4:
                self._send(400, json.dumps({"error": "prompt too short"}).encode(),
                           "application/json")
                return
            season = (qs.get("season", [""])[0] or "").strip()
            if season not in SELECTABLE:
                season = ""
            base = get_settings().inference_url
            if not base:
                self._send(503, json.dumps({"error": "The geometry server is offline right "
                                            "now - try again shortly, or use Copy "
                                            "FeatureScript."}).encode(), "application/json")
                return
            import urllib.parse  # noqa: PLC0415
            import urllib.request  # noqa: PLC0415
            url = (base.rstrip("/") + "/export/step?"
                   + urllib.parse.urlencode({"prompt": prompt[:5000], "season": season}))
            token = os.environ.get("INFERENCE_TOKEN", "")
            request = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {token}"} if token else {})
            try:
                with urllib.request.urlopen(request, timeout=250) as resp:
                    data = resp.read()
                    dispo = resp.headers.get("Content-Disposition",
                                             'attachment; filename="kale-robot.step"')
                    # Wire header, not branding: the STEP worker on the VM sends
                    # this name and renaming it here would break the export until that
                    # service is redeployed to match.
                    parts = resp.headers.get("X-Kale-Parts", "")
                # STEP is verbose text that gzips ~10x, and a full robot sits right at the
                # function's response-size ceiling uncompressed. The browser inflates it
                # transparently, so the saved file is plain .step.
                import gzip  # noqa: PLC0415
                self._send(200, gzip.compress(data, 6), "application/step",
                           extra={"Content-Disposition": dispo, "X-Kale-Parts": parts,
                                  "Content-Encoding": "gzip"})
            except Exception:
                self._send(503, json.dumps({"error": "The geometry server did not answer - "
                                            "it may be waking up. Try again in a minute, "
                                            "or use Copy FeatureScript."}).encode(),
                           "application/json")
            return
        if qs.get("seasons", ["0"])[0] == "1":
            # Static per deploy and public by design; an hour of CDN cache spares a
            # function invocation on every studio page load.
            self._send(200, json.dumps({"seasons": season_options(),
                                        "default": SELECTABLE[0] if SELECTABLE else ""}).encode(),
                       "application/json",
                       extra={"Cache-Control": "public, max-age=3600"})
            return
        if qs.get("json", ["0"])[0] == "1":
            # Was authenticated but still carried the prompt in the URL. POST /api/studio/designs.
            self._fail(410, "ENDPOINT_MOVED",
                       "Design generation moved to POST /api/studio/designs so prompts stay out of "
                       "URLs and logs.", _request_id())
            return
        # One nonce per response, stamped on the three inline scripts and named in the CSP.
        nonce = _new_nonce()
        self._send(200, PAGE.replace("__CSP_NONCE__", nonce).encode(),
                   "text/html; charset=utf-8", nonce=nonce)

    def do_POST(self) -> None:  # noqa: N802
        """The design API. Every route here requires a session and takes a JSON body.

            POST /api/studio/designs                          → generate (or refuse with questions)
            POST /api/studio/designs/exports/featurescript     → FeatureScript source
            POST /api/studio/designs/exports/step              → STEP assembly
            POST /api/studio/designs/onshape                   → publish to the caller's Onshape

        Prompts travel in the body, never the URL. Exports are refused when the design fails
        a hard season rule, so an illegal robot cannot be handed to a manufacturing workflow.
        """
        rid = _request_id()
        path = urlparse(self.path).path.rstrip("/") or "/"
        legacy = path in ("/studio", "/api/studio", "/")
        known = {"/api/studio/designs", "/api/studio/designs/exports/featurescript",
                 "/api/studio/designs/exports/step", "/api/studio/designs/onshape"}
        if path not in known and not legacy:
            self._fail(404, "NOT_FOUND", "No such endpoint.", rid)
            return
        if not self._require_auth(rid) or not self._same_origin(rid):
            return
        if not _rate_ok(_client_key(self.headers)):
            self._fail(429, "RATE_LIMITED", "Too many requests — wait a minute and retry.", rid)
            return
        body = self._read_json(rid)
        if body is None:
            return

        # The legacy /studio POST only ever meant "publish to Onshape".
        if legacy:
            path = "/api/studio/designs/onshape" if body.get("onshape") else "/api/studio/designs"

        season = (body.get("season") or "").strip()
        if season not in SELECTABLE:
            season = ""
        # The studio's type selector. "auto" means classify it; anything else is the user
        # telling us directly and is honoured. An unknown value falls back to auto rather
        # than erroring, because a stale client should still be able to generate.
        from app.services.design_intent import DESIGN_TYPES  # noqa: PLC0415
        design_type = (body.get("designType") or "auto").strip().lower()
        if design_type not in DESIGN_TYPES and design_type != "auto":
            design_type = "auto"
        # Exports never write: strip revision/restore/persist intents so an export
        # can only ever read the exact stored chain (pinned by baseRevision).
        if path != "/api/studio/designs":
            body = {k: body.get(k) for k in ("designId", "baseRevision", "prompt", "onshape",
                                             "access_key", "secret_key", "base_url", "name",
                                             "designType")}
        prompt, commit = self._resolve_design(body, season, rid)
        if prompt is None:
            return

        import time  # noqa: PLC0415
        started = time.time()

        if path == "/api/studio/designs":
            spec, failed = self._spec_or_error(prompt, season, rid, use_model=True,
                                               design_type=design_type)
            if failed:
                return
            if commit is not None:
                # Build first, write second: the store only learns about a revision,
                # restore or new design that actually validated and built.
                try:
                    spec["design"] = commit(spec)
                except _CommitFailed as cf:
                    self._fail(cf.status, cf.code, str(cf), rid)
                    return
            spec["request_id"] = rid
            _log("design.built", requestId=rid, season=season or "inferred",
                 parts=spec.get("cad", {}).get("feature_total"),
                 rules_ok=spec.get("rule_report", {}).get("ok"),
                 ms=int((time.time() - started) * 1000))
            self._send(200, json.dumps(spec).encode(), "application/json")
            return

        # ── exports ──────────────────────────────────────────────────────────
        spec, failed = self._spec_or_error(prompt, season, rid, use_model=False,
                                           design_type=design_type)
        if failed:
            return
        report = spec.get("rule_report") or {}
        if report.get("export_blocked"):
            first = (report.get("failed") or [{}])[0]
            self._fail(422, "DESIGN_RULE_VIOLATION",
                       first.get("detail") or "The design fails a season rule check.", rid,
                       failed=[{"rule": c.get("rule"), "check": c.get("check"),
                                "measured": c.get("measured"), "limit": c.get("limit"),
                                "unit": c.get("unit"), "detail": c.get("detail")}
                               for c in report.get("failed", [])])
            _log("export.blocked", requestId=rid,
                 rules=[c.get("rule") for c in report.get("failed", [])])
            return

        name = spec.get("name", "Rams FRC Robot")
        if path == "/api/studio/designs/exports/featurescript":
            source = build_featurescript(spec, name)
            _log("export.featurescript", requestId=rid, bytes=len(source),
                 ms=int((time.time() - started) * 1000))
            self._send(200, json.dumps({"name": name, "source": source,
                                        "ruleReport": report}).encode(), "application/json")
            return

        if path == "/api/studio/designs/exports/step":
            base = get_settings().inference_url
            if not base:
                self._fail(503, "GEOMETRY_SERVER_OFFLINE",
                           "The geometry server is offline right now.", rid)
                return
            import urllib.parse  # noqa: PLC0415
            import urllib.request  # noqa: PLC0415
            url = (base.rstrip("/") + "/export/step?"
                   + urllib.parse.urlencode({"prompt": prompt, "season": season}))
            token = os.environ.get("INFERENCE_TOKEN", "")
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {token}"} if token else {})
            try:
                with urllib.request.urlopen(req, timeout=250) as resp:
                    data = resp.read()
                    dispo = resp.headers.get("Content-Disposition",
                                             'attachment; filename="kale-robot.step"')
                    parts = resp.headers.get("X-Kale-Parts", "")
                import gzip  # noqa: PLC0415
                _log("export.step", requestId=rid, bytes=len(data), parts=parts,
                     ms=int((time.time() - started) * 1000))
                self._send(200, gzip.compress(data, 6), "application/step",
                           extra={"Content-Disposition": dispo, "X-Kale-Parts": parts,
                                  "Content-Encoding": "gzip"})
            except Exception as exc:
                _log("export.step.failed", requestId=rid, error=type(exc).__name__)
                self._fail(503, "EXPORT_FAILED",
                           "The geometry server did not answer. Try again shortly.", rid)
            return

        # ── Onshape publish ──────────────────────────────────────────────────
        access = (body.get("access_key") or "").strip()
        secret = (body.get("secret_key") or "").strip()
        if not access or not secret:
            self._fail(422, "ONSHAPE_KEYS_REQUIRED",
                       "Both Onshape API keys are required.", rid)
            return
        try:
            source = build_featurescript(spec, name)
            result = _onshape_publish(access, secret, name, source)
            if not _safe_onshape_url(result.get("url", "")):
                self._fail(502, "ONSHAPE_BAD_URL",
                           "Onshape returned an unexpected document URL.", rid)
                return
            _log("export.onshape", requestId=rid, ms=int((time.time() - started) * 1000))
            self._send(200, json.dumps(result).encode(), "application/json")
        except Exception as exc:
            # Never echo the exception: an Onshape auth failure can quote the credential.
            _log("export.onshape.failed", requestId=rid, error=type(exc).__name__)
            self._fail(502, "ONSHAPE_FAILED",
                       "Onshape rejected the request. Check the keys and try again.", rid)
        finally:
            access = secret = ""  # drop the references as soon as the request is done


# ─────────────────────────────────────────────────────────────────────────────
# The page. Prompt in; the engineered design and its own 3D viewer out.
# ─────────────────────────────────────────────────────────────────────────────
PAGE = r"""<!doctype html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Design Studio | Rams Forge</title>
<meta name="description" content="Describe hardware at any scale, a bearing block, a subsystem, a PCB or a complete FRC robot, and get an engineered design with a one-to-one 3D model.">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700&family=Barlow+Condensed:wght@600;700;900&family=Share+Tech+Mono&display=swap');
  :root{
    --bg:#080800;--surface:#0f0f00;--surface-2:#141400;--ink:#f5f5e8;--muted:#8a8a70;
    --line:rgba(255,214,0,.18);--line-strong:rgba(255,214,0,.32);--brand:#ffd600;--brand-hover:#fff06a;--brand-soft:rgba(255,214,0,.12);
    --on-brand:#000;
    --danger:#ff6b5e;--warning:#ffc247;--sans:Barlow,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
    --display:"Barlow Condensed","Arial Narrow",Arial,sans-serif;
    --mono:"Share Tech Mono",ui-monospace,"SFMono-Regular",Menlo,Consolas,monospace;
  }
  *{box-sizing:border-box}
  html,body{height:100%;margin:0}
  body{background-color:var(--bg);background-image:linear-gradient(rgba(255,214,0,.05) 1px,transparent 1px),linear-gradient(90deg,rgba(255,214,0,.05) 1px,transparent 1px);background-size:40px 40px;color:var(--ink);font-family:var(--sans);-webkit-font-smoothing:antialiased;overflow:hidden}
  a{color:inherit;text-decoration:none}
  .app{position:fixed;inset:0;display:grid;grid-template-rows:auto 1fr}
  .bar{display:flex;align-items:center;gap:14px;padding:0 18px;min-height:56px;border-bottom:1px solid var(--line);background:color-mix(in srgb,var(--bg) 92%,transparent);backdrop-filter:blur(10px);z-index:8}
  .brand{display:flex;align-items:center;gap:8px;font:900 19px var(--display);letter-spacing:.02em;text-transform:uppercase;color:var(--ink)}.brand b{color:var(--brand)}
  .brand b{color:var(--brand);font-weight:720}
  .bar-gap{flex:1}
  .prompt select{background:var(--surface);border:1px solid var(--line-strong);border-radius:9px;color:var(--ink);padding:9px 10px;font:600 13px var(--sans);cursor:pointer}
  .prompt select:focus{outline:none;border-color:var(--brand)}
  .btn{appearance:none;border:1px solid var(--line-strong);background:transparent;color:var(--ink);font:680 13px var(--sans);padding:9px 14px;border-radius:9px;cursor:pointer;white-space:nowrap;transition:.14s}
  .btn:hover{border-color:var(--brand);color:var(--brand)}
  .btn.primary{background:var(--brand);color:var(--on-brand);border-color:var(--brand)}
  .btn.primary:hover{background:var(--brand-hover)}
  .btn.on{background:var(--brand);color:var(--on-brand);border-color:var(--brand)}
  .stage{position:relative;min-height:0;display:grid;grid-template-columns:340px 1fr}
  /* Closed by default: the robot is the point, the numbers are there when you want them. */
  .stage.closed{grid-template-columns:0 1fr}
  /* position:relative matters more than it looks. `.empty` is inset:0 absolute, so without a
     positioned parent the dossier's empty state resolved against `.stage` and rendered
     centred over the viewport, on top of the viewport's own empty text. */
  .dossier{position:relative;border-right:1px solid var(--line);overflow:auto;padding:16px;background:var(--surface)}
  .stage.closed .dossier{padding:0;border-right:0;overflow:hidden;visibility:hidden}
  .dtoggle{position:absolute;top:50%;left:0;transform:translateY(-50%);z-index:7;width:44px;height:58px;display:grid;place-items:center;border:1px solid var(--line-strong);border-left:0;border-radius:0 9px 9px 0;background:var(--surface);color:var(--muted);font:700 15px var(--sans);cursor:pointer;padding:0;transition:color .14s,border-color .14s}
  .dtoggle:hover{color:var(--brand);border-color:var(--brand)}
  .stage:not(.closed) .dtoggle{left:340px}
  .dossier h2{font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);margin:18px 0 8px}
  .dossier h2:first-child{margin-top:0}
  .name{font-size:19px;font-weight:740;letter-spacing:-.02em;margin:0}
  .sub{color:var(--muted);font-size:12px;margin:3px 0 6px}
  .kv{display:grid;grid-template-columns:auto 1fr;gap:5px 12px;font-size:12.5px}
  .kv dt{color:var(--muted)} .kv dd{margin:0;text-align:right;font-variant-numeric:tabular-nums}
  .chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px}
  .chip{font-size:11px;padding:4px 8px;border-radius:999px;border:1px solid var(--line-strong);color:var(--muted);cursor:pointer}
  .chip:hover{border-color:var(--brand);color:var(--brand)}
  .sslist{margin:0;padding:0;list-style:none}
  .sslist li{border-top:1px solid var(--line);padding:9px 0}
  .sslist li[data-asm]:hover b{color:var(--brand)}
  .sslist li.sel{box-shadow:inset 3px 0 0 var(--brand);padding-left:9px}
  .sslist b{font-size:13px} .sslist span{display:block;color:var(--muted);font-size:12px;margin-top:2px}
  .note{color:var(--muted);font-size:12px;line-height:1.5;margin:6px 0 0}
  /* Reference material the design is derived from, folded away by default. It is kept rather
     than trimmed because every row carries the rule it came from, which is the part worth
     reading once you care, but it should not be the first thing between you and the robot. */
  .dossier details{margin:18px 0 0;border-top:1px solid var(--line);padding-top:8px}
  .dossier details summary{font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);cursor:pointer;list-style:none;display:flex;justify-content:space-between;gap:10px;align-items:baseline}
  .dossier details summary::-webkit-details-marker{display:none}
  .dossier details summary:hover{color:var(--brand)}
  .dossier details summary::after{content:'show';font-size:11px;letter-spacing:.04em;text-transform:none;color:var(--brand)}
  .dossier details[open] summary::after{content:'hide'}
  .dossier details > *:first-child + *{margin-top:8px}
  .verify{color:var(--warning);font-size:11px;border-top:1px solid var(--line);padding-top:8px;margin-top:12px;line-height:1.45}
  .view{position:relative;min-height:0;display:grid;grid-template-rows:minmax(0,1fr) auto;background:rgba(8,8,0,.62)}
  .viewport{position:relative;min-height:0;overflow:hidden}
  #scene{position:absolute;inset:0;display:block}
  .ctrls{position:absolute;left:14px;top:14px;z-index:5;display:flex;flex-direction:column;gap:8px;width:190px}
  .panel{border:1px solid var(--line-strong);border-radius:11px;background:color-mix(in srgb,var(--surface) 91%,transparent);backdrop-filter:blur(12px);box-shadow:0 14px 34px rgba(45,48,43,.13);padding:10px}
  .panel .row{display:flex;gap:7px}.panel .row .btn{flex:1;text-align:center;padding:7px 6px}
  .panel .btn{width:100%;margin-top:7px;text-align:center}
  .panel .btn:first-of-type{margin-top:0}
  .slab{font-size:11px;color:var(--muted);display:flex;justify-content:space-between;margin:9px 0 3px}
  input[type=range]{width:100%;accent-color:var(--brand)}
  .empty{position:absolute;inset:0;display:grid;place-items:center;color:var(--muted);font-size:14px;text-align:center;padding:20px}
  .spin{width:32px;height:32px;border:3px solid var(--line-strong);border-top-color:var(--brand);border-radius:50%;animation:sp 1s linear infinite;margin:0 auto 12px}
  @keyframes sp{to{transform:rotate(360deg)}}
  .hint{position:absolute;left:50%;bottom:12px;transform:translateX(-50%);z-index:4;color:var(--muted);font-size:12px;background:color-mix(in srgb,var(--surface) 88%,transparent);border:1px solid var(--line);padding:5px 11px;border-radius:999px}
  /* The prompt sits at the bottom of the stage, under the robot it describes. Both boxes are
     textareas that grow line by line, so a long requirement stays readable in full instead of
     scrolling sideways inside a one-line field. */
  .dock{position:relative;z-index:7;border-top:1px solid var(--line-strong);background:var(--surface);padding:11px 16px 13px;box-shadow:0 -12px 30px rgba(45,48,43,.08)}
  .dock-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0 2px 9px}
  .dock-tabs{display:flex;gap:4px;background:var(--surface-2);border-radius:9px;padding:4px}
  .dock-tabs button{appearance:none;border:0;background:transparent;color:var(--muted);font:650 12.5px var(--sans);padding:6px 12px;border-radius:6px;cursor:pointer}
  .dock-tabs button.on{background:var(--surface);color:var(--ink);box-shadow:0 1px 4px rgba(45,48,43,.14)}
  .dock-tabs button:disabled{opacity:.45;cursor:default}
  .dock-head>span{font:600 11px var(--mono);color:var(--brand);text-align:right}
  #dock[data-mode=new] #revision-form,#dock[data-mode=edit] #form{display:none}
  .composer{display:flex;align-items:flex-end;gap:9px}
  .composer textarea{min-width:0;flex:1;resize:none;overflow-y:auto;max-height:38vh;background:var(--bg);border:1px solid var(--line-strong);border-radius:11px;color:var(--ink);padding:11px 13px;font:500 14px/1.55 var(--sans);white-space:pre-wrap}
  .composer textarea:focus{outline:none;border-color:var(--brand)}
  .composer-side{display:flex;flex-direction:column;gap:7px;flex:0 0 auto}
  .composer-side .btn{width:100%}
  .revision-note{color:var(--muted);font-size:11px;line-height:1.5;margin:7px 2px 0}
  /* Reachable by a screen reader, invisible on screen: the composer's design puts the
     instruction in the placeholder, which is not an accessible name. */
  .sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
  .questions{margin:10px 0 0;padding:0 0 0 18px;color:var(--ink);font-size:12px;line-height:1.6}
  .questions li{margin:3px 0}
  @media(prefers-reduced-motion:reduce){
    /* The viewer's idle spin and the loading pulse are motion the user asked not to see.
       Content must not depend on either, so both simply stop. */
    .spin{animation:none}
    *{scroll-behavior:auto!important}
  }
  .history{display:flex;align-items:center;gap:10px;margin:8px 2px 0}
  .history-label{color:var(--muted);font-size:11px}
  /* 44px minimum touch target, per WCAG 2.5.8, without changing the visual size */
  .btn-small{font-size:11px;padding:4px 10px;min-height:44px;min-width:44px}
  .revision-examples{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.revision-examples button{appearance:none;border:1px solid var(--line);background:var(--bg);color:var(--muted);border-radius:999px;padding:5px 8px;font:600 10px var(--sans);cursor:pointer}.revision-examples button:hover{border-color:var(--brand);color:var(--brand)}
  /* The design-type selector. Same segmented language as the dock tabs, so the studio still
     reads as one tool rather than a settings panel bolted to a prompt box. */
  .kinds{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px}
  .kinds button{appearance:none;border:1px solid var(--line);background:var(--bg);color:var(--muted);border-radius:7px;padding:5px 10px;font:700 10px var(--sans);letter-spacing:.08em;text-transform:uppercase;cursor:pointer}
  .kinds button:hover{border-color:var(--brand);color:var(--brand)}
  .kinds button.on{background:var(--brand);border-color:var(--brand);color:var(--on-brand)}
  /* Provenance chips. Deliberately quiet: they must be readable on every row without
     turning the dossier into a traffic light. */
  .prov{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:0 5px;font:700 9px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--muted);vertical-align:1px}
  .prov.ok{border-color:color-mix(in srgb,var(--brand) 45%,var(--line));color:var(--brand)}
  /* Tuned for the near-black: the previous amber and oxblood were picked against a cream
     background and both sink into this one. */
  .prov.warn{border-color:color-mix(in srgb,var(--warning) 45%,var(--line));color:var(--warning)}
  .prov.bad{border-color:color-mix(in srgb,var(--danger) 45%,var(--line));color:var(--danger)}
  .list{margin:6px 0 0;padding-left:16px;color:var(--muted);font-size:12px;line-height:1.6}
  .list b{color:var(--ink);font-weight:700}
  @media (max-width:820px){.stage{grid-template-columns:1fr;grid-template-rows:44% 1fr}.stage.closed{grid-template-rows:0 1fr}.stage:not(.closed) .dtoggle{left:0}.dossier{border-right:0;border-bottom:1px solid var(--line)}.ctrls{width:150px}.dock-head>span{display:none}.composer{flex-direction:column;align-items:stretch}.composer-side{flex-direction:row}.composer-side select{flex:1}.revision-examples{display:none}.kinds button{padding:5px 7px}}
  #gate{position:fixed;inset:0;z-index:40;display:grid;place-items:center;background:color-mix(in srgb,var(--bg) 86%,transparent);backdrop-filter:blur(10px)}
  #gate[hidden]{display:none}
  #onshape-modal{position:fixed;inset:0;z-index:30;display:grid;place-items:center;background:color-mix(in srgb,var(--bg) 82%,transparent);backdrop-filter:blur(8px)}
  #onshape-modal[hidden]{display:none}
  .gate-card{width:min(92vw,380px);border:1px solid var(--line-strong);border-radius:14px;background:var(--surface);box-shadow:0 30px 70px rgba(45,48,43,.2);padding:22px}
  .gate-card h1{font-size:19px;letter-spacing:-.02em;margin:0}
  .gate-card p{color:var(--muted);font-size:13px;line-height:1.55;margin:8px 0 0}
  .gate-tabs{display:grid;grid-template-columns:1fr 1fr;gap:6px;background:var(--surface-2);border-radius:9px;padding:5px;margin-top:16px}
  .gate-tabs button{appearance:none;border:0;background:transparent;color:var(--muted);font:650 13px var(--sans);padding:8px;border-radius:6px;cursor:pointer}
  .gate-tabs button.on{background:var(--surface);color:var(--ink);box-shadow:0 2px 8px rgba(0,0,0,.3)}
  #gate label{display:block;font:600 11px var(--sans);color:var(--muted);margin:13px 0 5px}
  #gate input{width:100%;background:var(--bg);border:1px solid var(--line-strong);border-radius:8px;color:var(--ink);padding:9px 11px;font:500 14px var(--sans)}
  #gate input:focus{outline:none;border-color:var(--brand)}
  #gate .btn.primary{width:100%;margin-top:16px;padding:11px}
  #gate-err{display:none;color:var(--danger);font-size:12px;line-height:1.5;margin:12px 0 0;border:1px solid color-mix(in srgb,var(--danger) 40%,transparent);border-radius:8px;padding:8px 10px}
  #who{display:none;align-items:center;gap:8px;color:var(--muted);font-size:12px;white-space:nowrap}
  #who b{color:var(--ink);font-weight:650}
  #who button{appearance:none;border:0;background:none;color:var(--muted);font:600 12px var(--sans);cursor:pointer;padding:4px}
  #who button:hover{color:var(--danger)}
</style>
</head>
<body>
<div class="app">
  <div class="bar">
    <a class="brand" href="/"><img src="/rams-forge-mark.svg" width="22" height="22" alt="" aria-hidden="true">RAMS <b>FORGE</b></a>
    <a class="btn" href="/app" title="Upload a KiCad project, netlist or BOM; 40+ deterministic checks find problems and the review model explains them" style="border:0;padding:9px 6px">PCB review</a>
    <span class="bar-gap"></span>
    <span id="who"><b id="who-name"></b><button id="signout" type="button" title="Sign out" style="min-width:44px;min-height:44px">sign out</button></span>
  </div>
  <div class="stage closed" id="stage">
    <button class="dtoggle" id="dtoggle" type="button" aria-expanded="false" title="Show design details">›</button>
    <div class="dossier" id="dossier">
      <div class="empty" id="dossier-empty">What do you want to engineer?<br>A robot, a subsystem, a single part, or a PCB.</div>
    </div>
    <div class="view">
      <div class="viewport">
        <canvas id="scene" role="img" aria-label="Interactive 3D preview of the generated design. The dossier panel lists every assembly and part as text."></canvas>
        <div class="ctrls" id="ctrls" style="display:none">
          <div class="panel">
            <div class="row"><button class="btn" data-view="iso">Iso</button><button class="btn" data-view="top">Top</button><button class="btn" data-view="front">Front</button><button class="btn" data-view="side">Side</button></div>
            <div class="row"><button class="btn" id="b-zin" title="Zoom in (or scroll on the model)">Zoom +</button><button class="btn" id="b-zout" title="Zoom out (or scroll on the model)">Zoom −</button><button class="btn" id="b-fit" title="Frame the whole robot again">Fit</button></div>
            <button class="btn" id="b-explode">Exploded</button>
            <button class="btn" id="b-run">Run mechanisms</button>
            <button class="btn" id="b-onshape" title="Download this design as editable STEP solids for Onshape or any CAD">Download CAD</button>
            <div class="slab"><span>Section cut</span><span id="cutv">off</span></div>
            <input type="range" id="cut" min="0" max="100" value="0" aria-label="Section cut depth">
            <div class="slab" id="picked" style="display:none"></div>
          </div>
        </div>
        <!-- aria-live: a generation that finishes, fails or is refused must announce itself.
             Without it a screen-reader user hears nothing between pressing Generate and
             tabbing back through the whole page to discover what happened. -->
        <!-- The message lives in its own span: writing to the wrapper would delete the
             spinner element that sits beside it. -->
        <div class="empty" id="view-empty" role="status" aria-live="polite" aria-atomic="true"><div><div class="spin" style="display:none" id="spin"></div><span id="view-msg">Your generated model appears here.</span></div></div>
        <div class="hint" id="hint" style="display:none">drag to orbit · scroll to zoom · click a part</div>
      </div>
      <div class="dock" id="dock" data-mode="new">
        <div class="dock-head">
          <div class="dock-tabs">
            <button type="button" id="tab-new" class="on">New design</button>
            <button type="button" id="tab-edit" disabled title="Generate a design first">Edit this design</button>
          </div>
          <span id="revision-state"></span>
        </div>
        <form class="prompt" id="form">
          <div class="kinds" role="radiogroup" aria-label="What kind of design">
            <button type="button" data-kind="auto" class="on" aria-checked="true" role="radio" title="Rams Forge works out what you are asking for">Auto</button>
            <button type="button" data-kind="robot" aria-checked="false" role="radio" title="A complete FRC robot">Robot</button>
            <button type="button" data-kind="subsystem" aria-checked="false" role="radio" title="One mechanism: elevator, intake, shooter, arm">Subsystem</button>
            <button type="button" data-kind="mechanical_part" aria-checked="false" role="radio" title="A single part: bearing block, shaft, plate, gusset">Part</button>
            <button type="button" data-kind="pcb" aria-checked="false" role="radio" title="A circuit board from a requirement">PCB</button>
          </div>
          <div class="composer">
            <label class="sr-only" for="q">What do you want to engineer?</label>
            <textarea id="q" rows="1" autocomplete="off" placeholder="Design a bearing block for a 1/2 in hex shaft using two 1.125 in OD bearings..."></textarea>
            <div class="composer-side">
              <label class="sr-only" for="season">Game season</label>
              <select id="season" title="The game this robot is designed for. It sets the gamepiece, the goal height, the climb reach and the frame perimeter budget"></select>
              <button class="btn primary" type="submit" id="generate">Generate</button>
              <button class="btn" type="button" id="cancel" hidden>Cancel</button>
            </div>
          </div>
          <p class="revision-note">Describe a robot, subsystem, mechanical part, PCB or custom assembly. Enter generates. Shift + Enter starts a new line.</p>
          <div class="revision-examples" aria-label="Example designs">
            <button type="button" data-example="Design a bearing block for a 1/2 in hex shaft.">Bearing block</button>
            <button type="button" data-example="Create a two-Kraken gearbox plate.">Two-Kraken gearbox</button>
            <button type="button" data-example="Design a 4 in compliant-wheel intake roller.">Intake roller</button>
            <button type="button" data-example="Build a 4-layer PCB for CAN sensors with 12V input.">CAN sensor board</button>
            <button type="button" data-example="Design a NEO motor mounting plate.">Motor plate</button>
            <button type="button" data-example="Build a complete FRC robot with swerve, an intake and a shooter.">Complete robot</button>
          </div>
        </form>
        <form class="revision" id="revision-form">
          <div class="composer">
            <label class="sr-only" for="revision-q">Describe the edit to apply</label>
            <textarea id="revision-q" rows="1" autocomplete="off" placeholder="Make it 3.5 in wide, use a slip fit, add a 5 V rail..."></textarea>
            <div class="composer-side">
              <button class="btn primary" id="revision-apply" type="submit">Apply edit</button>
            </div>
          </div>
          <p class="revision-note" id="revision-note">Each edit rebuilds the validated specification and every editable part.</p>
          <div class="history" id="history" style="display:none"></div>
          <div class="revision-examples" id="revision-examples" aria-label="Example design edits"></div>
        </form>
      </div>
    </div>
  </div>
</div>

<div id="onshape-modal" hidden>
  <div class="gate-card" role="dialog" aria-modal="true" aria-labelledby="os-title" style="width:min(92vw,440px)">
    <h1 id="os-title" style="font-size:19px;letter-spacing:-.02em;margin:0">Get this design into CAD</h1>
    <p style="color:var(--muted);font-size:13px;line-height:1.55;margin:8px 0 0">Download the design as a <b>STEP file</b>. In Onshape, upload it into a document, then <b>right-click the uploaded file's tab → Import… → Import to this document</b>: Onshape stores uploads as plain files until you ask it to translate, so without this step you only see a download card. After the import (a minute or two for a full robot) every part is a separate, named, coloured <b>editable solid</b>; Onshape creates Part Studios and Assemblies as the file's structure needs (or combine into a single Part Studio at import). Fusion, SolidWorks and FreeCAD open the file directly. Structure and gearing carry true dimensions, tooth counts and bores; belts and chains are wrapped loops; motors, gearboxes and electronics are catalog envelopes and their part names say so. Concept geometry to keep engineering, never a frozen mesh, never a claim beyond what was modelled. No account keys, no setup.</p>
    <button class="btn primary" id="os-download" type="button" style="width:100%;margin-top:14px;padding:11px">Download .step</button>
    <div id="os-result" style="display:none;font-size:12px;line-height:1.5;margin:12px 0 0;border:1px solid var(--line);border-radius:8px;padding:8px 10px"></div>
    <details style="margin-top:14px;border-top:1px solid var(--line);padding-top:10px">
      <summary style="font-size:12px;color:var(--muted);cursor:pointer">Advanced: parametric FeatureScript instead</summary>
      <p style="color:var(--muted);font-size:12px;line-height:1.5;margin:8px 0 0">The fully parametric source, every dimension a named variable. Copy it into a Feature Studio yourself, or publish straight into your account with <a href="https://cad.onshape.com/appstore/dev-portal" target="_blank" rel="noopener" style="color:var(--brand);text-decoration:underline">Onshape API keys</a> (used once, never stored).</p>
      <label style="display:block;font:600 11px var(--sans);color:var(--muted);margin:11px 0 5px" for="os-access">Access key</label>
      <input id="os-access" autocomplete="off" style="width:100%;background:var(--bg);border:1px solid var(--line-strong);border-radius:8px;color:var(--ink);padding:9px 11px;font:500 13px var(--mono)">
      <label style="display:block;font:600 11px var(--sans);color:var(--muted);margin:11px 0 5px" for="os-secret">Secret key</label>
      <input id="os-secret" type="password" autocomplete="off" style="width:100%;background:var(--bg);border:1px solid var(--line-strong);border-radius:8px;color:var(--ink);padding:9px 11px;font:500 13px var(--mono)">
      <div style="display:flex;gap:8px;margin-top:12px">
        <button class="btn" id="os-copy" type="button" title="Copy the FeatureScript to paste into any Feature Studio yourself">Copy FeatureScript</button>
        <button class="btn" id="os-create" type="button" style="flex:1">Create in my Onshape</button>
      </div>
    </details>
    <button class="btn" id="os-close" type="button" style="width:100%;margin-top:12px">Close</button>
  </div>
</div>

<div id="gate" hidden>
  <div class="gate-card" role="dialog" aria-modal="true" aria-labelledby="gate-title">
    <h1 id="gate-title">Sign in to the Design Studio</h1>
    <p>Designs are generated for signed-in accounts. Creating one takes a moment. An email is just your key back in.</p>
    <div class="gate-tabs"><button type="button" id="tab-in" class="on">Sign in</button><button type="button" id="tab-up">Create account</button></div>
    <form id="gate-form">
      <div id="f-name" style="display:none"><label for="g-name">Name</label><input id="g-name" autocomplete="name"></div>
      <label for="g-email">Email</label><input id="g-email" type="email" autocomplete="email" required>
      <label for="g-pass">Password</label><input id="g-pass" type="password" autocomplete="current-password" required minlength="8">
      <div id="gate-err"></div>
      <button class="btn primary" type="submit" id="gate-go">Sign in</button>
    </form>
  </div>
</div>

<script nonce="__CSP_NONCE__">
// Account gate: the studio stays hidden until /api/auth/me answers 200. Sessions come from
// the main app's auth API through the same-domain /api proxy, so the cookie is first-party.
(() => {
  const gate = document.getElementById('gate'), err = document.getElementById('gate-err');
  const tabIn = document.getElementById('tab-in'), tabUp = document.getElementById('tab-up');
  const nameRow = document.getElementById('f-name'), go = document.getElementById('gate-go');
  const who = document.getElementById('who'), whoName = document.getElementById('who-name');
  let mode = 'in';
  const setMode = m => { mode = m;
    tabIn.classList.toggle('on', m === 'in'); tabUp.classList.toggle('on', m === 'up');
    nameRow.style.display = m === 'up' ? '' : 'none';
    go.textContent = m === 'in' ? 'Sign in' : 'Create account';
    document.getElementById('g-pass').autocomplete = m === 'in' ? 'current-password' : 'new-password';
    err.style.display = 'none';
  };
  tabIn.onclick = () => setMode('in'); tabUp.onclick = () => setMode('up');
  const open = () => {
    who.style.display = 'none';
    if (window.__openDialog) window.__openDialog(gate, document.getElementById('g-email'));
    else gate.hidden = false;
  };
  const close = user => {
    if (window.__closeDialog) window.__closeDialog(gate); else gate.hidden = true;
    if (user) { whoName.textContent = user.name || user.email || 'Account'; who.style.display = 'flex'; } };
  window.__requireSignIn = open;
  fetch('/api/auth/me').then(r => r.ok ? r.json().then(close) : open()).catch(open);
  document.getElementById('gate-form').addEventListener('submit', async e => {
    e.preventDefault(); err.style.display = 'none';
    const body = { email: document.getElementById('g-email').value.trim(),
                   password: document.getElementById('g-pass').value };
    if (mode === 'up') body.name = document.getElementById('g-name').value.trim();
    try {
      const r = await fetch(mode === 'in' ? '/api/auth/login' : '/api/auth/register', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (!r.ok) { const d = await r.json().catch(() => ({}));
        err.textContent = d.detail || d.error || 'That did not work. Check the details and try again.';
        err.style.display = 'block'; return; }
      close(await r.json());
    } catch (_e) { err.textContent = 'Could not reach the sign-in service.'; err.style.display = 'block'; }
  });
  document.getElementById('signout').addEventListener('click', async () => {
    try { await fetch('/api/auth/logout', { method: 'POST' }); } catch (_e) {}
    open();
  });
})();
</script>

<script nonce="__CSP_NONCE__" type="importmap">
{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}
</script>
<script nonce="__CSP_NONCE__" type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { buildScene } from '/studio-viewer.js';

const $ = s => document.querySelector(s);

// One place that talks to the design API. Every call is a POST with a JSON body (prompts
// never travel in a URL), carries an AbortController so a superseded request is cancelled,
// and refuses to call .json() on a response that is not JSON - an HTML error page used to
// surface as a parse error instead of a readable message.
// One controller per KIND of operation. Generation, exports and Onshape publishing
// are unrelated: starting an export must never abort a running generation, which a
// single shared controller used to do. Cancelling a scope also bumps its ticket, so
// a response already in flight is treated as stale, never applied to a page that
// has moved on.
const __ops = {};
function abortScope(scope){
  const op = __ops[scope];
  if (op){ op.ctl.abort(); op.ticket++; }
}
function abortInflight(){ abortScope('design'); }
function apiPost(path, payload, scope){
  scope = scope || 'design';
  const prev = __ops[scope];
  if (prev) prev.ctl.abort();
  const ctl = new AbortController();
  const op = { ctl, ticket: (prev ? prev.ticket : 0) + 1 };
  __ops[scope] = op;
  const ticket = op.ticket;
  const timer = setTimeout(() => ctl.abort(), 300000);
  return fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify(payload),
    signal: ctl.signal,
  }).then(async res => {
    clearTimeout(timer);
    const type = res.headers.get('Content-Type') || '';
    let data = null;
    if (type.includes('application/json')) { try { data = await res.json(); } catch (_e) { data = null; } }
    return { ok: res.ok, status: res.status, data: data, stale: ticket !== __ops[scope].ticket };
  }).catch(err => {
    clearTimeout(timer);
    if (err && err.name === 'AbortError') return { aborted: true, stale: ticket !== __ops[scope].ticket };
    return { ok: false, status: 0, data: null, network: true, stale: ticket !== __ops[scope].ticket };
  });
}
// Turns a machine-readable error envelope into something a person can act on.
function apiMessage(r){
  const e = (r && r.data && r.data.error) || {};
  const byCode = {
    AUTH_REQUIRED: 'Your session expired - sign in again.',
    RATE_LIMITED: 'Too many requests. Wait a moment and try again.',
    CLARIFICATION_REQUIRED: e.message || 'A few more details are needed.',
    DESIGN_RULE_VIOLATION: e.message || 'That design breaks a season rule.',
    PROMPT_TOO_SHORT: 'Describe what you want to engineer in a few more words.',
    PROMPT_TOO_LONG: 'That description is too long.',
    GEOMETRY_SERVER_OFFLINE: 'The geometry server is offline right now.',
    EXPORT_FAILED: 'The export did not complete. Try again shortly.',
    ONSHAPE_FAILED: 'Onshape rejected the request. Check the keys.',
    GENERATION_FAILED: 'The design could not be generated.',
  };
  if (r && r.network) return 'Network problem - check your connection and retry.';
  if (r && r.status === 0) return 'The request did not complete.';
  return byCode[e.code] || e.message || ('Request failed (HTTP ' + ((r && r.status) || '?') + ').');
}

const form = $('#form'), q = $('#q'), seasonSel = $('#season');

// Cancel is a real cancel: it aborts the in-flight request rather than hiding the
// spinner and letting the response arrive later and overwrite the page.
$('#cancel') && $('#cancel').addEventListener('click', () => {
  abortInflight();
  setGenState('cancelled');
  $('#view-empty').style.display = 'grid';
  setViewMessage('Generation cancelled.');
  q.focus();
});

// The details panel starts closed and its arrow must work straight away, before any design
// exists. It used to be wired inside wireControls(), which only runs once a scene has been
// built, so on a fresh page the arrow did nothing.
//
// Collapsing the panel changes the viewport width without a window resize, and the renderer
// only re-measures its parent on 'resize', so the canvas would keep the old width and render
// the robot stretched. Pump the event across the CSS transition rather than once at the end.
(function wireDossierToggle(){
  const stage = $('#stage'), dtg = $('#dtoggle');
  if (!stage || !dtg) return;
  dtg.addEventListener('click', () => {
    const closed = stage.classList.toggle('closed');
    dtg.textContent = closed ? '›' : '‹';
    dtg.setAttribute('aria-expanded', String(!closed));
    dtg.title = closed ? 'Show design details' : 'Hide design details';
    // The column snaps, but the canvas only re-measures its parent on 'resize'.
    requestAnimationFrame(() => dispatchEvent(new Event('resize')));
  });
})();
// The CAD export dialog must be closable from the moment the page exists. Its buttons used
// to be wired only inside wireControls(), which runs after the first design is generated -
// so if the dialog was ever visible before then, it sat over the whole studio with a Close
// button that did nothing, and every control underneath was unreachable.
// Keyboard users must be able to get into a dialog, stay inside it while it is open,
// and get back to where they were on close. Tab wrapping is done here rather than with
// inert/aria-hidden on the rest of the page, because the studio's background content
// stays visible and a screen reader should still be able to see it is there.
const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select,textarea,summary,[tabindex]:not([tabindex="-1"])';
function trapFocus(dialog){
  return (e) => {
    if (e.key !== 'Tab' || dialog.hidden) return;
    const items = [...dialog.querySelectorAll(FOCUSABLE)].filter(el => el.offsetParent !== null);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first){ e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last){ e.preventDefault(); first.focus(); }
  };
}
function openDialog(dialog, firstFocus){
  dialog.__returnFocus = document.activeElement;
  dialog.hidden = false;
  if (!dialog.__trap){ dialog.__trap = trapFocus(dialog); addEventListener('keydown', dialog.__trap); }
  const target = firstFocus || dialog.querySelector(FOCUSABLE);
  if (target) target.focus();
}
function closeDialog(dialog){
  dialog.hidden = true;
  const back = dialog.__returnFocus;
  if (back && document.contains(back) && back.focus) back.focus();
}
window.__openDialog = openDialog; window.__closeDialog = closeDialog;

(function wireCadModal(){
  const modal = $('#onshape-modal');
  if (!modal) return;
  modal.hidden = true;
  const close = () => {
    // Keys never outlive the dialog, whatever path closed it.
    $('#os-access').value=''; $('#os-secret').value='';
    closeDialog(modal);
  };
  $('#os-close').addEventListener('click', close);
  modal.addEventListener('click', e => { if (e.target === modal) close(); });
  addEventListener('keydown', e => { if (e.key === 'Escape' && !modal.hidden) close(); });
})();
const revisionForm = $('#revision-form'), revisionQ = $('#revision-q');

// Both prompt boxes are textareas so a long requirement wraps and stays visible in full. The
// height is driven off scrollHeight rather than a row count, because the wrap point depends on
// the dock width, and the dock width changes when the details panel opens.
const dock = $('#dock'), tabNew = $('#tab-new'), tabEdit = $('#tab-edit');
function grow(el){
  el.style.height = 'auto';
  el.style.height = el.scrollHeight + 'px';
}
// A grown dock steals height from the viewport, and the renderer only re-measures on 'resize'.
// This is deliberately not called from grow() itself: the resize listener below re-grows both
// boxes, so dispatching from inside grow() would loop forever.
function pump(){ requestAnimationFrame(() => dispatchEvent(new Event('resize'))); }
[q, revisionQ].forEach(el => {
  el.addEventListener('input', () => { grow(el); pump(); });
  // Enter submits the way the old single-line inputs did; Shift+Enter is the new line.
  el.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); el.form.requestSubmit(); }
  });
});
addEventListener('resize', () => { grow(q); grow(revisionQ); });

function setMode(mode){
  dock.dataset.mode = mode;
  tabNew.classList.toggle('on', mode === 'new');
  tabEdit.classList.toggle('on', mode === 'edit');
  const box = mode === 'new' ? q : revisionQ;
  grow(box); pump(); box.focus();
}
tabNew.addEventListener('click', () => setMode('new'));
tabEdit.addEventListener('click', () => { if (!tabEdit.disabled) setMode('edit'); });

// One starting prompt per season. The same words describe a different robot in a different
// game, so handing over last year's example with this year's season selected is worse than
// no example at all.
const SEASON_EXAMPLES = {
  '2026-rebuilt': "27 inch REBUILT robot on MK4i swerve, dual-roller over-bumper intake into a spindexer, turreted hooded shooter, telescoping climber to L3",
  '2025-reefscape': "28 inch REEFSCAPE robot, three-stage cascade elevator to L4, coaxial slapdown intake, wristed carriage arm, deep-cage climb",
  'offseason': "MK5i swerve on Krakens at R2 with a fast dual-roller over-bumper intake and a deep-cage climber",
};
const FALLBACK = SEASON_EXAMPLES['2026-rebuilt'];
// The box starts EMPTY: pre-filling a full robot spec let a new user generate
// without describing anything, and hid whether vague prompts really get questions.
const PART_PLACEHOLDER = 'Design a bearing block for a 1/2 in hex shaft using two 1.125 in OD bearings...';
q.placeholder = PART_PLACEHOLDER;
grow(q);

// ── design type ────────────────────────────────────────────────────────────
// AUTO is the default and means "work out what I am asking for". The selector exists for
// the times the words are ambiguous and the user already knows, "make a gearbox" is a
// reasonable request for either an assembly or the plate that carries it.
let designKind = 'auto';
const kindButtons = Array.from(document.querySelectorAll('[data-kind]'));
kindButtons.forEach(btn => btn.addEventListener('click', () => {
  designKind = btn.dataset.kind;
  kindButtons.forEach(b => {
    const on = b === btn;
    b.classList.toggle('on', on);
    b.setAttribute('aria-checked', on ? 'true' : 'false');
  });
  syncSeasonVisibility();
  applySeasonExample();
}));

// A game season only means something for a complete robot or for a subsystem that handles
// the game piece. Asking someone to pick REEFSCAPE before they can have a bearing housing
// was the clearest sign this tool thought it only made robots. The client mirrors the
// server's rule in `design_intent.season_is_relevant`; the server decides, this only hides.
const GAME_WORDS = /(20\d\d|reefscape|rebuilt|crescendo|charged\s*up|rapid\s*react|season|game manual|field|match)/i;
const GAME_PIECE = /(intake|hopper|indexer|spindexer|shooter|flywheel|launcher|climb\w*|hang\w*|end\s*effector|gripper)/i;
const ROBOT_WORDS = /(robot|drivebase|drive\s*base|chassis|drivetrain|swerve|west\s*coast|tank\s*drive)/i;
function seasonMatters(){
  if (designKind === 'robot') return true;
  if (designKind === 'mechanical_part' || designKind === 'pcb') return false;
  const text = q.value || '';
  if (GAME_WORDS.test(text)) return true;
  if (designKind === 'subsystem') return GAME_PIECE.test(text);
  return ROBOT_WORDS.test(text);
}
function syncSeasonVisibility(){
  const show = seasonMatters();
  seasonSel.style.display = show ? '' : 'none';
  seasonSel.disabled = !show;
}
q.addEventListener('input', syncSeasonVisibility);

let seasonInfo = {};
const KIND_PLACEHOLDER = {
  auto: PART_PLACEHOLDER,
  mechanical_part: PART_PLACEHOLDER,
  pcb: 'Design a PCB that takes 12V input and provides protected 5V 3A and 3.3V 1A outputs...',
  subsystem: 'Design a two-stage cascade elevator reaching 48 in, rope-rigged on two Krakens...',
};
function applySeasonExample(){
  // Only ever replaces the PLACEHOLDER, so a prompt the user typed always survives.
  if (q.value.trim()) { grow(q); return; }
  if (designKind !== 'robot' && KIND_PLACEHOLDER[designKind]) {
    q.placeholder = KIND_PLACEHOLDER[designKind];
    grow(q);
    return;
  }
  const next = SEASON_EXAMPLES[seasonSel.value];
  if (next) q.placeholder = 'Describe a robot, for example: ' + next;
  const s = seasonInfo[seasonSel.value];
  if (s) q.placeholder = `Describe a ${s.label} robot. Gamepiece: ${s.gamepiece}. Frame perimeter budget: ${s.perimeter_in} in`;
  grow(q);
}
seasonSel.addEventListener('change', applySeasonExample);

// The season list is served by the same function; a failure here leaves the selector empty
// and the season is read out of the prompt instead, which still produces a design.
// Rule data failing to load must FAIL CLOSED: a studio that silently continues
// and lets the season be "inferred" is doing FRC rule checks against guesses.
// Generation pauses until seasons load or the user explicitly goes rule-free.
window.__seasonsFailed = false;
function loadSeasons(){
  return fetch('/studio?seasons=1').then(r => r.json()).then(data => {
    // Season names come from the backend; built as elements so a crafted label is text.
    seasonSel.replaceChildren(...data.seasons.map(s => {
      const o = document.createElement('option');
      o.value = s.key;
      o.textContent = s.year ? s.year + ' ' + s.game : s.game;
      return o;
    }));
    data.seasons.forEach(s => { seasonInfo[s.key] = s; });
    seasonSel.value = data.default || '';
    window.__seasonsFailed = false;
    applySeasonExample();
  }).catch(() => {
    window.__seasonsFailed = true;
    seasonSel.replaceChildren((() => {
      const o = document.createElement('option');
      o.value = 'offseason'; o.textContent = 'off-season (no rule checks)';
      return o;
    })());
    setViewMessage('Season rule data did not load. Reload the page to retry, or generate '
                   + 'as off-season, explicitly without competition rule checks.');
  });
}
loadSeasons();

let scene3d = null, currentPrompt = '', currentSpec = null, revisionNumber = 0;
let queuedRevisionPrompt = '';
// The design's identity in the store. History lives there, keyed to the signed-in
// account: the client sends an id, never an ever-growing prompt. All WRITES happen
// server-side, AFTER validation: the client never records a revision itself, so a
// rejected edit cannot enter the history, and Cancel aborts the one request that
// exists. The client only READS the store, for the history panel.
let designId = '';

// The generation lifecycle, in one place. Every transition moves the same three
// controls together, so the UI can never sit on a spinner with an enabled Generate
// button or leave a Cancel button visible after the request has finished.
let genState = 'idle';
function setGenState(next){
  genState = next;
  const busy = next === 'generating';
  const gen = $('#generate'), cancel = $('#cancel');
  if (gen){ gen.disabled = busy; gen.textContent = busy ? 'Generating…' : 'Generate'; }
  if (cancel) cancel.hidden = !busy;
  $('#spin').style.display = busy ? 'block' : 'none';
}

// The empty-state panel is written through here so the spinner, which is a sibling of
// the message, survives every update. Returns the wrapper for anything appending to it.
function setViewMessage(text){
  const msg = $('#view-msg');
  if (msg) msg.textContent = text;
  const wrap = $('#view-empty').querySelector('div');
  wrap.querySelectorAll('.questions').forEach(el => el.remove());
  return wrap;
}

// A refusal that carries questions or rule violations shows them as a list. The server
// already worked out what it needs to know; burying that in one sentence wastes it.
function showQuestions(err, host){
  // Questions arrive as {question, examples}; violations as {detail}. Both are flattened
  // to a sentence here, and the examples come along, they are the fastest way for
  // someone to answer, and dropping them made a refusal feel like a dead end.
  const items = (err.questions || []).map(q => {
    const text = (q && q.question) || String(q);
    const eg = (q && q.examples || []).join(', ');
    return eg ? text + '  e.g. ' + eg : text;
  }).concat((err.violations || []).map(v => (v && (v.detail || v.message)) || String(v)));
  if (!items.length) return;
  const list = document.createElement('ul');
  list.className = 'questions';
  for (const item of items.slice(0, 6)){
    const li = document.createElement('li');
    li.textContent = item;                // textContent: refusal text is model-influenced
    list.appendChild(li);
  }
  host.appendChild(list);
}

async function storeRead(path){
  try {
    const r = await fetch('/api/forge/designs' + path, { credentials: 'same-origin' });
    return { ok: r.ok, status: r.status, data: r.ok ? await r.json() : null };
  } catch (_e) { return { ok: false, status: 0, data: null }; }
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const isRevision = Boolean(queuedRevisionPrompt);
  const prompt = (queuedRevisionPrompt || q.value).trim();
  queuedRevisionPrompt = '';
  if (prompt.length < 4){
    // A too-short prompt must say so, not silently swallow Enter. The live region
    // announces the message for screen-reader users too.
    $('#view-empty').style.display='grid';
    setViewMessage('Describe what you want to engineer in a few more words, then generate.');
    return;
  }
  if (window.__seasonsFailed && seasonSel.value !== 'offseason'){
    $('#view-empty').style.display='grid';
    setViewMessage('Season rule data did not load, so rule-checked generation is paused. '
                   + 'Reload to retry, or select off-season to build explicitly rule-free.');
    return;
  }
  $('#dossier-empty') && ($('#dossier-empty').style.display='none');
  $('#view-empty').style.display='grid';
  setViewMessage('Generating the design…');
  setGenState('generating');
  try {
    // One request does everything: the function validates and builds FIRST, then
    // commits the revision/creation to the store, so Cancel has exactly one thing
    // to abort, and a rejected edit never enters the history.
    const request = (isRevision && designId)
      ? { designId: designId, revision: prompt, baseRevision: revisionNumber,
          season: seasonSel.value || '', designType: designKind }
      : { prompt: prompt, season: seasonSel.value || '', persist: true,
          designType: designKind };
    const r = await apiPost('/api/studio/designs', request);
    if (r.aborted || r.stale) return;          // a newer request owns the UI now
    if (r.status === 401) { window.__requireSignIn && window.__requireSignIn();
      setGenState('idle');
      const editButton = $('#revision-apply'); editButton.disabled = false; editButton.textContent = 'Apply edit';
      return; }
    if (!r.ok) {
      const err = (r.data && r.data.error) || {};
      const e = new Error(apiMessage(r));
      e.code = err.code; e.questions = err.questions || []; e.violations = err.violations || [];
      throw e;
    }
    const spec = r.data;
    if (!spec || spec.error) throw new Error((spec && spec.error) || 'Empty response.');
    currentPrompt = prompt; currentSpec = spec;
    // The server is the source of truth for identity and revision: it committed
    // the store write only after this design validated and built.
    const savedDesign = spec.design || {};
    if (savedDesign.id){ designId = savedDesign.id; revisionNumber = savedDesign.revision || 1; }
    else if (!isRevision){ designId = ''; revisionNumber = 1; }
    else { revisionNumber = revisionNumber + 1; }
    setGenState('succeeded');
    renderHistory();
    renderDossier(spec);
    syncEditExamples(spec.designType || 'robot');
    if (!scene3d) {
      scene3d = buildScene({ THREE, OrbitControls, CSS2DRenderer, CSS2DObject, RoomEnvironment, canvas: $('#scene') });
      // Exposed so the viewer can be driven from the console or a screenshot script, and so
      // scene3d.capture() can export a PNG of the current design.
      window.scene3d = scene3d;
    }
    scene3d.load(spec);
    $('#ctrls').style.display='flex'; $('#hint').style.display='block';
    $('#view-empty').style.display='none';
    tabEdit.disabled = false; tabEdit.title = '';
    setMode('edit');
    const editableParts = (((spec || {}).editable_manifest || {}).parts || []).length;
    $('#revision-state').textContent = `validated / ${editableParts} editable parts / revision ${revisionNumber}`;
    // Honest edit feedback: clauses the engine could not apply are named, not absorbed.
    const rev = spec.revision;
    if (isRevision && rev && (rev.unrecognized || []).length) {
      const applied = (rev.clauses || []).filter(c => c.recognized).length;
      $('#revision-note').textContent =
        `Not understood, so not applied: "${rev.unrecognized.join('" · "')}". `
        + (applied ? `The other ${applied} edit${applied > 1 ? 's' : ''} rebuilt the design.`
                   : 'Try naming a dimension, a part or a mechanism type.');
    } else {
      $('#revision-note').textContent = isRevision
        ? `Revision ${revisionNumber} rebuilt from the prior parametric design. Nothing was flattened.`
        : 'Each edit rebuilds the validated specification and every editable part.';
    }
    const editButton = $('#revision-apply'); editButton.disabled = false; editButton.textContent = 'Apply edit';
    wireControls();
    // The assembly list in the dossier and the 3D view are two views of the same tree,
    // so picking in one selects in the other.
    document.querySelectorAll('[data-asm]').forEach(li=>li.addEventListener('click',()=>{
      document.querySelectorAll('[data-asm]').forEach(o=>o.classList.remove('sel'));
      li.classList.add('sel'); scene3d.select(li.dataset.asm);
    }));
  } catch (err) {
    const editButton = $('#revision-apply'); editButton.disabled = false; editButton.textContent = 'Apply edit';
    if (isRevision && currentSpec) {
      $('#view-empty').style.display='none';
      $('#revision-note').textContent = 'Edit rejected: ' + err.message;
      showQuestions(err, $('#revision-note'));
    } else {
      $('#view-empty').style.display='grid';
      setGenState('failed');
      showQuestions(err, setViewMessage(
        (err.code === 'CLARIFICATION_REQUIRED' ? '' : 'Could not generate: ') + err.message));
    }
  }
});

revisionForm.addEventListener('submit', (e) => {
  e.preventDefault();
  const edit = revisionQ.value.trim();
  if (!currentSpec) return;
  if (edit.length < 3){
    $('#revision-note').textContent = 'Describe the edit in a few more words, then apply it.';
    return;
  }
  // With a stored design the edit alone is queued, the chain is composed and
  // committed server-side, after validation. Only a design that was never saved
  // (store down at creation) still carries its prompt client-side.
  queuedRevisionPrompt = designId
    ? edit
    : `${currentPrompt}\n\nRevision request: ${edit}`;
  const editButton = $('#revision-apply'); editButton.disabled = true; editButton.textContent = 'Rebuilding...';
  revisionQ.value = '';
  grow(revisionQ);
  form.requestSubmit();
});

// ── revision history ────────────────────────────────────────────────────────
// Undo is a server-side restore: the chain is truncated to the chosen revision and
// the design rebuilt from it, so history and geometry can never disagree.
// A real history: every step of the chain, each restorable. The list is read
// straight from the store; a read failure just hides the panel for this render
// and tries again next time, it never changes how revisions work.
async function renderHistory(){
  const box = $('#history');
  if (!box) return;
  box.textContent = '';
  if (!designId || revisionNumber < 2){ box.style.display = 'none'; return; }
  const read = await storeRead('/' + designId);
  if (!read.ok || !read.data){ box.style.display = 'none'; return; }
  const chain = read.data.chain || [];
  box.style.display = 'block';
  const label = document.createElement('span');
  label.className = 'history-label';
  label.textContent = 'History · revision ' + read.data.revision + ' of ' + chain.length;
  box.appendChild(label);
  chain.forEach((step, i) => {
    const row = document.createElement('div');
    row.className = 'history-row';
    const text = document.createElement('span');
    text.className = 'history-step';
    text.textContent = (i + 1) + '. ' + (String(step).length > 70
      ? String(step).slice(0, 70) + '…' : String(step));
    row.appendChild(text);
    if (i + 1 !== read.data.revision){
      const btn = document.createElement('button');
      btn.type = 'button'; btn.className = 'btn btn-small';
      btn.textContent = 'Restore';
      btn.addEventListener('click', () => restoreTo(i + 1));
      row.appendChild(btn);
    }
    box.appendChild(row);
  });
}

async function restoreTo(revision){
  if (revision < 1 || !designId) return;
  const note = $('#revision-note');
  queuedRevisionPrompt = '';
  // One transactional request: the function rebuilds the truncated chain FIRST and
  // rewinds the stored history only if that build succeeds, the store can never
  // sit at a revision the screen is not showing.
  $('#view-empty').style.display='grid'; setGenState('generating');
  const r = await apiPost('/api/studio/designs',
    { designId: designId, restoreTo: revision, baseRevision: revisionNumber,
      season: seasonSel.value || '', designType: designKind });
  setGenState(r.ok ? 'succeeded' : 'failed');
  if (!r.ok){ note.textContent = 'Could not restore: ' + apiMessage(r); return; }
  const savedDesign = r.data.design || {};
  revisionNumber = savedDesign.revision || revision;
  currentSpec = r.data;
  renderDossier(r.data);
  if (scene3d) scene3d.load(r.data);
  $('#view-empty').style.display='none';
  note.textContent = 'Restored. This design is back at revision ' + revisionNumber + '.';
  renderHistory();
}

document.querySelectorAll('[data-example]').forEach(button => {
  button.addEventListener('click', () => {
    q.value = button.dataset.example;
    grow(q); syncSeasonVisibility(); q.focus();
  });
});
// Edit suggestions follow the artifact. Offering "remove the turret" on a bearing block
// taught users that the edit box only understood robots, which was never true.
const EDIT_EXAMPLES = {
  robot: [['26 inch frame', 'Make the frame 26 inches wide'],
          ['Remove turret', 'Remove the turret'],
          ['3-stage elevator', 'Use a 3-stage elevator'],
          ['Use L2 ratio', 'Change the drive ratio to L2']],
  subsystem: [['Add a stage', 'Add another stage'],
              ['Slower, stronger', 'Change the reduction to 1:20'],
              ['Lighter', 'Lighten the plates'],
              ['Different motor', 'Use a NEO Vortex instead']],
  mechanical_part: [['Wider block', 'Make it 3.5 inches wide'],
                    ['Slip fit', 'Use a slip fit instead of a press fit'],
                    ['1/4-20 screws', 'Use 1/4-20 mounting screws'],
                    ['Thicker', 'Make it 0.5 in thick'],
                    ['Steel', 'Make it from steel']],
  pcb: [['Add a connector', 'Add a fifth CAN connector'],
        ['5 V rail', 'Add a 5 V rail rated for 2 A'],
        ['Four layers', 'Use a four layer stackup'],
        ['Reverse protection', 'Add reverse polarity protection on the input']],
};
EDIT_EXAMPLES.mechanical_assembly = EDIT_EXAMPLES.mechanical_part;
EDIT_EXAMPLES.enclosure = EDIT_EXAMPLES.mechanical_part;
EDIT_EXAMPLES.other = EDIT_EXAMPLES.mechanical_part;
EDIT_EXAMPLES.electronics = EDIT_EXAMPLES.pcb;

const editExamples = $('#revision-examples');
function syncEditExamples(designType){
  const list = EDIT_EXAMPLES[designType] || EDIT_EXAMPLES.robot;
  editExamples.replaceChildren(...list.map(([label, text]) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.addEventListener('click', () => {
      revisionQ.value = text; grow(revisionQ); revisionQ.focus();
    });
    return button;
  }));
}
syncEditExamples('robot');

// ── untrusted-value handling ────────────────────────────────────────────────
// Everything below that ends up inside a template literal came from a prompt, a model, a
// database row or a backend error string. None of it is trusted markup. The dossier's HTML
// *structure* is written here; every *value* is escaped once, at the boundary, by
// escapeDeep() before it can reach innerHTML, so an image/svg/anchor/script payload renders
// as visible inert text.
//
// NB: never write a literal closing script tag in this file, not even inside a comment -
// the HTML parser ends the inline script element at the first one it sees, whatever the
// JavaScript context, and everything after it becomes live markup.
// Mirrors the server's allowlist: https only, on a known Onshape host, no embedded
// credentials. Anything else is never made clickable.
const ONSHAPE_HOSTS = new Set(['cad.onshape.com', 'www.onshape.com', 'onshape.com']);
function safeOnshapeUrl(url){
  try {
    const u = new URL(String(url || ''));
    return u.protocol === 'https:' && ONSHAPE_HOSTS.has(u.hostname)
           && !u.username && !u.password;
  } catch (_e) { return false; }
}
function esc(value){
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function escapeDeep(value, depth){
  depth = depth || 0;
  if (depth > 12) return '';
  if (typeof value === 'string') return esc(value);
  if (Array.isArray(value)) return value.map(v => escapeDeep(v, depth + 1));
  if (value && typeof value === 'object') {
    const out = {};
    for (const k of Object.keys(value)) out[k] = escapeDeep(value[k], depth + 1);
    return out;
  }
  return value;   // numbers and booleans cannot carry markup
}
function row(dt, dd){ return `<dt>${dt}</dt><dd>${dd}</dd>`; }

// A provenance chip. Every generated number says where it came from, because the difference
// between "you told me 1.125" and "I assumed 1.125" is the difference between a part that
// fits and one that does not.
function src(source){
  if (!source) return '';
  const tone = {VERIFIED:'ok', USER_PROVIDED:'ok', CALCULATED:'ok',
                INFERRED:'warn', ASSUMED:'warn', UNRESOLVED:'bad'}[source] || '';
  return ' <span class="prov ' + tone + '">' + String(source).replace('_',' ').toLowerCase() + '</span>';
}
function dimRows(list){
  return (list||[]).map(function(d){
    return row(d.label, d.value + (d.unit ? ' ' + d.unit : '') + src(d.source)
      + (d.note ? '<br><span class="note">' + d.note + '</span>' : ''));
  }).join('');
}
function provRows(list){
  return (list||[]).map(function(r){
    return row(r.label, r.value + (r.unit ? ' ' + r.unit : '') + src(r.source)
      + (r.note ? '<br><span class="note">' + r.note + '</span>' : ''));
  }).join('');
}

// ── mechanical dossier ─────────────────────────────────────────────────────
// A part shows what a part has. It does NOT show a drivetrain, a power system or a season,
// because a bearing block has none of those, and printing empty robot sections was the most
// visible way this tool told users it only really made robots.
function renderPartDossier(spec){
  const out = [];
  const sub = (spec.designTypeLabel||'')
    + (spec.partType ? ' · ' + String(spec.partType).replace(/_/g,' ') : '')
    + (spec.intent && spec.intent.confidence != null ? ' · confidence ' + spec.intent.confidence : '');
  out.push('<p class="name">' + (spec.name||'Part') + '</p><p class="sub">' + sub + '</p>');
  if (spec.intent && spec.intent.why){
    out.push('<p class="note">Read as a ' + String(spec.designType).replace(/_/g,' ')
      + ': ' + spec.intent.why + '.</p>');
  }
  if (spec.dimensions && spec.dimensions.length){
    out.push('<details open><summary>Dimensions · ' + spec.dimensions.length
      + '</summary><dl class="kv">' + dimRows(spec.dimensions) + '</dl></details>');
  }
  const mat = spec.material||{}, proc = spec.process||{}, mass = spec.mass||{};
  out.push('<details open><summary>Material and fabrication</summary><dl class="kv">'
    + row('Material', (mat.name||'-') + src(mat.source))
    + row('Process', (proc.name||'-') + src(proc.source))
    + (mass.value != null
        ? row('Mass', mass.value + ' lb' + src(mass.source)
            + '<br><span class="note">' + (mass.covers || '')
            + (mass.excludes ? ', excluding ' + mass.excludes : '') + '</span>')
        : row('Mass', 'not calculated' + src('UNRESOLVED')))
    + '</dl></details>');
  if (spec.interfaces && spec.interfaces.length){
    out.push('<details><summary>Interfaces · ' + spec.interfaces.length + '</summary><dl class="kv">'
      + spec.interfaces.map(function(i){ return row(i.name, i.detail + src(i.source)); }).join('')
      + '</dl></details>');
  }
  const calcRows = (spec.provenance||[]).filter(function(r){ return r.source === 'CALCULATED'; });
  if (calcRows.length){
    out.push('<details><summary>Calculations · ' + calcRows.length + '</summary><dl class="kv">'
      + provRows(calcRows) + '</dl></details>');
  }
  const assumed = (spec.provenance||[]).filter(function(r){
    return r.source === 'ASSUMED' || r.source === 'INFERRED'; });
  if (assumed.length){
    out.push('<details open><summary>Assumptions · ' + assumed.length + '</summary>'
      + '<p class="note">Rams Forge chose these because nothing in the request set them. '
      + 'Edit the design to change any of them.</p><dl class="kv">'
      + provRows(assumed) + '</dl></details>');
  }
  const gaps = (spec.provenance||[]).filter(function(r){ return r.source === 'UNRESOLVED'; });
  if (gaps.length){
    out.push('<details open><summary>Unresolved · ' + gaps.length + '</summary>'
      + '<p class="note">Needed, not known, and not safe to invent.</p><ul class="list">'
      + gaps.map(function(r){ return '<li>' + r.label + ': ' + (r.note||'') + '</li>'; }).join('')
      + '</ul></details>');
  }
  if (spec.hardware && spec.hardware.length){
    out.push('<details><summary>Hardware · ' + spec.hardware.length + '</summary><dl class="kv">'
      + spec.hardware.map(function(h){
          return row((h.qty||1) + ' x ' + h.name,
            (h.verified ? 'catalog part' : 'requirement') + src(h.source)
            + (h.note ? '<br><span class="note">' + h.note + '</span>' : ''));
        }).join('') + '</dl></details>');
  }
  if (spec.risks && spec.risks.length){
    out.push('<details><summary>Risks and checks · ' + spec.risks.length + '</summary><ul class="list">'
      + spec.risks.map(function(r){ return '<li><b>' + r.severity + '</b>: ' + r.detail + '</li>'; }).join('')
      + '</ul></details>');
  }
  if (spec.parameters){
    const keys = Object.keys(spec.parameters);
    out.push('<details><summary>Parametric variables · ' + keys.length + '</summary>'
      + '<p class="note">The named dimensions the CAD is driven by. The FeatureScript export '
      + 'exposes each one, so the model stays editable.</p><dl class="kv">'
      + keys.map(function(k){ return row(k, spec.parameters[k]); }).join('') + '</dl></details>');
  }
  return out.join('');
}

// ── PCB dossier ────────────────────────────────────────────────────────────
function renderBoardDossier(spec){
  const out = [];
  const comp = spec.completion||{}, board = spec.board||{}, el = spec.electronics||{};
  out.push('<p class="name">' + (spec.name||'Board') + '</p><p class="sub">'
    + (spec.designTypeLabel||'PCB') + ' · reached '
    + String(comp.reached||'NONE').replace(/_/g,' ').toLowerCase() + '</p>');
  // The completion ladder goes first and is never collapsed. Nobody should have to open a
  // section to discover that the board they are looking at is not routed.
  out.push('<details open><summary>Completion state</summary><p class="note">'
    + (comp.summary||'') + '</p><dl class="kv">'
    + (comp.stages||[]).map(function(st){
        return row(String(st.stage).replace(/_/g,' ').toLowerCase(),
          (st.done ? 'complete' : 'not implemented')
          + '<br><span class="note">' + st.detail + '</span>');
      }).join('') + '</dl></details>');
  out.push('<details open><summary>Board</summary><dl class="kv">'
    + row('Layers', board.layers || '-')
    + row('Size', board.size_mm ? board.size_mm[0] + ' x ' + board.size_mm[1] + ' mm' + src(board.size_source) : '-')
    + row('Input', (el.input && el.input.voltage != null) ? el.input.voltage + ' V' + src(el.input.source) : '-')
    + row('Mounting', (board.mounting_holes||0) + ' holes<br><span class="note">'
        + (board.mounting_note||'') + '</span>')
    + '</dl></details>');
  const lay = spec.layout;
  if (lay && lay.placements && lay.placements.length){
    const bad = (lay.checks||[]).filter(function(c){ return !c.ok; });
    out.push('<details open><summary>Placement · ' + lay.placements.length
      + ' parts</summary><p class="note">' + lay.board_mm[0] + ' x ' + lay.board_mm[1]
      + ' mm outline, origin ' + lay.origin + '. ' + lay.package_note + '.</p>'
      + '<dl class="kv">'
      + (lay.checks||[]).map(function(c){
          return row(c.check, (c.ok ? 'pass' : 'review')
            + '<br><span class="note">' + c.detail + '</span>');
        }).join('')
      + '</dl>'
      + '<details><summary>Every position</summary><dl class="kv">'
      + lay.placements.map(function(pl){
          return row(pl.reference, pl.x + ', ' + pl.y + ' mm · ' + pl.package
            + '<br><span class="note">' + pl.why + '</span>');
        }).join('')
      + '</dl></details></details>');
  }
  const rt = spec.routing;
  if (rt && rt.traces && rt.traces.length){
    const perLayer = {};
    rt.traces.forEach(function(t){ perLayer[t.layer] = (perLayer[t.layer]||0) + 1; });
    out.push('<details open><summary>Copper · ' + rt.traces.length + ' runs</summary>'
      + '<p class="note">' + rt.copper_mm + ' mm of trace on a ' + rt.grid_mm
      + ' mm grid, ' + rt.clearance_mm + ' mm clearance. ' + rt.fab_note + '.</p>'
      + '<dl class="kv">'
      + row('Top layer', (perLayer.top || 0) + ' runs')
      + row('Bottom layer', (perLayer.bottom || 0) + ' runs')
      + row('Vias', (rt.vias || []).length + ' plated through')
      + (rt.ground ? row('Ground', rt.ground.kind + ' on the ' + rt.ground.layer
          + '<br><span class="note">' + rt.ground.note + '</span>') : '')
      + (rt.unrouted && rt.unrouted.length
          ? row('Unrouted', rt.unrouted.join(', ') + src('UNRESOLVED')) : '')
      + '</dl></details>');
  }
  if (spec.drc && spec.drc.length){
    const bad = spec.drc.filter(function(c){ return !c.ok; });
    out.push('<details ' + (bad.length ? 'open' : '') + '><summary>Design rule check · '
      + (spec.drc.length - bad.length) + ' passed, ' + bad.length + ' to review</summary>'
      + '<p class="note">Re-derived from the finished copper, not read back from the router, '
      + 'so a bug in the router shows up here instead of being confirmed by it.</p>'
      + '<ul class="list">'
      + spec.drc.map(function(c){
          return '<li><b>' + (c.ok ? 'pass' : 'review') + '</b>: ' + c.check + ': '
            + c.detail + '</li>'; }).join('')
      + '</ul></details>');
  }
  if (spec.power && spec.power.rails && spec.power.rails.length){
    out.push('<details open><summary>Power · ' + spec.power.total_w + ' W</summary><dl class="kv">'
      + spec.power.rails.map(function(r){
          return row(r.name, r.current_a + ' A · ' + r.power_w + ' W'); }).join('')
      + '</dl></details>');
  }
  if (spec.components && spec.components.length){
    const unresolved = spec.components.filter(function(c){ return !c.verified; }).length;
    out.push('<details><summary>Components · ' + spec.components.length + '</summary>'
      + '<p class="note">' + unresolved + ' of ' + spec.components.length
      + ' are stated as requirements rather than exact parts. Rams Forge does not invent '
      + 'manufacturer part numbers.</p><dl class="kv">'
      + spec.components.map(function(c){
          return row(c.reference + ' ' + (c.value||''),
            (c.mpn ? c.mpn : (c.requirement || c.category)) + src(c.source));
        }).join('') + '</dl></details>');
  }
  if (spec.nets && spec.nets.length){
    out.push('<details><summary>Nets · ' + spec.nets.length + '</summary><dl class="kv">'
      + spec.nets.map(function(n){
          return row(n.name, n.node_count + ' pins<br><span class="note">' + (n.note||'') + '</span>');
        }).join('') + '</dl></details>');
  }
  if (spec.checks && spec.checks.length){
    const bad = spec.checks.filter(function(c){ return !c.ok; }).length;
    out.push('<details ' + (bad ? 'open' : '') + '><summary>Checks · '
      + (spec.checks.length - bad) + ' passed, ' + bad + ' to review</summary><ul class="list">'
      + spec.checks.map(function(c){
          return '<li><b>' + (c.ok ? 'ok' : 'review') + '</b>: ' + c.check + ': ' + c.detail + '</li>';
        }).join('') + '</ul></details>');
  }
  if ((el.assumptions||[]).length){
    out.push('<details open><summary>Assumptions · ' + el.assumptions.length + '</summary><ul class="list">'
      + el.assumptions.map(function(a){ return '<li>' + a + '</li>'; }).join('') + '</ul></details>');
  }
  const ex = spec.exports||{};
  out.push('<details><summary>Export</summary><p class="note">' + (ex.note||'') + '</p><dl class="kv">'
    + row('BOM', ex.bom_csv ? 'available' : 'not available')
    + row('KiCad source', ex.kicad_sch ? 'available' : 'not generated')
    + row('Gerbers', ex.gerbers ? 'available' : 'not generated')
    + row('STEP', ex.step ? 'available, board, parts and copper' : 'not available')
    + '</dl></details>');
  return out.join('');
}

function renderDossier(rawSpec){
  // One escape pass at the boundary; nothing downstream re-inserts raw values.
  const spec = escapeDeep(rawSpec);
  // The dossier follows the artifact. Anything that is not a robot gets the dossier for what
  // it actually is, and never a drivetrain section it does not have.
  const kind = spec.designType || 'robot';
  if (kind === 'pcb' || kind === 'electronics'){
    dossier.innerHTML = renderBoardDossier(spec);
    return;
  }
  if (spec.engine === 'mechanical'){
    dossier.innerHTML = renderPartDossier(spec);
    return;
  }
  const d = spec.drivetrain||{}, e = spec.electrical||{}, f = spec.frame||{};
  const inc = (spec.subsystems||[]);
  const partHtml = [];
  const season = spec.season||{};
  partHtml.push(`<p class="name">${spec.name||'Robot'}</p><p class="sub">${spec.team_number?'Team '+spec.team_number+' · ':''}${season.label||(spec.profile&&spec.profile.label)||''}${season.selected_by?' · '+season.selected_by:''}</p>`);

  // What the season asks for, before what this robot does about it. Every row states the
  // number, where it came from and what it forces. The derivation is the useful part.
  if (season.design_targets && season.design_targets.length){
    partHtml.push(`<details><summary>What ${season.label} asks for · ${season.design_targets.length} constraints</summary>
      <p class="note">${season.summary||''}</p><dl class="kv">
      ${season.design_targets.map(t=>row(t.target, t.value+' | '+t.from)).join('')}
      </dl>
      <p class="note">${season.design_targets.map(t=>t.means).slice(0,3).join(' ')}</p>
      ${season.verify?`<p class="note">${season.verify}</p>`:''}
    </details>`);
  }

  // The rule check is reported, never enforced: a design that trips one is shown with the
  // check attached so the team decides what to do about it.
  const checks = spec.rule_check||[];
  if (checks.length){
    const bad = checks.filter(c=>!c.ok);
    const rows = `<dl class="kv">
      ${checks.map(c=>row(c.check+' ('+c.rule+')', (c.ok?'pass':'FAILS')+': '+c.detail)).join('')}
    </dl>`;
    const caveat = `<p class="note">These are the four automatically checked rule-envelope constraints. Many failure modes are not checked, interference, shaft stress, current limits, CG, fatigue, and passing is not an inspection.</p>`;
    if (bad.length){
      // A failing check is the one thing on this page nobody should have to open a fold to see.
      partHtml.push(`<h2>Construction rule check</h2>`+rows);
      bad.forEach(c=>partHtml.push(`<p class="note"><b>${c.check} fails ${c.rule}.</b> ${c.fix}</p>`));
      partHtml.push(caveat);
    } else {
      partHtml.push(`<details><summary>Construction rule check · ${checks.length} passed</summary>${rows}${caveat}</details>`);
    }
  }

  // How real the CAD is, in one word, with the counts behind it. Generated from the
  // same per-kind map the exports use, so this page cannot claim more than the
  // geometry delivers.
  const fid = spec.fidelity||{};
  if (fid.levels){
    partHtml.push(`<details><summary>CAD fidelity · ${fid.overall||''}</summary><dl class="kv">
      ${row('Detailed', String(fid.levels.detailed||0)+' parts | true dimensions, teeth, bores')}
      ${row('Concept', String(fid.levels.concept||0)+' parts | true path and section, simplified form')}
      ${row('Envelope', String(fid.levels.envelope||0)+' parts | catalog outer dimensions only')}
      </dl><p class="note">${fid.statement||''}</p></details>`);
  }

  // Measured transmissions: a failing mesh is shown unfolded, like a failing rule.
  const trans = spec.transmission||{};
  if (trans.issues && trans.issues.length){
    partHtml.push(`<h2>Transmission check</h2>`);
    trans.issues.forEach(i=>partHtml.push(`<p class="note"><b>${(i.parts||[]).join(' · ')}:</b> ${i.detail}</p>`));
  } else if (trans.checked){
    partHtml.push(`<details><summary>Transmission check · verified</summary><p class="note">${trans.checked}. Nothing failed on this design.</p></details>`);
  }

  partHtml.push(`<h2>Drivetrain</h2><dl class="kv">
     ${row('Type', d.type||'Not set')}
     ${row('Module', d.module||'Not set')}
     ${row('Drive motor', d.motor||'Not set')}
     ${d.drive_ratio?row('Drive ratio', (d.drive_ratio_label?d.drive_ratio_label+' | ':'')+d.drive_ratio+':1'):''}
     ${d.steer_ratio?row('Azimuth', d.steer_ratio+':1'):''}
     ${d.free_speed_fps?row('Free speed', d.free_speed_fps+' ft/s'):''}
     ${row('Modules', (d.module_count||0)+(d.modules_included===false?' (reserved)':''))}
   </dl>`);
  if (spec.intake && spec.intake.included){
    const i = spec.intake;
    partHtml.push(`<h2>Intake (detailed)</h2><dl class="kv">
      ${row('Type', i.type)}
      ${row('Rollers', (i.roller_count||1)+' × Ø'+i.roller_diameter_in+' in')}
      ${i.roller_center_distance_in?row('Center distance', i.roller_center_distance_in+' in'):''}
      ${i.compliant_wheel?row('Wheels', i.compliant_wheel):''}
      ${row('Compression', (i.compression_in||0.5)+' in')}
      ${i.gear_reduction?row('Reduction', i.gear_reduction):''}
      ${i.roller_surface_speed_fps?row('Surface speed', i.roller_surface_speed_fps+' ft/s'):''}
      ${i.deploy?row('Deploy', i.deploy):''}
      ${i.motor?row('Motor', (i.motor_count||1)+' × '+i.motor):''}
    </dl>`);
    if (i.roller_surface_speed_fps) partHtml.push(`<p class="note">Surface speed is geared above the approach speed so the roller pulls the gamepiece in rather than pushing it away; compression comes from the roller center distance, not from feel.</p>`);
  }
  const hp = spec.hopper;
  if (hp && hp.included){
    partHtml.push(`<h2>Hopper / indexer</h2><dl class="kv">
      ${row('Type', hp.type)}
      ${row('Floor', hp.floor_width_in+' × '+hp.floor_depth_in+' in, '+hp.wall_height_in+' in walls')}
      ${row('Capacity', '~'+hp.capacity_estimate+' '+((spec.season&&spec.season.gamepiece)||'pieces'))}
      ${row('Feed rate', '~'+hp.feed_rate_per_s+' per second')}
      ${row('Exit lane', hp.lanes+' × '+hp.exit_lane_width_in+' in')}
      ${row('Index wheels', hp.wheel_count+' × Ø'+hp.wheel_diameter_in+' in, '+hp.wheel_proud_in+' in proud of the floor')}
      ${row('Reduction', hp.gear_reduction)}
      ${row('Motors', (hp.motor_count||1)+' × '+hp.motor)}
    </dl>
    <p class="note">${hp.sensor}. Index wheels stand proud of the floor because a flush wheel lets the piece ride the floor and slip, and a high one lets it climb over.</p>
    <p class="note">${hp.caveat}</p>`);
  }
  const sh = spec.shooter;
  if (sh && sh.included){
    partHtml.push(`<h2>Shooter</h2><dl class="kv">
      ${row('Type', sh.type)}
      ${sh.flywheel_stages?row('Flywheel stages', sh.flywheel_stages):''}
      ${row('Flywheel', 'Ø'+sh.flywheel_diameter_in+' in')}
      ${sh.barrel_length_in?row('Barrel', sh.barrel_length_in+' in'):''}
      ${row('Compression', (sh.compression_in||0.5)+' in')}
      ${sh.hood_angle_deg?row('Hood / pivot', sh.hood_angle_deg[0]+'–'+sh.hood_angle_deg[1]+'°'):''}
      ${sh.turreted?row('Turret','yes'):''}
      ${sh.gear_reduction?row('Reduction', sh.gear_reduction):''}
      ${sh.flywheel_surface_speed_fps?row('Surface speed', sh.flywheel_surface_speed_fps+' ft/s'):''}
      ${sh.exit_velocity_fps?row('Exit velocity', sh.exit_velocity_fps+' ft/s'):''}
      ${sh.spinup_time_s?row('Recovery', '~'+sh.spinup_time_s+' s'):''}
      ${row('Motors', (sh.motor_count||2)+' × '+sh.motor)}
    </dl>${sh.feed_path?`<p class="note">Feed path: ${sh.feed_path}.</p>`:''}
    ${sh.exit_velocity_fps&&!sh.stacked?`<p class="note">A gamepiece squeezed between one wheel and a stationary hood leaves at about half the wheel's surface speed. Size the range on the exit velocity, not the surface speed.</p>`:''}`);
    // The shot the season actually asks for: required velocity worked back from the goal
    // geometry, and what this design does against it.
    const st = sh.shot;
    if (st){
      partHtml.push(`<h2>The shot</h2><dl class="kv">
        ${row('Target', st.target+' opening at '+st.target_height_in+' in')}
        ${row('Release height', st.release_height_in+' in')}
        ${row('Cheapest angle', st.optimal_angle_deg+'° at '+st.design_range_ft+' ft')}
        ${row('Required exit', st.required_exit_fps+' ft/s ('+st.required_surface_speed_fps+' ft/s surface)')}
        ${row('Required at '+st.long_range_ft+' ft', st.required_exit_fps_long+' ft/s')}
        ${row('This design', st.achieved_exit_fps+' ft/s → '+st.achieved_range_ft+' ft, '+st.entry_angle_deg+'° entry')}
        ${row('Apex', st.apex_in+' in')}
      </dl>
      <p class="note">${st.makes_design_range?'Clears the design range.':'<b>Short of the design range.</b> Raise the surface speed or lower the reduction.'} The cheapest angle is 45° + ½·atan(Δh/d), which is the shot needing the least flywheel energy.</p>
      <p class="note">${st.caveat}</p>`);
    }
  }
  const el2 = spec.elevator;
  if (el2 && el2.included){
    partHtml.push(`<h2>Elevator</h2><dl class="kv">
      ${row('Architecture', el2.architecture)}
      ${row('Stages', el2.stages)}
      ${row('Max height', el2.max_height_in+' in')}
      ${el2.rigging?row('Rigging', el2.rigging):''}
      ${el2.travel_in?row('Carriage travel', el2.travel_in+' in ('+el2.stage_travel_in+' in per stage)'):''}
      ${el2.carriage_speed_multiple>1?row('Carriage speed', el2.carriage_speed_multiple+'× drum payout'):''}
      ${el2.stage_overlap_in?row('Overlap at full ext.', el2.stage_overlap_in+' in'):''}
      ${el2.upright_span_in?row('Upright span', el2.upright_span_in+' in'):''}
      ${row('Rail', el2.rail)} ${row('Reduction', el2.reduction)}
      ${row('Motors', (el2.motor_count||2)+' × '+el2.motor)}
    </dl>${el2.bearing_blocks?`<p class="note">Bearing blocks: ${el2.bearing_blocks}.</p>`:''}
    ${el2.stage_overlap_in?`<p class="note">That remaining overlap, with a bearing block at each end of it, is the only thing resisting the tip moment at full extension.</p>`:''}`);
  }
  const am = spec.manipulator;
  if (am && am.included){
    partHtml.push(`<h2>Arm</h2><dl class="kv">
      ${row('Type', am.type)}
      ${row('Reach', am.reach_in+' in')}
      ${am.segments?row('Segments', am.segments+(am.segment_lengths_in?' ('+am.segment_lengths_in.join(' + ')+' in)':'')):''}
      ${am.shoulder_pivot_deg?row('Shoulder', am.shoulder_pivot_deg[0]+'–'+am.shoulder_pivot_deg[1]+'°'):''}
      ${am.wrist?row('Wrist', (am.wrist_range_deg||[]).join('–')+'°'):''}
      ${am.end_effector?row('End effector', am.end_effector):''}
      ${am.reduction?row('Reduction', am.reduction):''}
      ${am.holding_torque_nm?row('Holding torque', am.holding_torque_nm+' N·m'):''}
      ${am.shoulder_height_in?row('Shoulder height', am.shoulder_height_in+' in'):''}
      ${row('Shaft', am.shaft)}
    </dl>${am.gravity_compensation?`<p class="note">Reduction sized for the worst case, holding horizontal at full extension, with ${am.gravity_compensation}.</p>`:''}`);
  }
  const cl = spec.climber;
  if (cl && cl.included){
    partHtml.push(`<h2>Climber</h2><dl class="kv">
      ${row('Type', cl.type)}
      ${cl.stages?row('Stages', cl.stages):''}
      ${row('Stowed / extended', cl.stowed_height_in+' → '+cl.extended_height_in+' in')}
      ${cl.winch_drum_diameter_in?row('Winch drum', 'Ø'+cl.winch_drum_diameter_in+' in'):''}
      ${cl.rope?row('Rope', cl.rope):''}
      ${cl.hook?row('Hook', cl.hook):''}
      ${cl.travel_in?row('Travel', cl.travel_in+' in'):''}
      ${cl.rope_tension_lbf?row('Rope tension', cl.rope_tension_lbf+' lbf over '+cl.load_paths+' path'+(cl.load_paths>1?'s':'')):''}
      ${cl.drum_torque_nm?row('Drum torque', cl.drum_torque_nm+' N·m'):''}
      ${cl.reduction?row('Reduction', cl.reduction):''}
      ${row('Motors', (cl.motor_count||2)+' × '+cl.motor)}
    </dl><p class="note">${cl.ratchet}. ${cl.engagement||''}</p>
    ${cl.drum_torque_nm?`<p class="note">The drum radius is a lever working against you: a bigger drum takes more rope but costs you reduction.</p>`:''}`);
  }
  partHtml.push(`<h2>Power</h2><dl class="kv">
     ${row('Distributor', (e.distributor_key||'pdh').toUpperCase())}
     ${row('Channels used', (e.budget&&e.budget.channels_used)||'Not set')}
     ${row('Main breaker', ((e.budget&&e.budget.main_breaker_a)||120)+' A')}
     ${row('Frame', (f.width_in||28)+' × '+(f.length_in||28)+' in')}
   </dl>`);
  const cad = spec.cad;
  if (cad && cad.assemblies){
    const c = cad.feature_counts||{};
    const order = ['tube','plate','gusset','shaft','bearing','gear','pulley','sprocket','belt','wheel','motor','gearbox','bolts'];
    const chips = order.filter(k=>c[k]).map(k=>`${c[k]} ${k}`).concat(
      Object.keys(c).filter(k=>!order.includes(k)).map(k=>`${c[k]} ${k}`));
    const integ = cad.integrity;
    partHtml.push(`<h2>CAD model</h2><dl class="kv">
      ${row('Schema', cad.version)}
      ${row('Assemblies', cad.assemblies.length)}
      ${row('Modelled features', cad.feature_total)}
      ${row('Envelope', cad.envelope_in.join(' × ')+' in')}
      ${integ?row('Assembly connectivity', integ.ok?'every body reaches the frame through contact':'FLAGGED'):''}
    </dl>`);
    if (integ){
      partHtml.push(integ.ok
        ? `<p class="note">Every one of the ${integ.bodies} bodies is in contact with structure and every assembly has a mount path back to the chassis, ${integ.contacts} contacts verified at ±${integ.tolerance_in} in. Feet sit on crossmembers, gearboxes on plates, bolts at every joint.</p>`
        : `<p class="note"><b>Assembly connectivity flagged:</b> ${(integ.floating||[]).concat(integ.unreached_assemblies||[]).slice(0,6).join(' · ')}</p>`);
    }
    partHtml.push(`<ul class="sslist">`+cad.assemblies.map(a=>
      `<li data-asm="${a.id}" style="cursor:pointer"><b>${a.name}</b><span>${a.features.length} features${a.note?' · '+a.note:''}</span>
       ${(a.mates||[]).length?`<span style="margin-top:4px">${a.mates.join(' · ')}</span>`:''}</li>`).join('')+
    `</ul><p class="note">${chips.join(' · ')}.</p>`);
    const cuts = spec.cut_list||[];
    if (cuts.length){
      const sec = r => `${+r.section_in[0].toFixed(2)}×${+r.section_in[1].toFixed(2)}×${r.wall_in} in`;
      partHtml.push(`<h2>Tube cut list</h2><dl class="kv">`+
        cuts.map(r=>row(sec(r), `${r.qty} × ${r.length_in} in | ${r.used_in.join(', ')}`)).join('')+`</dl>
        <p class="note">Every member is a catalog stock section, including the telescoping stages, so the list is orderable as written. Add your own kerf and squaring allowance.</p>`);
    }
    partHtml.push(`<p class="verify">${cad.caveat}</p>`);
  }
  const me = spec.mass_estimate;
  if (me){
    const rows = Object.entries(me).filter(([k,v])=>typeof v === 'number');
    if (rows.length) partHtml.push(`<h2>Mass estimate</h2><dl class="kv">`+
      rows.map(([k,v])=>row(k.replace(/_lb$/,'').replace(/_/g,' '), v+' lb')).join('')+`</dl>`);
  }
  if ((spec.design_notes||[]).length){
    partHtml.push(`<h2>Design notes</h2><ul class="sslist">`+
      spec.design_notes.slice(0,5).map(n=>`<li><span>${n}</span></li>`).join('')+`</ul>`);
  }
  if ((spec.risks||[]).length){
    partHtml.push(`<h2>Risks</h2><ul class="sslist">`+
      spec.risks.slice(0,4).map(n=>`<li><span>${n}</span></li>`).join('')+`</ul>`);
  }
  const tq = spec.techniques||[];
  if (tq.length){
    partHtml.push(`<h2>Build techniques (${tq.length})</h2><ul class="sslist">`+
      tq.slice(0,8).map(t=>`<li><b>${t.name}</b><span>${t.why||''}</span>
        ${t.how?`<span style="margin-top:4px"><b style="font-weight:600">How:</b> ${t.how}</span>`:''}
        ${t.pitfall?`<span style="margin-top:4px;color:var(--warning)"><b style="font-weight:600">Avoid:</b> ${t.pitfall}</span>`:''}</li>`).join('')+
      `</ul>${tq.length>8?`<p class="note">+ ${tq.length-8} more in the full package.</p>`:''}`);
  }
  partHtml.push(`<div class="verify">Nominal envelopes and published figures are for packaging and first-order sizing. Verify every part against the vendor drawing and the current game manual before fabrication. Deterministic synthesis; concept geometry, not native parametric CAD.</div>`);
  $('#dossier').innerHTML = partHtml.join('');
}

function wireControls(){
  const s = scene3d; if (!s || s._wired) return; s._wired = true;
  document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>s.setView(b.dataset.view)));
  const be=$('#b-explode'); be.addEventListener('click',()=>{const on=s.toggleExplode();be.classList.toggle('on',on);});
  const br=$('#b-run'); br.addEventListener('click',()=>{const on=s.toggleRun();br.classList.toggle('on',on);br.textContent=on?'Stop mechanisms':'Run mechanisms';});
  const cut=$('#cut'); cut.addEventListener('input',()=>{s.setCut(cut.value/100);$('#cutv').textContent=cut.value>0?cut.value+'%':'off';});
  // Link to Onshape: the design as editable FeatureScript, copied out or created directly in
  // the user's own account with their API keys (used once, never stored).
  const osModal=$('#onshape-modal'), osResult=$('#os-result');
  $('#b-onshape').addEventListener('click',()=>{ osResult.style.display='none'; window.__openDialog(osModal, $('#os-download')); });
  // Exports must reference the EXACT design on screen. With a stored design that is
  // its id pinned to the shown revision (the server 409s if the chain moved); the
  // raw prompt is only for a design that was never saved. Sending currentPrompt
  // after a revision used to export a robot built from just the edit text.
  const fsBody=()=> designId
    ? {designId: designId, baseRevision: revisionNumber, season: seasonSel.value||'',
       designType: designKind}
    : {prompt: currentPrompt, season: seasonSel.value||'', designType: designKind};
  // STEP download: fetched rather than a bare link so a sleeping geometry server
  // surfaces as a sentence, not a broken download.
  $('#os-download').addEventListener('click', async ()=>{
    const b=$('#os-download'); b.disabled=true; b.textContent='Building solids…';
    osResult.style.display='block'; osResult.textContent='Generating the STEP assembly. Large robots take a while on a cold server.';
    try {
      // The export gets its own controller and timeout: it must never abort a
      // running generation, and a hung geometry server must surface as a message.
      abortScope('export-step');
      const ctl=new AbortController();
      __ops['export-step']={ctl, ticket:((__ops['export-step']||{}).ticket||0)+1};
      const timer=setTimeout(()=>ctl.abort(), 300000);
      const r=await fetch('/api/studio/designs/exports/step',{
        method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify(fsBody()), signal:ctl.signal});
      clearTimeout(timer);
      if(!r.ok){
        const t=r.headers.get('Content-Type')||'';
        let msg='HTTP '+r.status;
        if(t.includes('application/json')){ const d=await r.json().catch(()=>null);
          msg=(d&&d.error&&d.error.message)||msg; }
        throw new Error(msg);
      }
      const blob=await r.blob();
      // A proxy error page is HTTP 200 HTML, it must not be saved as a .step file.
      const head=await blob.slice(0, 32).text();
      if(!head.startsWith('ISO-10303')) throw new Error('The server did not return a STEP file. Try again.');
      const dispo=r.headers.get('Content-Disposition')||'';
      const m=dispo.match(/filename="([^"]+)"/);
      const a=document.createElement('a');
      a.href=URL.createObjectURL(blob); a.download=(m&&m[1])||'kale-robot.step';
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(()=>URL.revokeObjectURL(a.href), 60000);
      const parts=r.headers.get('X-Kale-Parts');
      osResult.textContent='Downloaded ✓ In Onshape: open a document and drag the file in (or Insert → Import), '+(parts?parts+' ':'')+'named editable solids arrive in a Part Studio.';
    } catch(err){ osResult.textContent='Download failed: '+err.message; }
    b.disabled=false; b.textContent='Download .step';
  });
  $('#os-copy').addEventListener('click', async ()=>{
    const b=$('#os-copy'); b.textContent='Copying…';
    try {
      const rf=await apiPost('/api/studio/designs/exports/featurescript', fsBody(), 'export-fs');
      if(!rf.ok) throw new Error(apiMessage(rf));
      const src=(rf.data&&rf.data.source)||'';
      // Clipboard can fail (permissions, insecure context, focus) - always leave a way out.
      try { await navigator.clipboard.writeText(src); b.textContent='Copied ✓'; }
      catch(_e){
        const blob=new Blob([src],{type:'text/plain'});
        const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
        a.download='kale-robot.fs';
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(()=>URL.revokeObjectURL(a.href), 60000);
        b.textContent='Downloaded ✓';
      }
    } catch(_e){ b.textContent='Copy failed'; }
    setTimeout(()=>{ b.textContent='Copy FeatureScript'; }, 2200);
  });
  $('#os-create').addEventListener('click', async ()=>{
    const b=$('#os-create');
    const access=$('#os-access').value.trim(), secret=$('#os-secret').value.trim();
    osResult.style.display='block';
    if(!access||!secret){ osResult.textContent='Both API keys are needed, create them in the Onshape developer portal.'; return; }
    b.disabled=true; b.textContent='Publishing…'; osResult.textContent='Creating the document and writing the Feature Studio…';
    try {
      const rr=await apiPost('/api/studio/designs/onshape',
        Object.assign({access_key:access,secret_key:secret}, fsBody()), 'onshape');
      if(!rr.ok) throw new Error(apiMessage(rr));
      const d=rr.data||{};
      // A URL from the backend is untrusted: only an https link on a known Onshape host is
      // ever turned into an anchor, and it is built as an element rather than markup.
      osResult.replaceChildren();
      osResult.append('Published as editable FeatureScript ✓, ');
      if (safeOnshapeUrl(d.url)) {
        const a=document.createElement('a');
        a.href=d.url; a.target='_blank'; a.rel='noopener noreferrer external';
        a.textContent='open it in Onshape';
        a.style.color='var(--brand)'; a.style.textDecoration='underline';
        osResult.append(a);
      } else {
        osResult.append('the document was created, but Onshape returned an unexpected link.');
      }
      osResult.append('. In the document, add a Part Studio and use the feature to see the solids.');
    } catch(err){ osResult.textContent='Onshape publish failed: '+err.message; }
    finally {
      // "Used once, never stored" must hold in the DOM too: the keys are wiped the
      // moment the request ends, so reopening the modal cannot resurface them.
      $('#os-access').value=''; $('#os-secret').value='';
    }
    b.disabled=false; b.textContent='Create in my Onshape';
  });
  // Zoom is also on the scroll wheel, but a wheel is invisible and a trackpad usually gives
  // the gesture to the page instead of the canvas. Holding the button keeps zooming.
  const press=(el,fn)=>{let t=null,r=null;const stop=()=>{clearTimeout(t);clearInterval(r);t=r=null;};
    el.addEventListener('pointerdown',e=>{e.preventDefault();fn();t=setTimeout(()=>{r=setInterval(fn,90);},320);});
    ['pointerup','pointerleave','pointercancel'].forEach(ev=>el.addEventListener(ev,stop));};
  press($('#b-zin'), ()=>s.zoom(0.86));
  press($('#b-zout'), ()=>s.zoom(1.16));
  $('#b-fit').addEventListener('click',()=>s.setView('iso'));
  // Clicking a part reports the feature the CAD tree actually holds for it.
  const picked=$('#picked');
  s.onPick((id,f)=>{
    document.querySelectorAll('[data-asm]').forEach(o=>o.classList.toggle('sel', o.dataset.asm===id));
    if(!f){picked.style.display='none';return;}
    const dims=[];
    if(f.sec) dims.push(f.sec[0]+'×'+f.sec[1]+' in, '+f.len.toFixed(2)+' in long');
    else if(f.size) dims.push(f.size.map(v=>+v.toFixed(2)).join(' × ')+' in');
    else if(f.dia) dims.push('Ø'+f.dia+' in'+(f.len?' × '+f.len+' in':''));
    if(f.teeth) dims.push(f.teeth+'T, PD '+f.pd+' in');
    if(f.bore) dims.push(f.bore+' in bore');
    picked.style.display='block';
    const nameEl=document.createElement('span'); nameEl.textContent=f.n||'';
    const dimEl=document.createElement('span'); dimEl.textContent=dims.join(' · ')||f.t||'';
    picked.replaceChildren(nameEl, dimEl);
  });
}
</script>
</body>
</html>
"""
