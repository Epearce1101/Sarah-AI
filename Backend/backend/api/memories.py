"""Memory-browser endpoints (browse, pin, unpin, manual add)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from backend.api.schemas import MemoryCreate
from backend.models.core import (
    add_memory,
    delete_memory,
    get_memories,
    get_pinned_memories,
    search_memories,
    set_memory_pinned,
)

router = APIRouter()


@router.get("/api/memories")
def api_get_memories(limit: int = 50, keyword: str | None = None):
    try:
        if keyword:
            mems = search_memories(keyword)
        else:
            mems = get_memories(limit=limit)
        return {"ok": True, "memories": mems}
    except Exception as e:
        logging.exception("[/api/memories] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/memories/pinned")
def api_get_pinned_memories(limit: int = 100):
    try:
        mems = get_pinned_memories(limit=limit)
        return {"ok": True, "memories": mems}
    except Exception as e:
        logging.exception("[/api/memories/pinned] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/memories")
def api_add_memory_manual(payload: MemoryCreate):
    try:
        add_memory(
            role=payload.role,
            content=payload.content,
            tags=payload.tags or "",
            importance=payload.importance or 0,
        )
        return {"ok": True}
    except Exception as e:
        logging.exception("[/api/memories POST] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/memories/{memory_id}")
def api_delete_memory(memory_id: int):
    """Forget a memory (e.g. a wrong auto-extracted fact)."""
    if not delete_memory(memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"ok": True, "deleted_id": memory_id}


@router.post("/api/memories/{memory_id}/pin")
def api_pin_memory(memory_id: int):
    try:
        set_memory_pinned(memory_id, True)
        return {"ok": True, "pinned": True, "id": memory_id}
    except Exception as e:
        logging.exception("[/api/memories/{id}/pin] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/memories/{memory_id}/unpin")
def api_unpin_memory(memory_id: int):
    try:
        set_memory_pinned(memory_id, False)
        return {"ok": True, "pinned": False, "id": memory_id}
    except Exception as e:
        logging.exception("[/api/memories/{id}/unpin] failed")
        raise HTTPException(status_code=500, detail=str(e))
