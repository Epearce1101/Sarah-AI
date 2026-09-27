"""SARAH AI backend entry point — thin uvicorn shim around ``create_app``."""
from __future__ import annotations

import os

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONLEGACYWINDOWSSTDIO", "utf-8")

import sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from backend.app import create_app
from backend.config import settings as _settings

app = create_app()


def run() -> None:
    import uvicorn

    # Pass the app object: an import string makes uvicorn re-import this
    # module under its package name and build the whole app a second time.
    uvicorn.run(
        app,
        host=_settings.backend_host,
        port=_settings.backend_port,
        reload=False,
        log_level="debug",
        timeout_keep_alive=120,
        limit_concurrency=1000,
    )


if __name__ == "__main__":
    run()
