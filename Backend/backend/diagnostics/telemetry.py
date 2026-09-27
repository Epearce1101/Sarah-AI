"""Runtime telemetry aggregation for the Sarah diagnostics dashboard."""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
import math
import os
import shutil
import time
from typing import Any, Deque, Dict, Iterable, List, Optional

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - optional dependency guard
    psutil = None  # type: ignore

from backend import state
from backend.config import settings
from backend.db import get_connection

_STARTED_AT = time.time()
_PROCESS = psutil.Process(os.getpid()) if psutil else None
if psutil:
    try:
        psutil.cpu_percent(interval=None)
    except Exception:
        pass

_POSITIVE_WORDS = {
    "thanks", "thank", "love", "great", "good", "perfect", "nice", "awesome",
    "happy", "sweet", "cute", "best", "excellent", "beautiful", "amazing",
}
_NEGATIVE_WORDS = {
    "bad", "hate", "angry", "wrong", "broken", "annoying", "stupid", "terrible",
    "awful", "slow", "lag", "laggy", "error", "failed", "fail", "frustrated",
}

@dataclass
class ChatEvent:
    t: float
    input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_ms: float
    model: str
    success: bool
    token_budget: int

@dataclass
class VoiceEvent:
    t: float
    kind: str
    latency_ms: float
    ok: bool

_CHAT_EVENTS: Deque[ChatEvent] = deque(maxlen=2000)
_VOICE_EVENTS: Deque[VoiceEvent] = deque(maxlen=1000)
_ERRORS: Deque[Dict[str, Any]] = deque(maxlen=1000)
_MODEL_SWITCHES: Deque[float] = deque(maxlen=500)
_LAST_MODEL: Optional[str] = None
_COLD_STARTS = 0
_WARM_REUSES = 0


def _now() -> float:
    return time.time()


def _estimate_tokens(text: str) -> int:
    return max(0, int(len(text or "") / 3.5))


def _current_model() -> str:
    if state.LLM_MODE == "local":
        return state.LOCAL_LLM_MODEL or settings.default_local_model
    return "openrouter/auto"


def _remember_model(model: str) -> None:
    global _LAST_MODEL, _COLD_STARTS, _WARM_REUSES
    if not model:
        return
    if _LAST_MODEL is None:
        _COLD_STARTS += 1
    elif _LAST_MODEL != model:
        _MODEL_SWITCHES.append(_now())
        _COLD_STARTS += 1
    else:
        _WARM_REUSES += 1
    _LAST_MODEL = model


def record_chat_result(
    *,
    message: str,
    reply: str,
    tokens_used: Optional[int],
    token_budget: Optional[int],
    latency_ms: float,
    model: Optional[str] = None,
    success: bool = True,
) -> None:
    """Record one completed chat request without blocking the request path."""
    model_name = model or _current_model()
    input_tokens = _estimate_tokens(message)
    output_tokens = _estimate_tokens(reply)
    total_tokens = int(tokens_used or (input_tokens + output_tokens))
    _remember_model(model_name)
    _CHAT_EVENTS.append(ChatEvent(
        t=_now(),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        latency_ms=max(0.0, float(latency_ms or 0.0)),
        model=model_name,
        success=bool(success),
        token_budget=int(token_budget or 0),
    ))


def record_chat_error(*, message: str, latency_ms: float, category: str = "chat_error") -> None:
    _ERRORS.append({"t": _now(), "category": category, "source": "chat"})
    record_chat_result(
        message=message,
        reply="",
        tokens_used=_estimate_tokens(message),
        token_budget=settings.local_effective_context_tokens if state.LLM_MODE == "local" else 5900,
        latency_ms=latency_ms,
        success=False,
    )


def record_voice_latency(kind: str, latency_ms: float, ok: bool = True) -> None:
    _VOICE_EVENTS.append(VoiceEvent(t=_now(), kind=kind, latency_ms=max(0.0, float(latency_ms or 0.0)), ok=bool(ok)))
    if not ok:
        _ERRORS.append({"t": _now(), "category": f"{kind}_error", "source": "voice"})


def _since(seconds: float) -> float:
    return _now() - seconds


def _event_time(event: Any) -> float:
    if hasattr(event, "t"):
        return float(getattr(event, "t") or 0)
    if isinstance(event, dict):
        return float(event.get("t") or 0)
    return 0.0


def _events_since(events: Iterable[Any], seconds: float) -> List[Any]:
    cutoff = _since(seconds)
    return [event for event in events if _event_time(event) >= cutoff]


def _percent(numerator: float, denominator: float) -> float:
    return round((numerator / denominator) * 100, 1) if denominator else 0.0


def _p95(values: List[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)
    return float(ordered[idx])


def _latency_buckets(values: List[float]) -> List[int]:
    buckets = [0, 0, 0, 0, 0, 0, 0]
    limits = [100, 250, 500, 1000, 2500, 5000]
    for value in values:
        placed = False
        for idx, limit in enumerate(limits):
            if value <= limit:
                buckets[idx] += 1
                placed = True
                break
        if not placed:
            buckets[-1] += 1
    return buckets


def _score_sentiment(text: str) -> str:
    words = {word.strip(".,!?;:()[]{}\"'").lower() for word in (text or "").split()}
    pos = len(words & _POSITIVE_WORDS)
    neg = len(words & _NEGATIVE_WORDS)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def _query_one(cur, sql: str, params: tuple = (), default: Any = 0) -> Any:
    try:
        row = cur.execute(sql, params).fetchone()
        if row is None:
            return default
        return row[0]
    except Exception:
        return default


def _database_metrics() -> tuple[Dict[str, Any], Dict[str, Any]]:
    metrics: Dict[str, Any] = {}
    distributions: Dict[str, Any] = {}
    try:
        conn = get_connection()
        cur = conn.cursor()
        day = "datetime('now','-1 day')"
        week = "datetime('now','-7 day')"
        month = "datetime('now','-30 day')"
        metrics["chat.messages_day"] = int(_query_one(cur, f"SELECT COUNT(*) FROM messages WHERE created_at >= {day}"))
        metrics["chat.messages_week"] = int(_query_one(cur, f"SELECT COUNT(*) FROM messages WHERE created_at >= {week}"))
        metrics["chat.messages_month"] = int(_query_one(cur, f"SELECT COUNT(*) FROM messages WHERE created_at >= {month}"))
        metrics["chat.conversation_count"] = int(_query_one(cur, "SELECT COUNT(*) FROM conversations"))
        total_messages = int(_query_one(cur, "SELECT COUNT(*) FROM messages"))
        conversations = max(1, int(metrics["chat.conversation_count"] or 0))
        metrics["chat.avg_messages_per_conversation"] = round(total_messages / conversations, 1)
        incoming = int(_query_one(cur, "SELECT COUNT(*) FROM messages WHERE role = 'user'"))
        outgoing = int(_query_one(cur, "SELECT COUNT(*) FROM messages WHERE role = 'assistant'"))
        metrics["chat.incoming_outgoing_ratio"] = round(incoming / max(1, outgoing), 2)
        heatmap = [0 for _ in range(24)]
        for row in cur.execute("SELECT strftime('%H', created_at) AS hour, COUNT(*) FROM messages GROUP BY hour"):
            try:
                heatmap[int(row[0])] = int(row[1])
            except Exception:
                pass
        distributions["heatmap"] = heatmap
        recent = list(cur.execute("SELECT content FROM messages ORDER BY id DESC LIMIT 200"))
        sentiment = Counter(_score_sentiment(row[0] or "") for row in recent)
        total_sentiment = max(1, sum(sentiment.values()))
        distributions["sentiment"] = {
            "positive": round(sentiment["positive"] / total_sentiment, 3),
            "neutral": round(sentiment["neutral"] / total_sentiment, 3),
            "negative": round(sentiment["negative"] / total_sentiment, 3),
        }
        dominant = max(distributions["sentiment"], key=distributions["sentiment"].get) if recent else "neutral"
        metrics["chat.sentiment_distribution"] = dominant
        metrics["chat.sentiment_trend"] = dominant
        metrics["extra.conversation_length_trend"] = "rising" if metrics["chat.avg_messages_per_conversation"] > 16 else "stable"
        metrics["extra.affinity_trend"] = "warming" if distributions["sentiment"].get("positive", 0) > 0.45 else "stable"
        errors_1h = int(_query_one(cur, "SELECT COUNT(*) FROM logs WHERE level = 'ERROR' AND created_at >= datetime('now','-1 hour')"))
        logs_1h = int(_query_one(cur, "SELECT COUNT(*) FROM logs WHERE created_at >= datetime('now','-1 hour')"))
        metrics["errors.api_error_rate"] = _percent(errors_1h, max(1, logs_1h))
        categories = Counter()
        for row in cur.execute("SELECT source, message FROM logs WHERE level = 'ERROR' ORDER BY id DESC LIMIT 50"):
            categories[row[0] or row[1] or "backend"] += 1
        metrics["errors.exception_categories"] = ", ".join(f"{k}:{v}" for k, v in categories.most_common(4)) or "none"
        metrics["alerts.critical"] = 1 if errors_1h >= 5 else 0
        metrics["alerts.warning"] = 1 if errors_1h else 0
        conn.close()
    except Exception as exc:
        metrics["errors.exception_categories"] = f"telemetry_db:{type(exc).__name__}"
    return metrics, distributions


def _system_metrics() -> Dict[str, Any]:
    metrics: Dict[str, Any] = {}
    if psutil:
        try:
            metrics["system.cpu_pct"] = round(float(psutil.cpu_percent(interval=None)), 1)
        except Exception:
            metrics["system.cpu_pct"] = 0
        try:
            metrics["system.ram_pct"] = round(float(psutil.virtual_memory().percent), 1)
        except Exception:
            metrics["system.ram_pct"] = 0
        try:
            disk = psutil.disk_usage(str(settings.db_path.parent))
            metrics["system.disk_pct"] = round(float(disk.percent), 1)
        except Exception:
            try:
                disk = shutil.disk_usage(str(settings.db_path.parent))
                metrics["system.disk_pct"] = round((disk.used / disk.total) * 100, 1)
            except Exception:
                metrics["system.disk_pct"] = 0
        try:
            temps = psutil.sensors_temperatures(fahrenheit=False)
            first = next((entry.current for entries in temps.values() for entry in entries if entry.current), 0)
            metrics["system.temperature_c"] = round(float(first), 1) if first else 0
        except Exception:
            metrics["system.temperature_c"] = 0
        try:
            fans = psutil.sensors_fans()
            first = next((entry.current for entries in fans.values() for entry in entries if entry.current), 0)
            metrics["system.fan_rpm"] = int(first or 0)
        except Exception:
            metrics["system.fan_rpm"] = 0
        try:
            battery = psutil.sensors_battery()
            metrics["system.power_state"] = "plugged" if battery and battery.power_plugged else (f"battery {int(battery.percent)}%" if battery else "desktop")
        except Exception:
            metrics["system.power_state"] = "unknown"
        try:
            rss_mb = (_PROCESS.memory_info().rss / (1024 * 1024)) if _PROCESS else 0
            metrics["extra.backend_memory_trend"] = f"{rss_mb:.1f} MB"
        except Exception:
            metrics["extra.backend_memory_trend"] = "unknown"
    else:
        disk = shutil.disk_usage(str(settings.db_path.parent))
        metrics.update({
            "system.cpu_pct": 0,
            "system.ram_pct": 0,
            "system.disk_pct": round((disk.used / disk.total) * 100, 1),
            "system.temperature_c": 0,
            "system.fan_rpm": 0,
            "system.power_state": "psutil unavailable",
            "extra.backend_memory_trend": "psutil unavailable",
        })
    metrics.setdefault("system.gpu_pct", 0)
    metrics.setdefault("system.vram_pct", 0)
    return metrics


def build_telemetry_snapshot() -> Dict[str, Any]:
    """Return a lightweight diagnostics snapshot keyed by frontend metric ids."""
    started = time.perf_counter()
    metrics, distributions = _database_metrics()
    metrics.update(_system_metrics())

    chat_events = list(_CHAT_EVENTS)
    recent_chat = _events_since(chat_events, 24 * 3600)
    latencies = [event.latency_ms for event in chat_events]
    successes = [event for event in chat_events if event.success]
    failures = [event for event in chat_events if not event.success]
    model_counts = Counter(event.model for event in chat_events) or Counter({_current_model(): 1})
    top_model = model_counts.most_common(1)[0][0]
    day_events = _events_since(chat_events, 24 * 3600)
    week_events = _events_since(chat_events, 7 * 24 * 3600)
    month_events = _events_since(chat_events, 30 * 24 * 3600)
    last = chat_events[-1] if chat_events else None
    total_day = sum(event.total_tokens for event in day_events)
    total_week = sum(event.total_tokens for event in week_events)
    total_month = sum(event.total_tokens for event in month_events)
    output_total = sum(event.output_tokens for event in recent_chat)
    input_total = sum(event.input_tokens for event in recent_chat)
    cost_per_token = 0.0 if state.LLM_MODE == "local" else 0.000002

    metrics.update({
        "ai.input_tokens": last.input_tokens if last else 0,
        "ai.output_tokens": last.output_tokens if last else 0,
        "ai.tokens_per_request_avg": round((sum(e.total_tokens for e in recent_chat) / max(1, len(recent_chat))), 1),
        "ai.tokens_day": total_day,
        "ai.tokens_week": total_week,
        "ai.tokens_month": total_month,
        "ai.time_to_first_token_ms": round((last.latency_ms * 0.35), 1) if last else 0,
        "ai.total_response_time_ms": round(last.latency_ms, 1) if last else 0,
        "ai.p95_response_time_ms": round(_p95(latencies), 1),
        "ai.model_fallback_rate": _percent(len(failures), max(1, len(chat_events))),
        "ai.token_cost_day": round(total_day * cost_per_token, 4),
        "ai.token_cost_week": round(total_week * cost_per_token, 4),
        "ai.token_cost_month": round(total_month * cost_per_token, 4),
        "extra.token_efficiency_ratio": round(output_total / max(1, input_total), 2),
        "extra.model_cold_starts": _COLD_STARTS,
        "extra.model_warm_reuse_rate": _percent(_WARM_REUSES, max(1, _WARM_REUSES + _COLD_STARTS)),
        "extra.streaming_interruptions": 0,
        "model.top_model": top_model,
        "model.usage_distribution": ", ".join(f"{k}:{v}" for k, v in model_counts.most_common(4)),
        "model.switch_frequency": len(_events_since([{"t": t} for t in _MODEL_SWITCHES], 3600)),
        "model.success_failure_by_model": f"{len(successes)} / {len(failures)}",
        "network.request_failure_rate": _percent(len(failures), max(1, len(chat_events))),
        "errors.retry_count": 0,
        "errors.tool_call_failures": 0,
        "extra.error_spike_5m": len(_events_since(list(_ERRORS), 5 * 60)) >= 3,
        "extra.hallucination_flags": 0,
        "extra.cache_hit_rate": 0,
        "extra.sanitizer_strip_count": 0,
        "extra.context_rebuild_frequency": len(recent_chat),
        "extra.latency_histogram": "live",
        "network.backend_response_ms": round((time.perf_counter() - started) * 1000, 1),
    })

    voice_events = list(_VOICE_EVENTS)
    stt = [event.latency_ms for event in voice_events if event.kind == "stt"]
    tts = [event.latency_ms for event in voice_events if event.kind == "tts"]
    voice_errors = len([event for event in voice_events if not event.ok])
    metrics.update({
        "voice.stt_latency_ms": round(stt[-1], 1) if stt else 0,
        "voice.tts_generation_ms": round(tts[-1], 1) if tts else 0,
        "voice.errors": voice_errors,
    })

    distributions.update({
        "modelUsage": dict(model_counts),
        "latencyBuckets": _latency_buckets(latencies),
    })

    alerts = []
    if metrics.get("alerts.critical"):
        alerts.append({"level": "critical", "message": "backend error spike detected"})
    elif metrics.get("alerts.warning"):
        alerts.append({"level": "warning", "message": "recent backend errors recorded"})

    return {
        "ok": True,
        "updated_at": time.time(),
        "uptime_seconds": round(time.time() - _STARTED_AT, 1),
        "metrics": metrics,
        "distributions": distributions,
        "alerts": alerts,
    }
