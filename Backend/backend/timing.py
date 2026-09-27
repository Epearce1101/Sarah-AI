"""B6 timing instrumentation.

All timed blocks are gated behind the `B6_TIMING=1` env var so they're silent
in normal runs and visible only during the bench script (P3.6).

Usage:
    from backend.timing import stage

    with stage("tts.synth", cold=True, text_len=120):
        # ... work ...
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)


_ENABLED = os.getenv("B6_TIMING") == "1"


def enabled() -> bool:
    return _ENABLED


@contextmanager
def stage(name: str, **kwargs):
    if not _ENABLED:
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        ms = int((time.perf_counter() - t0) * 1000)
        extras = " ".join(f"{k}={v}" for k, v in kwargs.items())
        line = f"[TIMING] stage={name} ms={ms}"
        if extras:
            line = f"{line} {extras}"
        logger.info(line)
