"""Serve the Vercel Design Studio function locally for visual verification."""
from __future__ import annotations

import os
import sys
from http.server import HTTPServer
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ["KALE_DEV"] = "1"

from api.studio import handler  # noqa: E402


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8790
    print(f"Kale Forge studio on http://127.0.0.1:{port}", flush=True)
    HTTPServer(("127.0.0.1", port), handler).serve_forever()
