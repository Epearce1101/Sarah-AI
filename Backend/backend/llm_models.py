"""Which OpenRouter models Sarah uses, and whether they still exist.

Single source of truth for the online chat model so a UI change applies to
every caller (chat, summaries, code tools, screen analysis) without a
restart, plus:

* **Fallbacks.** Every chat request sends OpenRouter's ``models`` list
  (primary + up to two fallbacks, OpenRouter's maximum is 3 entries), so a
  retired or overloaded primary rolls over instead of failing every message —
  what happened when ``openrouter/owl-alpha`` was withdrawn.
* **Availability check.** The public model catalog is fetched at startup (and
  after a switch) and the result is exposed to the UI, so a dead model shows
  up as a warning in the header instead of as an error reply per message.
* **Persistence.** A model picked in the UI is stored in the ``settings``
  table and restored at boot; ``SARAH_OPENROUTER_MODEL`` is only the default.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import requests

from backend.config import settings

logger = logging.getLogger("sarah.models")

MODEL_SETTING_KEY = "openrouter_model"
MAX_MODELS_PER_REQUEST = 3  # OpenRouter rejects longer `models` arrays
CATALOG_URL = "https://openrouter.ai/api/v1/models"
CATALOG_TTL_SECONDS = 3600
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._~-]+/[A-Za-z0-9._~:+-]+$")

_lock = threading.Lock()
_override: Optional[str] = None
_catalog: Dict[str, Any] = {"fetched_at": 0.0, "models": []}
_status: Dict[str, Any] = {"model": None, "available": None, "checked_at": None, "error": None}
_listeners: List[Callable[[str], None]] = []


# ---------------------------------------------------------------------------
# Current model
# ---------------------------------------------------------------------------

def current_online_model() -> str:
    return _override or settings.openrouter_model


def current_vision_model() -> str:
    return settings.openrouter_vision_model


def fallback_models() -> List[str]:
    primary = current_online_model()
    seen = {primary}
    out: List[str] = []
    for model in settings.openrouter_fallback_models:
        if model and model not in seen:
            seen.add(model)
            out.append(model)
    return out[: MAX_MODELS_PER_REQUEST - 1]


def request_models() -> List[str]:
    return [current_online_model(), *fallback_models()]


def completion_kwargs(*, reasoning: bool = True, model: Optional[str] = None) -> Dict[str, Any]:
    """`model` + `extra_body` for an OpenAI-SDK call to OpenRouter.

    Passing `model` pins a specific one (e.g. the vision model) with no
    fallbacks; otherwise the chat model and its fallbacks are sent.
    """
    extra: Dict[str, Any] = {}
    if model is None:
        model = current_online_model()
        fallbacks = fallback_models()
        if fallbacks:
            extra["models"] = [model, *fallbacks]
    if reasoning:
        effort = (settings.openrouter_reasoning_effort or "").strip().lower()
        if effort and effort != "none":
            extra["reasoning"] = {"effort": effort, "exclude": True}
    return {"model": model, "extra_body": extra or None}


VISION_MIN_COMPLETION_TOKENS = 1500


def vision_request_body(messages: List[Dict[str, Any]], max_tokens: int, temperature: float = 0.2) -> Dict[str, Any]:
    """JSON body for an OpenRouter vision call.

    Reasoning models spend hidden tokens before answering; with the small
    budgets the screen-analysis code used (96-400) they returned empty text.
    """
    return {
        "model": current_vision_model(),
        "messages": messages,
        "max_tokens": max(int(max_tokens), VISION_MIN_COMPLETION_TOKENS),
        "temperature": temperature,
        "reasoning": {"effort": "low", "exclude": True},
    }


def request_body_extras(*, reasoning: bool = True) -> Dict[str, Any]:
    """Same as completion_kwargs, flattened for raw `requests` JSON bodies."""
    kw = completion_kwargs(reasoning=reasoning)
    body = {"model": kw["model"]}
    body.update(kw["extra_body"] or {})
    return body


def on_model_change(callback: Callable[[str], None]) -> None:
    _listeners.append(callback)


def set_online_model(model: str, *, persist: bool = True) -> str:
    """Switch the chat model at runtime (validated against the catalog)."""
    global _override
    model = (model or "").strip()
    if not _MODEL_ID_RE.match(model):
        raise ValueError(f"not a valid OpenRouter model id: {model!r}")
    ids = {m["id"] for m in fetch_catalog()}
    if ids and model not in ids:
        raise ValueError(f"model {model!r} is not in OpenRouter's current catalog")
    with _lock:
        _override = None if model == settings.openrouter_model else model
    if persist:
        from backend.models.core import set_setting
        set_setting(MODEL_SETTING_KEY, model)
    for callback in list(_listeners):
        try:
            callback(model)
        except Exception as exc:  # a listener must not block the switch
            logger.warning("model-change listener failed: %s", exc)
    check_availability()
    logger.info("Online model set to %s", model)
    return model


def load_persisted_model() -> None:
    """Restore a model chosen in the UI on a previous run."""
    global _override
    try:
        from backend.models.core import get_setting
        stored = (get_setting(MODEL_SETTING_KEY) or "").strip()
    except Exception as exc:
        logger.warning("could not read persisted model: %s", exc)
        return
    if stored and _MODEL_ID_RE.match(stored):
        with _lock:
            _override = None if stored == settings.openrouter_model else stored


# ---------------------------------------------------------------------------
# Catalog + availability
# ---------------------------------------------------------------------------

def fetch_catalog(max_age: float = CATALOG_TTL_SECONDS) -> List[Dict[str, Any]]:
    """OpenRouter's public model list (no key needed), cached."""
    now = time.time()
    if _catalog["models"] and now - _catalog["fetched_at"] < max_age:
        return _catalog["models"]
    try:
        resp = requests.get(CATALOG_URL, timeout=15)
        resp.raise_for_status()
        models = resp.json().get("data") or []
    except Exception as exc:
        logger.warning("model catalog fetch failed: %s", exc)
        return _catalog["models"]
    _catalog.update(fetched_at=now, models=models)
    return models


def _price_per_million(value: Any) -> Optional[float]:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return None if price < 0 else round(price * 1_000_000, 4)


def catalog_summary(text_only: bool = True) -> List[Dict[str, Any]]:
    """Compact, sorted list for the UI model picker (free models first)."""
    out = []
    for m in fetch_catalog():
        arch = m.get("architecture") or {}
        outputs = arch.get("output_modalities") or ["text"]
        if text_only and "text" not in outputs:
            continue
        pricing = m.get("pricing") or {}
        prompt = _price_per_million(pricing.get("prompt"))
        completion = _price_per_million(pricing.get("completion"))
        out.append({
            "id": m["id"],
            "name": m.get("name") or m["id"],
            "context_length": m.get("context_length") or 0,
            "prompt_per_million": prompt,
            "completion_per_million": completion,
            "free": prompt == 0 and completion == 0,
            "vision": "image" in (arch.get("input_modalities") or []),
        })
    out.sort(key=lambda m: (not m["free"], m["id"]))
    return out


def check_availability() -> Dict[str, Any]:
    """Look the current model and fallbacks up in the catalog."""
    model = current_online_model()
    models = fetch_catalog(max_age=0)
    ids = {m["id"] for m in models}
    status: Dict[str, Any] = {
        "model": model,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "fallbacks": [{"id": f, "available": (f in ids) if ids else None} for f in fallback_models()],
        "vision_model": current_vision_model(),
        "vision_available": (current_vision_model() in ids) if ids else None,
    }
    if not ids:
        status.update(available=None, error="could not reach OpenRouter model catalog")
    else:
        status.update(available=model in ids, error=None)
        if model not in ids:
            logger.warning("Configured model %s is not in OpenRouter's catalog", model)
    with _lock:
        _status.clear()
        _status.update(status)
    return dict(status)


def mark_unavailable(model: str, reason: str) -> None:
    """Record a hard failure seen on a live request (e.g. 404 no endpoints)."""
    with _lock:
        if _status.get("model") in (None, model):
            _status.update(model=model, available=False, error=reason[:300],
                           checked_at=datetime.now(timezone.utc).isoformat())


def availability_status() -> Dict[str, Any]:
    with _lock:
        status = dict(_status)
    if status.get("model") != current_online_model():
        status = {"model": current_online_model(), "available": None, "checked_at": None, "error": None}
    return status


def status_fields() -> Dict[str, Any]:
    """Fields merged into /api/llm_mode and /api/context_info payloads."""
    status = availability_status()
    warning = None
    if status.get("available") is False:
        warning = (
            f"{status['model']} is not available on OpenRouter"
            + (f" ({status['error']})" if status.get("error") else "")
            + "; requests fall back to: " + (", ".join(fallback_models()) or "none")
        )
    return {
        "model_available": status.get("available"),
        "model_warning": warning,
        "fallback_models": fallback_models(),
        "vision_model": current_vision_model(),
    }
