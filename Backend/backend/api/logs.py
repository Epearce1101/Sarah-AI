"""Log retrieval endpoint."""
from __future__ import annotations

from fastapi import APIRouter

from backend.models.core import get_logs

router = APIRouter()


@router.get("/api/logs")
def api_get_logs(limit: int = 200):
    return {"ok": True, "logs": get_logs(limit=limit)}
