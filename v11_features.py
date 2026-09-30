# backend/v11_features.py
"""
Sarah V11 features, wired into the FastAPI app with one call:

    from backend import v11_features
    v11_features.setup(app, get_sarah)

  - Durable background tasks + notifications (DBOS)      /api/durable_tasks, /api/notifications
  - Procedure memory (MemP-style)                        /api/procedures
  - Proactive suggestions that learn from feedback       /api/proactive
  - Screen timeline + "what happened so far" summaries   /api/screen/timeline
  - Document reading / Q&A (Docling)                     /api/documents

Each feature is optional: if a library is missing, only that feature is
switched off and /api/v11/status says why.

DBOS endpoints are plain `def` on purpose (see durable_tasks.py).
"""
import asyncio
import hashlib
import logging
import threading
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from backend import db
from backend import procedure_memory, proactive_engine, screen_timeline, document_reader

try:
    from backend import durable_tasks
    DURABLE_IMPORT_ERROR: Optional[str] = None
except Exception as e:  # dbos not installed, etc.
    durable_tasks = None  # type: ignore
    DURABLE_IMPORT_ERROR = str(e)

log = logging.getLogger("sarah.v11")
router = APIRouter()

_get_sarah: Optional[Callable[[], Any]] = None
_durable_error: Optional[str] = DURABLE_IMPORT_ERROR

# analyze_last re-serves the same screenshot on every UI poll; only a NEW
# screenshot should count as a timeline sighting or trigger a suggestion.
_last_frame_key: Optional[str] = None
_frame_lock = threading.Lock()


# ------------------------------------------------------------
# LLM helpers
# ------------------------------------------------------------
def _async_llm(max_tokens: int):
    async def _call(prompt: str) -> str:
        if _get_sarah is None:
            raise RuntimeError("v11_features.setup() was not called")
        return await _get_sarah()._call_llm(prompt, max_tokens=max_tokens)
    return _call


# ------------------------------------------------------------
# Request bodies
# ------------------------------------------------------------
class DurableTaskCreate(BaseModel):
    title: str
    instructions: str = ""
    delay_seconds: float = 0
    wait_for_approval: bool = False
    approval_timeout_seconds: float = 86400


class ApprovalBody(BaseModel):
    approve: bool = True


class GoodBadBody(BaseModel):
    good: bool


class ProactiveCheck(BaseModel):
    category: str
    text: str
    context: Optional[str] = None


class ProactiveFeedback(BaseModel):
    outcome: str  # accepted | rejected | ignored


class DocumentRead(BaseModel):
    source: str               # local file path or http(s) URL
    max_chars: Optional[int] = None


class DocumentAsk(BaseModel):
    source: str
    question: str


# ------------------------------------------------------------
# Status
# ------------------------------------------------------------
@router.get("/api/v11/status")
def api_v11_status():
    return {
        "durable_tasks": durable_tasks is not None and durable_tasks.is_running(),
        "durable_tasks_error": _durable_error,
        "docling": document_reader.docling_available(),
        "procedure_memory": True,
        "proactive": True,
        "screen_timeline": True,
    }


# ------------------------------------------------------------
# Durable tasks (DBOS)
# ------------------------------------------------------------
def _require_durable():
    if durable_tasks is None or not durable_tasks.is_running():
        raise HTTPException(503, f"Durable tasks unavailable: {_durable_error or 'not started'}")


@router.post("/api/durable_tasks")
def api_durable_create(payload: DurableTaskCreate):
    _require_durable()
    if not payload.title.strip():
        raise HTTPException(400, "title is required")
    if payload.delay_seconds < 0 or payload.approval_timeout_seconds <= 0:
        raise HTTPException(400, "delay_seconds must be >= 0 and approval_timeout_seconds > 0")
    return durable_tasks.start_background_task(
        payload.title.strip(),
        payload.instructions,
        payload.delay_seconds,
        payload.wait_for_approval,
        payload.approval_timeout_seconds,
    )


@router.get("/api/durable_tasks")
def api_durable_list(limit: int = 20):
    _require_durable()
    return {"tasks": durable_tasks.list_tasks(limit)}


@router.get("/api/durable_tasks/{workflow_id}")
def api_durable_status(workflow_id: str):
    _require_durable()
    info = durable_tasks.get_task_status(workflow_id)
    if info is None:
        raise HTTPException(404, "Unknown task")
    return info


@router.post("/api/durable_tasks/{workflow_id}/approve")
def api_durable_approve(workflow_id: str, payload: ApprovalBody):
    _require_durable()
    outcome = durable_tasks.answer_approval(workflow_id, payload.approve)
    if outcome == "not_found":
        raise HTTPException(404, "Unknown task")
    if outcome == "not_waiting":
        raise HTTPException(409, "That task isn't waiting for approval (finished, timed out, or never asked)")
    return {"ok": True}


@router.post("/api/durable_tasks/{workflow_id}/feedback")
def api_durable_feedback(workflow_id: str, payload: GoodBadBody):
    _require_durable()
    result = durable_tasks.give_feedback(workflow_id, payload.good)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", "Unknown task"))
    return result


@router.get("/api/notifications")
def api_notifications(unread_only: bool = True, limit: int = 50):
    if durable_tasks is None:
        return {"notifications": []}
    return {"notifications": durable_tasks.get_notifications(unread_only, limit)}


@router.post("/api/notifications/{notification_id}/read")
def api_notification_read(notification_id: int):
    if durable_tasks is None or not durable_tasks.mark_notification_read(notification_id):
        raise HTTPException(404, "Unknown notification")
    return {"ok": True}


# ------------------------------------------------------------
# Procedure memory
# ------------------------------------------------------------
@router.get("/api/procedures")
def api_procedures(include_retired: bool = False, limit: int = 100):
    return {"procedures": procedure_memory.list_procedures(include_retired, limit)}


@router.get("/api/procedures/search")
def api_procedures_search(task: str, limit: int = 3):
    return {"procedures": procedure_memory.find_procedures(task, limit)}


@router.post("/api/procedures/{procedure_id}/feedback")
def api_procedure_feedback(procedure_id: int, payload: GoodBadBody):
    if not procedure_memory.record_feedback(procedure_id, payload.good):
        raise HTTPException(404, "Unknown procedure")
    return {"ok": True}


# ------------------------------------------------------------
# Proactive suggestions
# ------------------------------------------------------------
@router.post("/api/proactive/check")
def api_proactive_check(payload: ProactiveCheck):
    if not payload.category.strip() or not payload.text.strip():
        raise HTTPException(400, "category and text are required")
    return proactive_engine.maybe_offer(payload.category.strip(), payload.text, payload.context)


@router.post("/api/proactive/{suggestion_id}/feedback")
def api_proactive_feedback(suggestion_id: int, payload: ProactiveFeedback):
    try:
        found = proactive_engine.record_feedback(suggestion_id, payload.outcome)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not found:
        raise HTTPException(404, "Unknown suggestion")
    return {"ok": True}


@router.get("/api/proactive/stats")
def api_proactive_stats():
    return proactive_engine.stats()


# ------------------------------------------------------------
# Screen timeline
# ------------------------------------------------------------
def on_screen_analysis(description: Optional[str], suggestions: Optional[str],
                       frame: Optional[str] = None) -> Dict[str, Any]:
    """
    Called by /api/screen/analyze_last. Records the event and, if the vision
    model had a suggestion, asks the proactive engine whether to offer it.
    `frame` identifies the screenshot (e.g. its base64 data); a frame that was
    already processed is skipped. Never raises: screen analysis must keep
    working if this fails.
    """
    global _last_frame_key
    out: Dict[str, Any] = {"proactive_offer": False, "suggestion_id": None}
    try:
        key = hashlib.sha1(
            (frame or f"{description}|{suggestions}").encode("utf-8", "replace")
        ).hexdigest()
        with _frame_lock:
            if key == _last_frame_key:
                return out
            _last_frame_key = key
        screen_timeline.add_event(description or "", suggestions)
        if suggestions and suggestions.strip():
            decision = proactive_engine.maybe_offer("screen", suggestions.strip(), description)
            out["proactive_offer"] = decision["offer"]
            out["suggestion_id"] = decision["suggestion_id"]
    except Exception as e:
        log.warning(f"[V11] screen timeline/proactive hook failed: {e}")
    return out


@router.get("/api/screen/timeline")
def api_screen_timeline(limit: int = 30):
    return {"events": screen_timeline.recent_events(limit)}


@router.post("/api/screen/timeline/summary")
async def api_screen_timeline_summary(limit: int = 30):
    loop = asyncio.get_running_loop()
    # SQLite reads are quick, but keep them off the event loop anyway
    timeline = await loop.run_in_executor(None, screen_timeline.build_timeline_text, limit)
    return {"summary": await screen_timeline.summarize(_async_llm(400), timeline)}


@router.delete("/api/screen/timeline")
def api_screen_timeline_clear():
    screen_timeline.clear()
    return {"ok": True}


# ------------------------------------------------------------
# Documents (Docling)
# ------------------------------------------------------------
@router.post("/api/documents/read")
def api_documents_read(payload: DocumentRead):
    result = document_reader.read_document(payload.source)
    if result.get("ok") and payload.max_chars and payload.max_chars > 0:
        md = result["markdown"]
        result["truncated"] = len(md) > payload.max_chars
        result["markdown"] = md[: payload.max_chars]
    return result


@router.post("/api/documents/ask")
async def api_documents_ask(payload: DocumentAsk):
    if not payload.question.strip():
        raise HTTPException(400, "question is required")
    return await document_reader.ask_document(payload.source, payload.question, _async_llm(700))


# ------------------------------------------------------------
# Setup / lifecycle
# ------------------------------------------------------------
def _start_durable() -> None:
    global _durable_error
    if durable_tasks is None:
        return
    try:
        durable_tasks.configure(
            llm=durable_tasks.llm_from_async(_async_llm(800)),
            db_path=db.DATA_DIR / "sarah_durable_tasks.sqlite",
        )
        durable_tasks.launch()
        _durable_error = None
        print("[V11] Durable tasks running (DBOS). Interrupted tasks will resume.")
    except Exception as e:
        _durable_error = str(e)
        print(f"[V11] Durable tasks disabled: {e}")


def _init_tables() -> None:
    db.DATA_DIR.mkdir(parents=True, exist_ok=True)
    procedure_memory.init_tables()
    proactive_engine.init_tables()
    screen_timeline.init_tables()
    if durable_tasks is not None:
        durable_tasks.init_tables()


def setup(app: FastAPI, get_sarah: Callable[[], Any]) -> None:
    """Register routes + startup/shutdown hooks. Call after the app is created."""
    global _get_sarah
    _get_sarah = get_sarah

    async def _on_startup():
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _init_tables)
        # DBOS must be launched outside the event loop
        await loop.run_in_executor(None, _start_durable)

    async def _on_shutdown():
        if durable_tasks is not None and durable_tasks.is_running():
            await asyncio.get_running_loop().run_in_executor(None, durable_tasks.shutdown)

    # Same mechanism server.py uses (add_event_handler is gone in new FastAPI)
    app.on_event("startup")(_on_startup)
    app.on_event("shutdown")(_on_shutdown)
    # Routes last: if anything above fails, no half-set-up endpoints are exposed
    app.include_router(router)
