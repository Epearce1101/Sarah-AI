"""The backend's line to Sarah's body in the renderer (/ws/senses).

Her mind uses it to ask the body for things (``request("look", ...)`` takes
a fresh look through her eyes and answers a question) and to make her act
without being asked (``push({"type": "say", ...})`` shows and speaks a line,
e.g. a reminder or something she decided to say on her own).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger("sarah.senses")


class Senses:
    def __init__(self) -> None:
        self.clients: list = []
        self.pending: Dict[str, asyncio.Future] = {}

    @property
    def connected(self) -> bool:
        return bool(self.clients)

    def attach(self, ws) -> None:
        self.clients.append(ws)

    def detach(self, ws) -> None:
        if ws in self.clients:
            self.clients.remove(ws)

    async def push(self, message: Dict[str, Any]) -> bool:
        """Send to the body; True if a renderer is there to receive it."""
        for ws in reversed(self.clients):
            try:
                await ws.send_json(message)
                return True
            except Exception:
                self.detach(ws)
        return False

    async def request(self, kind: str, payload: Dict[str, Any], timeout: float = 30) -> Optional[Any]:
        if not self.clients:
            return None
        rid = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        try:
            if not await self.push({"type": kind, "id": rid, **payload}):
                return None
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            logger.info("[SENSES] %s timed out", kind)
            return None
        finally:
            self.pending.pop(rid, None)

    def resolve(self, rid: str, data: Any) -> None:
        fut = self.pending.get(rid)
        if fut and not fut.done():
            fut.set_result(data)


senses = Senses()
