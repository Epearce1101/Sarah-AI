"""Process-wide logging for the backend server.

Everything goes to stdout, which start.py captures into a per-launch log
file (rotated by size and pruned by count there). Level comes from
SARAH_LOG_LEVEL (default INFO); per-request/per-poll details are DEBUG.
"""
from __future__ import annotations

import logging
import os
import sys

_NOISY = ("httpx", "httpcore", "openai", "urllib3", "asyncio", "PIL", "multipart", "aiohttp.access")


def log_level_name() -> str:
    return (os.environ.get("SARAH_LOG_LEVEL") or "INFO").upper()


def configure() -> None:
    root = logging.getLogger()
    if getattr(root, "_sarah_configured", False):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S"
    ))
    root.addHandler(handler)
    root.setLevel(log_level_name())
    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)
    root._sarah_configured = True
