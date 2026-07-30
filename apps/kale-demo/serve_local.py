"""Serve the Vercel function locally for development.

Vercel runs `api/design.py`'s `handler` for every request; this wraps the same class in a
stdlib HTTPServer so the page can be opened and verified before deploying. Nothing here
ships — Vercel never imports this file.

    python3 serve_local.py [port]
"""
from __future__ import annotations

import os
import sys
from http.server import HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.design import handler  # noqa: E402

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    print(f"kale-demo on http://localhost:{port}", flush=True)
    HTTPServer(("127.0.0.1", port), handler).serve_forever()
