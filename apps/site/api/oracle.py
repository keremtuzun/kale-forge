"""Proxy for the app pages still served by the Oracle VM.

Those pages come from a separate source tree that is not in this repo and the VM is only
reachable with an SSH key that is not on this machine, so their header cannot be fixed at
source. They are brought in line on the way through instead: the "Models & Evals" entry is
dropped, the header's "Design Studio" link is sent to this site's studio rather than the old
one on the VM, and the palette is pulled onto the same beige as the rest of the site.

Anything that is not an HTML document — RSC payloads for client-side navigation, JSON, assets —
is passed through untouched. Rewriting those would break routing.

When the VM's own source becomes editable again this whole file should go away, and the plain
rewrites in vercel.json should come back.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, quote, urlencode, urlparse

ORIGIN = "https://kaleai-app.150.136.212.31.nip.io"
TIMEOUT = 20

# Request headers worth carrying to the origin. Cookie is what keeps the session working, and
# the RSC/Next-* set is what tells the origin a client-side navigation is asking, not a browser.
_FORWARD = (
    "cookie", "user-agent", "accept", "accept-language", "referer",
    "rsc", "next-router-state-tree", "next-router-prefetch", "next-url",
)
# Response headers worth carrying back. Content-Length and Content-Encoding are deliberately
# absent: the body is rewritten, so both would be wrong.
_RETURN = ("content-type", "set-cookie", "cache-control", "location", "vary", "x-nextjs-cache")

PATCH = """
<style id="kale-beige">
  :root{--kale-bg:#f6f3ea;--kale-surface:#fbf9f2;--kale-line:#c7cbc4;--kale-ink:#171a18}
  html,body{background:var(--kale-bg)!important;color:var(--kale-ink)!important}
  /* The app paints white onto a handful of utility classes; this is the whole of it. */
  .bg-white,.bg-background,.bg-card,.bg-popover,.bg-muted,.bg-gray-50,.bg-slate-50,.bg-neutral-50,
  header,main,footer,dialog,[role=dialog]{background-color:var(--kale-surface)!important}
  input,textarea,select{background-color:var(--kale-bg)!important;color:var(--kale-ink)!important}
  hr,.border,.border-b,.border-t,.border-l,.border-r{border-color:var(--kale-line)!important}
</style>
<script>
(function () {
  "use strict";
  // The nav links are now correct in the app's own React source (no /models link,
  // Design Studio → /studio), so the old MutationObserver that rewrote the DOM on
  // every render is gone. One thing still needs the edge: /studio is a separate
  // function on this domain, not a Next route, so a Next <Link> click must become
  // a real navigation or the client router 404s it internally.
  document.addEventListener("click", function (e) {
    var a = e.target && e.target.closest && e.target.closest('a[href="/studio"]');
    if (!a || e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    location.assign("/studio");
  }, true);
})();
</script>
"""


def _origin_url(raw_path: str) -> str:
    """Rebuild the origin URL from the request.

    Each rewrite in vercel.json names its own target as `?path=`, because a function behind a
    rewrite cannot be relied on to see the URL the browser actually asked for. Vercel merges the
    incoming query string onto the destination's, so everything else in the query is the
    caller's and is handed straight back to the origin.
    """
    parsed = urlparse(raw_path)
    query = parse_qs(parsed.query, keep_blank_values=True)
    target = (query.pop("path", ["/"]) or ["/"])[0]
    if not target.startswith("/"):
        target = "/" + target
    rest = urlencode(query, doseq=True)
    return ORIGIN + quote(target, safe="/%:@&=+$,~") + ("?" + rest if rest else "")


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self._proxy()

    def do_HEAD(self) -> None:  # noqa: N802
        self._proxy()

    def _proxy(self) -> None:
        request = urllib.request.Request(
            _origin_url(self.path),
            headers={k: v for k in _FORWARD if (v := self.headers.get(k))},
            method=self.command,
        )
        try:
            # Redirects are returned to the browser rather than followed, so the origin's own
            # /login?next=... round trip keeps working.
            opener = urllib.request.build_opener(_NoRedirect)
            with opener.open(request, timeout=TIMEOUT) as resp:
                status, headers, body = resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            status, headers, body = exc.code, exc.headers, exc.read()
        except Exception as exc:  # origin down, DNS, timeout
            self._fail(f"The application server did not answer ({exc.__class__.__name__}).")
            return

        content_type = headers.get("Content-Type", "")
        if "text/html" in content_type.lower():
            body = _inject(body)

        self.send_response(status)
        for key in _RETURN:
            for value in headers.get_all(key) or []:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _fail(self, message: str) -> None:
        body = (
            '<!doctype html><meta charset="utf-8"><title>Unavailable | Kale Forge</title>'
            '<style>body{margin:0;min-height:100vh;display:grid;place-items:center;'
            "background:#f6f3ea;color:#171a18;font:15px/1.6 Inter,system-ui,sans-serif;padding:24px}"
            "a{color:#188449}</style><div><p>" + message + "</p>"
            '<p><a href="/">Back to the welcome page</a> · <a href="/studio">Design Studio</a></p></div>'
        ).encode()
        self.send_response(502)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, D102
        return None


def _inject(body: bytes) -> bytes:
    text = body.decode("utf-8", "replace")
    marker = "</body>"
    index = text.rfind(marker)
    if index == -1:
        return (text + PATCH).encode()
    return (text[:index] + PATCH + text[index:]).encode()
