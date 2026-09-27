"""Conversation + message endpoints."""
from __future__ import annotations

import json
import logging
import sqlite3

from fastapi import APIRouter, HTTPException

from backend.api.schemas import (
    ConversationCreate,
    ConversationRename,
    MessageCreate,
    MessageUpdate,
    PinMessage,
)
from backend.db import get_connection
from backend.models.core import (
    add_message,
    create_conversation,
    delete_message,
    delete_messages_after,
    get_messages,
    get_pinned_messages,
    list_conversations,
    pin_message,
    rename_conversation,
    search_messages,
    update_message,
)

router = APIRouter()


@router.get("/api/conversations")
def api_list_conversations():
    return {"ok": True, "conversations": list_conversations()}


@router.post("/api/conversations")
def api_create_conversation(payload: ConversationCreate):
    conv_id = create_conversation(payload.title)
    return {"ok": True, "conversation_id": conv_id}


@router.get("/api/conversations/{conversation_id}/messages")
def api_get_conversation_messages(conversation_id: int, limit: int = 200):
    msgs = get_messages(conversation_id, limit=limit)
    return {"ok": True, "messages": msgs}


@router.post("/api/conversations/{conversation_id}/messages")
def api_add_message(conversation_id: int, payload: MessageCreate):
    meta_json_str = json.dumps(payload.meta_json) if payload.meta_json else None
    add_message(conversation_id, payload.role, payload.content, meta_json_str)
    return {"ok": True}


@router.patch("/api/messages/{message_id}")
def api_update_message(message_id: int, payload: MessageUpdate):
    """Update a message's content."""
    try:
        success = update_message(message_id, payload.content)
        if success:
            return {"ok": True}
        raise HTTPException(status_code=404, detail="Message not found")
    except HTTPException:
        raise
    except Exception as e:
        logging.exception(f"[PATCH /api/messages/{message_id}] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/messages/{message_id}")
def api_delete_message(message_id: int):
    """Delete a single message."""
    try:
        success = delete_message(message_id)
        if success:
            return {"ok": True}
        raise HTTPException(status_code=404, detail="Message not found")
    except HTTPException:
        raise
    except Exception as e:
        logging.exception(f"[DELETE /api/messages/{message_id}] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/conversations/{conversation_id}/messages/after/{message_id}")
def api_delete_messages_after(conversation_id: int, message_id: int):
    """Delete all messages after a given message (for regeneration)."""
    try:
        count = delete_messages_after(conversation_id, message_id)
        return {"ok": True, "deleted_count": count}
    except Exception as e:
        logging.exception(f"[DELETE messages after {message_id}] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/messages/search")
def api_search_messages(q: str, limit: int = 50):
    """Search messages across all conversations."""
    try:
        results = search_messages(q, limit)
        return {"ok": True, "results": results}
    except Exception as e:
        logging.exception("[GET /api/messages/search] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/messages/{message_id}/pin")
def api_pin_message(message_id: int, payload: PinMessage):
    """Pin or unpin a message."""
    try:
        success = pin_message(message_id, payload.pinned)
        if success:
            return {"ok": True}
        raise HTTPException(status_code=404, detail="Message not found")
    except HTTPException:
        raise
    except Exception as e:
        logging.exception(f"[POST /api/messages/{message_id}/pin] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/messages/pinned")
def api_get_pinned_messages(limit: int = 50):
    """Get all pinned messages."""
    try:
        messages = get_pinned_messages(limit)
        return {"ok": True, "messages": messages}
    except Exception as e:
        logging.exception("[GET /api/messages/pinned] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/conversations/{conversation_id}/title")
def api_rename_conversation(conversation_id: int, payload: ConversationRename):
    try:
        rename_conversation(conversation_id, payload.title)
        return {"ok": True}
    except Exception as e:
        logging.exception("[/api/conversations/{id}/title] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/conversations/{conversation_id}")
def api_delete_conversation(conversation_id: int):
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        cur.execute(
            "DELETE FROM conversations WHERE id = ?",
            (conversation_id,),
        )
        # mood_state has no FK to conversations, so ON DELETE CASCADE never
        # reaches it; clear it explicitly (table may not exist yet).
        try:
            cur.execute(
                "DELETE FROM mood_state WHERE conversation_id = ?",
                (conversation_id,),
            )
        except sqlite3.OperationalError:
            pass
        conn.commit()
        conn.close()
        return {"ok": True, "deleted_id": conversation_id}
    except Exception as e:
        logging.exception("[DELETE /api/conversations/{id}] failed")
        raise HTTPException(status_code=500, detail=str(e))
