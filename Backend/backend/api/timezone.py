"""Timezone + local-time endpoints."""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException

from backend.api.schemas import TimezoneUpdate
from backend.db import get_connection

router = APIRouter()


@router.get("/api/conversations/{conversation_id}/timezone")
def api_get_timezone(conversation_id: int):
    """Get stored timezone for a conversation."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT timezone, updated_at FROM conversation_timezones WHERE conversation_id = ?",
            (conversation_id,),
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            return {"ok": True, "timezone": row[0], "updated_at": row[1]}
        return {
            "ok": True,
            "timezone": None,
            "message": "No timezone set for this conversation",
        }
    except Exception as e:
        logging.exception(f"[GET /api/conversations/{conversation_id}/timezone] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/conversations/{conversation_id}/timezone")
def api_set_timezone(conversation_id: int, payload: TimezoneUpdate):
    """Set timezone for a conversation."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO conversation_timezones (conversation_id, timezone, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(conversation_id) DO UPDATE SET
                timezone = excluded.timezone,
                updated_at = CURRENT_TIMESTAMP
            """,
            (conversation_id, payload.timezone),
        )
        conn.commit()
        conn.close()
        logging.info(f"[Timezone] Set timezone for conversation {conversation_id}: {payload.timezone}")
        return {
            "ok": True,
            "conversation_id": conversation_id,
            "timezone": payload.timezone,
        }
    except Exception as e:
        logging.exception(f"[POST /api/conversations/{conversation_id}/timezone] failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/time/local")
def api_get_local_time(timezone: str | None = None):
    """Get current local time. Query: ?timezone=IANA (defaults to UTC)."""
    try:
        import zoneinfo  # Python 3.9+

        logging.info(f"[Time API] Requested timezone: {timezone}")

        if timezone:
            try:
                tz = zoneinfo.ZoneInfo(timezone)
                local_time = datetime.now(tz)
                logging.info(f"[Time API] Successfully got time for {timezone}: {local_time}")
                return {
                    "ok": True,
                    "timezone": timezone,
                    "current_time": local_time.strftime("%I:%M %p"),
                    "current_time_24h": local_time.strftime("%H:%M"),
                    "current_date": local_time.strftime("%Y-%m-%d"),
                    "day_of_week": local_time.strftime("%A"),
                    "full_datetime": local_time.strftime("%A, %B %d, %Y at %I:%M %p"),
                    "iso_datetime": local_time.isoformat(),
                }
            except Exception as ze:
                logging.warning(f"[Time API] zoneinfo failed for {timezone}: {ze}, trying pytz...")
                try:
                    import pytz

                    tz = pytz.timezone(timezone)
                    local_time = datetime.now(tz)
                    logging.info(f"[Time API] pytz succeeded for {timezone}: {local_time}")
                    return {
                        "ok": True,
                        "timezone": timezone,
                        "current_time": local_time.strftime("%I:%M %p"),
                        "current_time_24h": local_time.strftime("%H:%M"),
                        "current_date": local_time.strftime("%Y-%m-%d"),
                        "day_of_week": local_time.strftime("%A"),
                        "full_datetime": local_time.strftime("%A, %B %d, %Y at %I:%M %p"),
                        "iso_datetime": local_time.isoformat(),
                    }
                except Exception as pe:
                    logging.error(
                        f"[Time API] Both zoneinfo and pytz failed for {timezone}: zoneinfo={ze}, pytz={pe}"
                    )

        logging.warning("[Time API] Falling back to UTC")
        utc_time = datetime.utcnow()
        return {
            "ok": True,
            "timezone": "UTC",
            "current_time": utc_time.strftime("%I:%M %p UTC"),
            "current_date": utc_time.strftime("%Y-%m-%d"),
            "day_of_week": utc_time.strftime("%A"),
            "full_datetime": utc_time.strftime("%A, %B %d, %Y at %I:%M %p UTC"),
            "iso_datetime": utc_time.isoformat(),
        }
    except Exception as e:
        logging.exception("[GET /api/time/local] failed")
        raise HTTPException(status_code=500, detail=str(e))
