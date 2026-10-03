"""Sarah's sight: what's on your screen and in front of her camera.

The renderer watches both sources locally and only sends a frame when the
view has meaningfully changed. Here a free cloud vision model describes it
(one request for both images), and the observation joins her self-model so
every prompt knows what she sees. Frames are never stored; only the short
text description is kept (in memory).

Free models only: any configured model without ":free" is dropped. Calls
are budgeted (daily cap, minimum interval) and back off on rate limits, so
sight never starves conversation of the shared free quota.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from datetime import date
from typing import Any, Dict, List, Optional

import requests

from backend.config import settings
from backend.identity import get_user_name

logger = logging.getLogger("sarah.sight")


def free_vision_models() -> List[str]:
    return [m for m in settings.vision_models if m.strip().endswith(":free")][:3]


class VisionBudget:
    """How often she may look: daily cap, spacing, and rate-limit backoff."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.day = date.today()
        self.used = 0
        self.last_at = 0.0
        self.backoff_until = 0.0
        self._backoff_s = 60.0
        self.in_flight = False

    def _roll(self) -> None:
        if date.today() != self.day:
            self.day, self.used = date.today(), 0

    def check(self, urgent: bool = False) -> Optional[str]:
        """None if she may look now, else the reason she can't."""
        with self._lock:
            self._roll()
            now = time.time()
            if self.in_flight:
                return "already looking"
            if now < self.backoff_until:
                return f"rate limited ({int(self.backoff_until - now)} s)"
            if self.used >= settings.vision_daily_cap:
                return "daily budget used"
            if not urgent and now - self.last_at < self._interval():
                return "too soon"
            self.in_flight = True
            self.last_at = now
            return None

    def _interval(self) -> float:
        """Spacing between looks; widens as the day's budget runs down so a
        busy hour (a game) can't spend the whole day's sight."""
        base = float(settings.vision_min_interval_seconds)
        share = self.used / max(1, settings.vision_daily_cap)
        return base * (4 if share >= 0.8 else 2 if share >= 0.5 else 1)

    def done(self, *, ok: bool, rate_limited: bool = False) -> None:
        with self._lock:
            self.in_flight = False
            self.used += 1
            if rate_limited:
                self.backoff_until = time.time() + self._backoff_s
                self._backoff_s = min(self._backoff_s * 2, 900.0)
            elif ok:
                self._backoff_s = 60.0

    def status(self) -> Dict[str, Any]:
        with self._lock:
            self._roll()
            return {
                "used_today": self.used,
                "daily_cap": settings.vision_daily_cap,
                "min_interval_seconds": int(self._interval()),
                "backoff_seconds": max(0, int(self.backoff_until - time.time())),
                "models": free_vision_models(),
            }


budget = VisionBudget()


def _prompt(has_screen: bool, has_camera: bool, question: Optional[str]) -> str:
    user = get_user_name()
    parts = ["You are the eyes of Sarah, a desktop companion who lives in a window on "
             f"{user}'s PC."]
    order = []
    if has_screen:
        order.append(f"image {len(order) + 1} is {user}'s screen")
    if has_camera:
        order.append(f"image {len(order) + 1} is {user}'s webcam (pointed at {user})")
    parts.append("Here " + " and ".join(order) + ".")
    schema = {}
    if has_screen:
        schema["screen"] = {"app": "<name of the app or game in front>", "activity": f"<what {user} is doing>",
                            "details": "<specific visible content: file names, text, game state; max 30 words>"}
    if has_camera:
        schema["camera"] = {"present": "<true or false: a real person is in view>",
                            "doing": "<what they're doing, max 10 words>",
                            "mood": "<expression / body language, max 6 words>"}
    schema["notable"] = "<one thing a friend watching would react to, or null>"
    if has_screen:
        # So she can circle it on screen when she brings it up (show_on_screen finds text exactly).
        schema["notable_text"] = ("<if that notable thing is on the screen and has words (an error, a message, "
                                  "a title, a button), those words exactly as shown, max 12 words; else null>")
    if question:
        schema["answer"] = f"<answer to: {question}>"
    parts.append("If the screen mainly shows Sarah's own chat window, say so briefly and describe what's around it.")
    parts.append("Reply with JSON only, no prose: " + json.dumps(schema))
    return " ".join(parts)


def _parse(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
    return {"notable": None, "raw": text[:300]}


def _call(content: List[Dict[str, Any]]) -> Dict[str, Any]:
    models = free_vision_models()
    if not models:
        raise RuntimeError("no free vision models configured")
    body = {
        "model": models[0],
        "models": models,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 700,
        "temperature": 0.2,
        # Reasoning off: 2.9 s instead of 18.7 s, and it no longer eats the
        # token budget and truncates the answer (measured on dots-3-note).
        "reasoning": {"enabled": False},
    }
    r = requests.post(
        f"{settings.openrouter_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
        json=body,
        timeout=60,
    )
    data = r.json()
    if r.status_code == 429 or (isinstance(data.get("error"), dict) and data["error"].get("code") == 429):
        raise RateLimited(str(data.get("error"))[:200])
    if "error" in data:
        raise RuntimeError(str(data["error"])[:300])
    served = data.get("model") or models[0]
    if not str(served).endswith(":free") and served not in models:
        logger.warning("[SIGHT] served by unexpected model %s", served)
    return {"text": data["choices"][0]["message"].get("content") or "", "model": served}


class RateLimited(RuntimeError):
    pass


async def look(screen_b64: Optional[str] = None, camera_b64: Optional[str] = None,
               question: Optional[str] = None) -> Dict[str, Any]:
    """Describe the given frames (base64 JPEG). Caller holds the budget."""
    content: List[Dict[str, Any]] = [{"type": "text", "text": _prompt(bool(screen_b64), bool(camera_b64), question)}]
    n = 0
    # Each image right after its own label, so the model can't swap them.
    for label, b64 in (("the SCREEN", screen_b64), ("the WEBCAM", camera_b64)):
        if b64:
            n += 1
            content.append({"type": "text", "text": f"Image {n}: {label}"})
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    started = time.time()
    from backend.usage import using

    with using("vision"):
        result = await asyncio.to_thread(_call, content)
    seen = _parse(result["text"])
    seen["_model"] = result["model"]
    seen["_ms"] = int((time.time() - started) * 1000)
    return seen


async def ground(screen_b64: str, width: int, height: int, target: str) -> Optional[List[int]]:
    """Where `target` is in a screenshot of `width` x `height` pixels, as
    [x1, y1, x2, y2] in its pixels, or None. Caller holds the budget.

    Vision models are good at finding things but not exact about edges, so
    callers treat the box as approximate. Some answer in 0-1 or 0-1000
    coordinates instead of pixels; both are converted.
    """
    prompt = (
        f"This is a screenshot of a computer screen, {width} x {height} pixels. "
        f"Find this on it: \"{target}\". Reply with JSON only: "
        '{"found": true, "box": [x1, y1, x2, y2]} in pixels of this image (origin top-left), '
        'tight around it, or {"found": false} if it is not visible.'
    )
    content = [{"type": "text", "text": prompt},
               {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screen_b64}"}}]
    from backend.usage import using

    with using("vision"):
        result = await asyncio.to_thread(_call, content)
    data = _parse(result["text"])
    box = data.get("box") if data.get("found") else None
    if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)):
        return None
    x1, y1, x2, y2 = (float(v) for v in box)
    if max(x1, y1, x2, y2) <= 1.0:                       # 0-1
        x1, x2, y1, y2 = x1 * width, x2 * width, y1 * height, y2 * height
    elif x2 > width * 1.05 or y2 > height * 1.05:        # 0-1000
        x1, x2, y1, y2 = x1 * width / 1000, x2 * width / 1000, y1 * height / 1000, y2 * height / 1000
    x1, x2 = sorted((max(0.0, min(width, x1)), max(0.0, min(width, x2))))
    y1, y2 = sorted((max(0.0, min(height, y1)), max(0.0, min(height, y2))))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return None
    return [int(x1), int(y1), int(x2), int(y2)]
