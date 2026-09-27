"""Auth header for scripts that call a running Sarah backend.

start.py generates a per-run API token and writes it to TOKEN_FILE for the
session (it's deleted on shutdown). SARAH_API_TOKEN in the environment wins.
With no token anywhere, no header is sent (backend started without one).
"""
import os
from pathlib import Path

TOKEN_FILE = Path(__file__).resolve().parent / "Backend" / "data" / ".api_token"
TOKEN_HEADER = "X-Sarah-Token"


def backend_headers() -> dict:
    token = os.environ.get("SARAH_API_TOKEN", "").strip()
    if not token:
        try:
            token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            token = ""
    return {TOKEN_HEADER: token} if token else {}
