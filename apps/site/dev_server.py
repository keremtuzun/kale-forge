"""Local dev server for the studio function: python dev_server.py [port].

Serves the same handler Vercel runs at /studio and /api/studio, plus the static
files next to it, so the studio page, the viewer and the JSON path can all be
exercised without a deploy. KALE_DEV=1 makes the handler re-read studio-viewer.js
per request and (dev only) skip the remote auth check.
"""
import os
import sys
from http.server import ThreadingHTTPServer

os.environ.setdefault("KALE_DEV", "1")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from api.studio import handler  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899


class dev_handler(handler):
    def do_GET(self):  # noqa: N802
        # The auth API lives on the production domain; locally, answer it ourselves so the
        # sign-in gate closes and the studio is usable.
        if self.path.startswith("/api/auth/me"):
            body = b'{"name": "Local Dev"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    # Dev-only text drop box: lets one browser tab hand a generated FeatureScript to
    # another origin (e.g. an Onshape tab) that CORS would otherwise wall off. Plain
    # text both ways, held in memory, never persisted.
    _dropped: dict[str, bytes] = {}

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/fs-get"):
            body = self._dropped.get("fs", b"")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self):  # noqa: N802
        if self.path.startswith("/fs-drop"):
            length = int(self.headers.get("Content-Length") or 0)
            self._dropped["fs"] = self.rfile.read(min(length, 2_000_000))
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        return self._orig_post()

    def _orig_post(self):  # noqa: N802
        # Dev-only capture receiver: the page POSTs a data-URL PNG here so renders can be
        # inspected without a visible browser (headless canvases screenshot as stale).
        if self.path.startswith("/capture"):
            import base64
            import re as _re
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8", "replace")
            m = _re.search(r"base64,(.+)", body)
            name = _re.search(r"[?&]name=([\w-]+)", self.path)
            out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures",
                               (name.group(1) if name else "capture") + ".png")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            if m:
                with open(out, "wb") as fh:
                    fh.write(base64.b64decode(m.group(1)))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        # Everything else is a real API call — hand it to the Vercel handler. This used to
        # answer 404 unconditionally, so the whole POST design API was untestable locally.
        super().do_POST()


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), dev_handler)
    print(f"studio dev server on http://127.0.0.1:{PORT}")
    server.serve_forever()
