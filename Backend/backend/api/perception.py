"""Sight endpoints: the renderer sends camera/screen frames when the view
changes; Sarah looks (within budget) and remembers what she saw."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.embodiment import get_self
from backend.perception import sight

router = APIRouter()
logger = logging.getLogger("sarah.sight")

MAX_B64 = 3_000_000  # ~2.2 MB JPEG


class LookRequest(BaseModel):
    screen: Optional[str] = Field(None, description="base64 JPEG of the screen")
    camera: Optional[str] = Field(None, description="base64 JPEG from the webcam")
    reason: str = Field("change", max_length=40)
    question: Optional[str] = Field(None, max_length=300)
    urgent: bool = False


@router.post("/api/perception/look")
async def perception_look(req: LookRequest):
    screen = req.screen if req.screen and len(req.screen) <= MAX_B64 else None
    camera = req.camera if req.camera and len(req.camera) <= MAX_B64 else None
    if not screen and not camera:
        return {"ok": False, "skipped": "no frames"}
    blocked = sight.budget.check(urgent=req.urgent)
    if blocked:
        return {"ok": True, "skipped": blocked, "budget": sight.budget.status()}
    ok = rate_limited = False
    try:
        seen = await sight.look(screen, camera, req.question)
        ok = True
    except sight.RateLimited as exc:
        rate_limited = True
        logger.info("[SIGHT] rate limited: %s", exc)
        return {"ok": True, "skipped": "rate limited", "budget": sight.budget.status()}
    except Exception as exc:
        logger.warning("[SIGHT] look failed: %s", exc)
        return {"ok": False, "error": str(exc)[:200]}
    finally:
        sight.budget.done(ok=ok, rate_limited=rate_limited)
    get_self().see(seen)
    logger.info("[SIGHT] %s (%s, %d ms): %s", req.reason, seen.get("_model"), seen.get("_ms", 0),
                str({k: v for k, v in seen.items() if not k.startswith("_")})[:240])
    return {"ok": True, "seen": seen, "budget": sight.budget.status()}


@router.get("/api/perception/status")
def perception_status():
    me = get_self().snapshot()
    return {"budget": sight.budget.status(), "sight": me.get("sight"), "noticed": me.get("sight_log")}
