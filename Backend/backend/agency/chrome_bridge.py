"""Sarah in Zero's real Chrome, through the Sarah Browser Bridge extension.

The extension (``chrome-extension/`` in the project, loaded once in Chrome)
connects to ``/ws/chrome``; only that extension's id is accepted. While it's
connected her ``browser`` tool works in Chrome itself (Zero's tabs, logins
and cookies) instead of her separate browser: she opens her own tab and
leaves Zero's alone unless asked (``use_tab``).
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("sarah.chrome")

EXTENSION_ID = "ffbkelalaoededopdcocdgbdeddfbcfk"
ALLOWED_ORIGIN = f"chrome-extension://{EXTENSION_ID}"
ACTIONS = ("tabs", "open", "use_tab", "read", "click", "type", "select", "press", "scroll", "back",
           "forward", "extract", "tables", "look", "close")


class ChromeBridge:
    def __init__(self) -> None:
        self.ws = None
        self.info: Dict[str, Any] = {}
        self.connected_at: Optional[float] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._ids = itertools.count(1)

    def connected(self) -> bool:
        return self.ws is not None

    def attach(self, ws) -> None:
        self.ws = ws
        self.connected_at = time.time()
        logger.info("[CHROME] bridge connected")

    def detach(self, ws) -> None:
        if self.ws is ws:
            self.ws = None
            self.connected_at = None
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("Chrome disconnected"))
            self._pending.clear()
            logger.info("[CHROME] bridge disconnected")

    def on_message(self, msg: Dict[str, Any]) -> None:
        if msg.get("type") == "hello":
            self.info = {"version": msg.get("version"), "ua": str(msg.get("ua", ""))[:200]}
            return
        fut = self._pending.pop(str(msg.get("id")), None) if msg.get("id") else None
        if fut and not fut.done():
            if msg.get("ok"):
                fut.set_result(msg.get("result"))
            else:
                fut.set_exception(RuntimeError(str(msg.get("error") or "failed")))

    async def call(self, action: str, args: Optional[Dict[str, Any]] = None, timeout: float = 60) -> Any:
        if self.ws is None:
            raise ConnectionError("Chrome isn't connected")
        req_id = f"c{next(self._ids)}"
        fut = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        await self.ws.send_json({"id": req_id, "action": action, "args": args or {}})
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(req_id, None)

    async def act(self, action: str, **kwargs) -> Any:
        """The browser tool's actions, done in Chrome."""
        if action not in ACTIONS:
            raise ValueError(f"unknown browser action {action!r}")
        args = {k: v for k, v in kwargs.items() if v is not None and k != "visible"}
        if action == "look":
            return await self._look(args)
        return await self.call(action, args, timeout=75)

    async def _look(self, args: Dict[str, Any]) -> Any:
        from backend.perception import sight

        shot = await self.call("screenshot", {k: v for k, v in args.items() if k == "tab_id"})
        blocked = sight.budget.check(urgent=True)
        if blocked:
            return f"Can't look right now: {blocked}"
        ok = limited = False
        try:
            seen = await sight.look(shot["jpeg_base64"], None, args.get("question") or "What is on this page?")
            ok = True
        except sight.RateLimited:
            limited = True
            return "Vision is rate limited right now; use read instead."
        finally:
            sight.budget.done(ok=ok, rate_limited=limited)
        return {"url": shot.get("url"), "seen": {k: v for k, v in seen.items() if not k.startswith("_")}}

    def status(self) -> Dict[str, Any]:
        return {"connected": self.connected(), "since": self.connected_at, **self.info,
                "extension_id": EXTENSION_ID}


bridge = ChromeBridge()
