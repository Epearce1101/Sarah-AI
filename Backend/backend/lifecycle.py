"""Graceful-shutdown hook shared by the server runner and the API."""
from __future__ import annotations

from typing import Callable, Optional

_shutdown_handler: Optional[Callable[[], None]] = None


def set_shutdown_handler(handler: Callable[[], None]) -> None:
    global _shutdown_handler
    _shutdown_handler = handler


def request_shutdown() -> bool:
    """Ask the running server to exit after finishing in-flight requests."""
    if _shutdown_handler is None:
        return False
    _shutdown_handler()
    return True
