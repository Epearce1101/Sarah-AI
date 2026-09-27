"""SARAH AI backend entry point — thin uvicorn shim around ``create_app``."""
from __future__ import annotations

import os

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONLEGACYWINDOWSSTDIO", "utf-8")

import sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

if __name__ == "__main__":
    # Configure before the app is imported so import-time messages
    # (memory system, persona, LLM init) are captured too.
    from backend.utils.logging_setup import configure as _configure_logging
    _configure_logging()

from backend import lifecycle
from backend.app import create_app
from backend.config import settings as _settings

app = create_app()


def run() -> None:
    import uvicorn

    from backend.utils.logging_setup import log_level_name

    # Pass the app object: an import string makes uvicorn re-import this
    # module under its package name and build the whole app a second time.
    config = uvicorn.Config(
        app,
        host=_settings.backend_host,
        port=_settings.backend_port,
        reload=False,
        log_level=log_level_name().lower(),
        timeout_keep_alive=120,
        limit_concurrency=1000,
    )
    server = uvicorn.Server(config)
    # POST /api/shutdown (launcher-only) flips this so the lifespan shutdown
    # (Ollama manager, vision session, Piper daemon) runs before exit;
    # a hard kill skips all of that.
    lifecycle.set_shutdown_handler(lambda: setattr(server, "should_exit", True))
    server.run()


if __name__ == "__main__":
    run()
