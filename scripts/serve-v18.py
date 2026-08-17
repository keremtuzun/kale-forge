"""Run v18: llama.cpp in its own process, the API in another.

The model used to run inside the API worker (`local_llamacpp`), which meant a fault in the C++
killed FastAPI too. v18 produced three in one afternoon — two `GGML_ASSERT` aborts in the
repack kernel (`ggml-cpu/repack.cpp:4238`) and one SIGSEGV. Capping threads made them rarer,
never absent, and supervision alone could not stop the API disappearing with the model.

Split in two, the same fault stops being an outage:

    llama-server :8010  ← weights live here, this is the process that dies
    API          :8001  ← stays up, reports the model as unavailable, keeps serving

When llama-server dies the API answers normally, `LlamaServerProvider` raises
`ProviderUnavailable`, and the Design Studio falls back to deterministic synthesis until this
script has the model back. Nothing 500s and no request is lost to a dead socket.

Both children are supervised; in practice only the first one ever needs it.

    python scripts/serve-v18.py

Ctrl+C stops both.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICE_DIR = ROOT / "services" / "inference"
LLAMA_SERVER = ROOT / "build" / "llamabin" / "llama-server.exe"
CLOUDFLARED = ROOT / "build" / "cloudflared.exe"
TUNNEL_LOG = ROOT / "build" / "tunnel.log"
# Where the current public hostname is written, because a quick tunnel gets a new random one
# every start and INFERENCE_URL on Vercel has to be pointed at it by hand.
TUNNEL_URL_FILE = ROOT / "build" / "tunnel-url.txt"
# The hostname that was last pushed to Vercel, so a restart that happens to get the same one
# does not trigger a pointless redeploy.
PUBLISHED_URL_FILE = ROOT / "build" / "published-url.txt"
SITE_DIR = ROOT / "apps" / "site"
WANT_TUNNEL = os.environ.get("KALE_TUNNEL", "1") != "0"
# Push a changed tunnel hostname to Vercel automatically. This is the difference between the
# site healing itself after a reboot and sitting on deterministic output until someone notices.
WANT_PUBLISH = os.environ.get("KALE_AUTO_PUBLISH", "1") != "0"
ALIAS = os.environ.get("KALE_ALIAS", "kaleai.vercel.app")

HOST = os.environ.get("KALE_HOST", "127.0.0.1")
API_PORT = os.environ.get("KALE_PORT", "8001")
MODEL_PORT = os.environ.get("KALE_MODEL_PORT", "8010")

MODEL_PATH = os.environ.get(
    "KALE_MODEL_PATH", str(ROOT / "build" / "gguf" / "kale-design-v18-q4_k_m.gguf"))
MODEL_VERSION = os.environ.get("KALE_MODEL_VERSION", "kale-design-qwen3-4b-v18-cad-repair-50")
# 8 threads segfaulted in-process. It is the model server's problem now rather than the API's,
# but a crash still costs a reload, so the conservative default stays.
THREADS = os.environ.get("KALE_LLAMA_THREADS", "4")
CTX = os.environ.get("KALE_LLAMA_CTX", "8192")

STARTUP_TIMEOUT_S = float(os.environ.get("KALE_STARTUP_TIMEOUT", "300"))
POLL_EVERY_S = 5.0
HEALTH_TIMEOUT_S = 20.0
HEALTH_FAILS_BEFORE_KILL = 3
BACKOFF_START_S, BACKOFF_MAX_S = 2.0, 60.0
STABLE_AFTER_S = 120.0


SUPERVISOR_LOG = ROOT / "build" / "supervisor.log"


def _console_python() -> str:
    """The interpreter to launch children with.

    Autostart runs this supervisor under pythonw so logon does not leave a console window, and
    `sys.executable` is then pythonw too. Handing that to a child is a trap: uvicorn writes its
    startup banner to stdout, pythonw has no stdout, and the child exits 1 immediately — a
    restart loop whose only symptom is "api: CRASHED (exit 1) after 2s". Children get the
    console interpreter regardless of how the supervisor itself was started.
    """
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        console = executable.with_name("python.exe")
        if console.exists():
            return str(console)
    return sys.executable


PYTHON = _console_python()


def log(message: str) -> None:
    """Write to stdout *and* to a file.

    Autostart runs this under pythonw, which has no console and throws stdout away — so a
    startup failure there is completely silent unless the log is written here. That is not
    hypothetical: the first autostart attempt failed to bind its ports and left no trace at
    all, and looked from outside exactly like a success.
    """
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    try:
        SUPERVISOR_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(SUPERVISOR_LOG, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def _ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=HEALTH_TIMEOUT_S) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


class Child:
    """One supervised process, restarted on exit or on a failed health check."""

    def __init__(self, name: str, argv: list[str], cwd: Path, health: str | None,
                 env: dict[str, str] | None = None):
        self.name, self.argv, self.cwd, self.health = name, argv, cwd, health
        self.env = {**os.environ, **(env or {})}
        self.proc: subprocess.Popen | None = None
        self.started_at = 0.0
        self.backoff = BACKOFF_START_S
        self.restarts = 0
        self.fails = 0
        self.retry_at = 0.0
        self.serving = False

    log_path: Path | None = None

    def spawn(self) -> None:
        # CREATE_NO_WINDOW, always. Children of a windowless pythonw each allocate their OWN
        # console on Windows, so autostart was popping a llama-server terminal onto the desktop
        # at every logon. With no console their output has nowhere to go, so every child gets
        # a log file — a silent child is the pythonw mistake all over again.
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        target = self.log_path or (SUPERVISOR_LOG.parent / f"{self.name}.log")
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = open(target, "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(self.argv, cwd=str(self.cwd), env=self.env,
                                     stdout=handle, stderr=subprocess.STDOUT,
                                     creationflags=flags)
        self.started_at = time.monotonic()
        self.serving = False
        self.fails = 0
        log(f"{self.name}: started pid {self.proc.pid}"
            + (f" (restart {self.restarts})" if self.restarts else ""))

    def on_running(self) -> None:
        """Called each poll while the process is alive; subclasses can resolve late state."""

    def stop(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)

    def _schedule_restart(self, why: str) -> None:
        uptime = time.monotonic() - self.started_at
        log(f"{self.name}: {why} after {uptime:.0f}s")
        self.stop()
        self.restarts += 1
        # A process that ran fine and then died is a fresh incident, not a crash loop.
        self.backoff = (BACKOFF_START_S if uptime >= STABLE_AFTER_S
                        else min(self.backoff * 2, BACKOFF_MAX_S))
        self.retry_at = time.monotonic() + self.backoff
        self.proc = None
        self.serving = False
        log(f"{self.name}: restarting in {self.backoff:.0f}s ({self.restarts} restarts so far)")

    def poll(self) -> None:
        if self.proc is None:
            if time.monotonic() >= self.retry_at:
                self.spawn()
            return
        code = self.proc.poll()
        if code is not None:
            # 139 SIGSEGV, 134 SIGABRT — a GGML_ASSERT as seen from out here.
            self._schedule_restart(f"CRASHED (exit {code})")
            return
        self.on_running()
        if self.health is not None and _ok(self.health):
            if not self.serving:
                log(f"{self.name}: serving ({time.monotonic() - self.started_at:.0f}s to start)")
                self.serving = True
            self.fails = 0
            return
        if not self.serving:
            if time.monotonic() - self.started_at > STARTUP_TIMEOUT_S:
                self._schedule_restart("never came up")
            return
        if self.health is None:
            return
        self.fails += 1
        log(f"{self.name}: health check failed ({self.fails}/{HEALTH_FAILS_BEFORE_KILL})")
        if self.fails >= HEALTH_FAILS_BEFORE_KILL:
            self._schedule_restart("HUNG")



def publish_tunnel_url(url: str) -> None:
    """Point kaleai.vercel.app's INFERENCE_URL at a fresh tunnel hostname, unattended.

    A quick tunnel's hostname changes on every start, and until Vercel knows the new one the
    site silently serves deterministic designs — a healthy-looking outage. The supervisor is
    the only process that reliably learns the new hostname, so publication is its job:
    update the env var, redeploy, and re-point the alias (the domain is pinned manually and
    does NOT follow production deploys — see kaleai-vercel-alias-pinned).

    Requires the Vercel CLI to be logged in, which it is on this machine. Failures are logged
    and left alone: the site keeps working deterministically, and the URL is still in
    tunnel-url.txt for a manual push.
    """
    last = None
    try:
        last = PUBLISHED_URL_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if url == last:
        log("publish: hostname unchanged, nothing to do")
        return

    # The globally-installed CLI in %APPDATA%\npm is INVISIBLE from here: this supervisor runs
    # under the Microsoft Store Python, which is MSIX-packaged, and the package carries an
    # empty private copy of that directory that shadows the real one — `iterdir()` returns
    # nothing while the files plainly exist on disk, and every child process inherits the same
    # view. So the CLI is vendored inside the repo (a location MSIX does not virtualize):
    #
    #     cd build && npm install vercel --prefix vercel-cli
    vercel = str(ROOT / "build" / "vercel-cli" / "node_modules" / ".bin" / "vercel.cmd")
    if not Path(vercel).exists():
        log("publish: vendored vercel CLI missing — run: "
            "cd build && npm install vercel --prefix vercel-cli")
        return

    def run(argv: list[str], timeout: float, stdin: str | None = None):
        return subprocess.run([vercel, *argv], cwd=str(SITE_DIR), capture_output=True,
                              text=True, timeout=timeout, input=stdin, shell=True)

    log(f"publish: pointing INFERENCE_URL at {url}")
    try:
        run(["env", "rm", "INFERENCE_URL", "production", "--yes"], 120)
        added = run(["env", "add", "INFERENCE_URL", "production"], 120, stdin=url)
        if added.returncode != 0:
            log(f"publish: env add failed: {added.stderr.strip()[:200]}")
            return
        deployed = run(["deploy", "--prod", "--yes"], 600)
        if deployed.returncode != 0:
            log(f"publish: deploy failed: {deployed.stderr.strip()[:200]}")
            return
        found = re.search(r"https://[a-z0-9.-]+\.vercel\.app", deployed.stdout)
        if not found:
            log(f"publish: no deployment url in output: {deployed.stdout.strip()[:200]}")
            return
        deployment = found.group(0)
        aliased = run(["alias", "set", deployment.replace("https://", ""), ALIAS], 180)
        if aliased.returncode != 0:
            log(f"publish: alias failed: {aliased.stderr.strip()[:200]}")
            return
        PUBLISHED_URL_FILE.write_text(url + "\n", encoding="utf-8")
        log(f"publish: {ALIAS} now reaches the model at {url}")
    except (subprocess.TimeoutExpired, OSError) as exc:
        log(f"publish: {type(exc).__name__}: {exc}")


class TunnelChild(Child):
    """cloudflared, whose public hostname is only knowable after it starts.

    A `trycloudflare` quick tunnel is assigned a fresh random hostname on every start, so the
    URL cannot be configured ahead of time — it has to be read back out of the process's own
    output. It is written to build/tunnel-url.txt so that after a reboot there is one obvious
    place to look, instead of grepping a log.

    This is a workaround for using quick tunnels at all. A named tunnel keeps its hostname and
    makes the whole problem disappear; see docs/serving-v18-free.md.
    """

    log_path = TUNNEL_LOG

    def __init__(self, origin: str):
        # --protocol http2 rather than the default QUIC. On a connection where UDP is throttled
        # the QUIC transport registers, then drops with "timeout: no recent network activity"
        # and reconnects in a loop — Cloudflare answers 530 (origin unreachable) the whole time
        # while the origin is perfectly healthy, which is a confusing way to be down.
        # KALE_TUNNEL_PROTOCOL=quic switches back.
        protocol = os.environ.get("KALE_TUNNEL_PROTOCOL", "http2")
        super().__init__("tunnel",
                         [str(CLOUDFLARED), "tunnel", "--url", origin, "--no-autoupdate",
                          "--protocol", protocol],
                         ROOT, None)
        self.url: str | None = None

    def spawn(self) -> None:
        self.url = None
        self.health = None
        super().spawn()

    def on_running(self) -> None:
        if self.url is not None:
            return
        try:
            text = TUNNEL_LOG.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        found = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", text)
        if not found:
            return
        self.url = found.group(0)
        self.health = f"{self.url}/health"
        try:
            TUNNEL_URL_FILE.write_text(self.url + "\n", encoding="utf-8")
        except OSError:
            pass
        log(f"tunnel: {self.url}")
        if WANT_PUBLISH:
            publish_tunnel_url(self.url)
        else:
            log(f"tunnel: auto-publish off — update INFERENCE_URL yourself, "
                f"see {TUNNEL_URL_FILE.name}")


def main() -> int:
    if not Path(MODEL_PATH).exists():
        log(f"model not found: {MODEL_PATH}")
        log("build it with scripts/build-v18-gguf.sh, or set KALE_MODEL_PATH")
        return 1
    if not LLAMA_SERVER.exists():
        log(f"llama-server not found: {LLAMA_SERVER}")
        log("download a llama.cpp release build into build/llamabin/")
        return 1

    # Refuse to be the second supervisor. Two of these fight over the same ports: the loser's
    # children cannot bind, so it restart-loops while the winner's stack looks healthy — which
    # is exactly how a stale supervisor from an earlier session silently kept serving while a
    # newly started one did nothing.
    if _ok(f"http://{HOST}:{API_PORT}/health"):
        log(f"something is already serving http://{HOST}:{API_PORT} — not starting a second "
            f"supervisor. Stop the running one first.")
        return 0

    model_url = f"http://{HOST}:{MODEL_PORT}"
    children = [
        Child("llama-server",
              [str(LLAMA_SERVER), "-m", MODEL_PATH, "-c", CTX, "-t", THREADS,
               "--host", HOST, "--port", MODEL_PORT],
              ROOT, f"{model_url}/health"),
        Child("api",
              [PYTHON, "-m", "uvicorn", "app.main:app", "--host", HOST,
               "--port", API_PORT],
              SERVICE_DIR, f"http://{HOST}:{API_PORT}/health",
              env={"KALE_INFERENCE_PROVIDER": "local_llamaserver",
                   "KALE_LLAMA_SERVER_URL": f"{model_url}/v1",
                   # A geometry proposal is ~2600 tokens: at CPU speed that is up to ~9
                   # minutes, and the provider timing out at the old 300 s default turned
                   # every proposal into "llama-server unreachable" while it was mid-thought.
                   "KALE_LLAMA_SERVER_TIMEOUT": os.environ.get("KALE_LLAMA_SERVER_TIMEOUT", "900"),
                   "KALE_MODEL_VERSION": MODEL_VERSION}),
    ]
    if WANT_TUNNEL and CLOUDFLARED.exists():
        children.append(TunnelChild(f"http://{HOST}:{API_PORT}"))
    elif WANT_TUNNEL:
        log(f"tunnel: {CLOUDFLARED} not found, serving locally only")

    log(f"model  {MODEL_PATH}")
    log(f"llama-server  {model_url}  ({THREADS} threads, {CTX} ctx)")
    log(f"api           http://{HOST}:{API_PORT}")

    def shutdown(_signum=None, _frame=None):
        log("shutting down")
        for child in reversed(children):
            child.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    for child in children:
        child.spawn()
    while True:
        for child in children:
            child.poll()
        time.sleep(POLL_EVERY_S)


if __name__ == "__main__":
    raise SystemExit(main())
