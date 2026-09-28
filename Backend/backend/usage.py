"""How much of the free OpenRouter allowance Sarah is using today.

Every request to OpenRouter's chat endpoint is counted where it leaves the
process (``install()`` wraps the `requests` and `httpx` transports), so all
callers are covered, old and new. Each count carries a category (what she
was doing: chat, vision, initiative, presence, memory, journal, tools...)
taken from a context variable the caller sets with ``using("vision")``;
unlabelled requests count as "chat".

Free-model limits reset daily (UTC), so the day here is the UTC date. Any
request to a model without ":free" is flagged: Zero doesn't pay for models.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional

from backend.config import settings

logger = logging.getLogger("sarah.usage")

_category: contextvars.ContextVar[str] = contextvars.ContextVar("sarah_usage_category", default="chat")
_lock = threading.Lock()
_installed = False
_ENDPOINT = "/chat/completions"


def daily_limit() -> int:
    return int(getattr(settings, "free_daily_request_limit", 1000))


@contextlib.contextmanager
def using(category: str) -> Iterator[None]:
    token = _category.set(category)
    try:
        yield
    finally:
        _category.reset(token)


def current_category() -> str:
    return _category.get()


def _day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _ensure_table(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS llm_usage (day TEXT, category TEXT, model TEXT, requests INTEGER DEFAULT 0,"
        " rate_limited INTEGER DEFAULT 0, failed INTEGER DEFAULT 0, PRIMARY KEY (day, category, model))"
    )


def record(model: Optional[str], status: Optional[int], category: Optional[str] = None) -> None:
    """Count one request (status None = transport error)."""
    category = category or _category.get()
    model = (model or "unknown")[:120]
    limited = int(status == 429)
    failed = int(status is None or (status >= 400 and status != 429))
    try:
        from backend.models.core import get_connection

        with _lock:
            conn = get_connection()
            try:
                _ensure_table(conn)
                conn.execute(
                    "INSERT INTO llm_usage (day, category, model, requests, rate_limited, failed) VALUES (?, ?, ?, 1, ?, ?)"
                    " ON CONFLICT(day, category, model) DO UPDATE SET requests = requests + 1,"
                    " rate_limited = rate_limited + excluded.rate_limited, failed = failed + excluded.failed",
                    (_day(), category, model, limited, failed),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception as exc:
        logger.debug("usage not recorded: %s", exc)
    if model != "unknown" and not model.endswith(":free") and "openrouter/free" not in model:
        logger.warning("[USAGE] request to a non-free model: %s (%s)", model, category)


def today() -> Dict[str, Any]:
    """Totals for today (UTC), by category, plus warnings."""
    rows = []
    try:
        from backend.models.core import get_connection

        conn = get_connection()
        try:
            _ensure_table(conn)
            rows = conn.execute(
                "SELECT category, model, requests, rate_limited, failed FROM llm_usage WHERE day = ?", (_day(),)
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("usage unavailable: %s", exc)
    by_category: Dict[str, int] = {}
    total = limited = failed = 0
    paid = set()
    for category, model, n, rl, fl in (tuple(r) for r in rows):
        by_category[category] = by_category.get(category, 0) + n
        total += n
        limited += rl
        failed += fl
        if model != "unknown" and not str(model).endswith(":free"):
            paid.add(model)
    limit = daily_limit()
    return {
        "day_utc": _day(),
        "used": total,
        "limit": limit,
        "remaining": max(0, limit - total),
        "share": round(total / limit, 3) if limit else 0,
        "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
        "rate_limited": limited,
        "failed": failed,
        "non_free_models": sorted(paid),
    }


def _model_from_body(body: Any) -> Optional[str]:
    try:
        if isinstance(body, (bytes, bytearray)):
            body = body.decode("utf-8", "ignore")
        if isinstance(body, str):
            body = json.loads(body)
        if isinstance(body, dict):
            return body.get("model")
    except Exception:
        return None
    return None


def _is_openrouter_chat(url: str) -> bool:
    return "openrouter.ai" in url and _ENDPOINT in url


def install() -> None:
    """Count every OpenRouter chat request made through requests or httpx."""
    global _installed
    if _installed:
        return
    _installed = True

    import requests.adapters

    original_send = requests.adapters.HTTPAdapter.send

    def counted_send(self, request, *args, **kwargs):
        if not _is_openrouter_chat(request.url or ""):
            return original_send(self, request, *args, **kwargs)
        model = _model_from_body(request.body)
        try:
            response = original_send(self, request, *args, **kwargs)
        except Exception:
            record(model, None)
            raise
        record(model, response.status_code)
        return response

    requests.adapters.HTTPAdapter.send = counted_send

    import httpx

    original_httpx_send = httpx.Client.send

    def counted_httpx_send(self, request, *args, **kwargs):
        url = str(request.url)
        if not _is_openrouter_chat(url):
            return original_httpx_send(self, request, *args, **kwargs)
        try:
            body = request.content  # raises for an unread streaming body
        except Exception:
            body = None
        model = _model_from_body(body)
        try:
            response = original_httpx_send(self, request, *args, **kwargs)
        except Exception:
            record(model, None)
            raise
        record(model, response.status_code)
        return response

    httpx.Client.send = counted_httpx_send
