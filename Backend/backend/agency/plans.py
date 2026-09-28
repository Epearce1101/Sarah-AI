"""Sarah's plans: multi-step tasks carried out step by step.

For anything that takes several actions she first writes a plan (goal +
steps) with the ``make_plan`` tool, then works through it: act, check the
result, ``update_plan`` (done / failed / skipped, add steps if something
needs a detour). The tool loop (``openrouter_client.chat_stream``) is the
executor: while a plan is open it gives her more steps, reminds her of the
next one after every action, and if the turn runs out before the plan is
finished the plan stays open and her mind loop picks it up again shortly,
in the background.

A plan can also be marked ``blocked`` (needs Zero: a password, a choice),
which parks it until Zero answers.
"""
from __future__ import annotations

import json
import threading
import time
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Dict, List, Optional

from .guard import WORKSPACE

_FILE = WORKSPACE / "plans.json"
_lock = threading.Lock()
STEP_STATUSES = ("done", "failed", "skipped")
PLAN_STATUSES = ("active", "done", "blocked", "cancelled")
MAX_STEPS = 15


def _load() -> List[Dict[str, Any]]:
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(plans: List[Dict[str, Any]]) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(plans[-40:], indent=1), encoding="utf-8")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _clean_steps(steps: Any) -> List[str]:
    if isinstance(steps, str):
        steps = [s for s in steps.splitlines()]
    out = []
    for s in steps or []:
        s = str(s).strip().lstrip("-*0123456789.) ").strip()
        if s:
            out.append(s[:300])
    return out


def create(goal: str, steps: Any, conversation_id: Optional[int] = None) -> Dict[str, Any]:
    steps = _clean_steps(steps)[:MAX_STEPS]
    if not goal.strip() or not steps:
        raise ValueError("a plan needs a goal and at least one step")
    with _lock:
        plans = _load()
        # A new plan for the same goal replaces the old open one.
        for p in plans:
            if p["status"] == "active" and p["goal"].strip().lower() == goal.strip().lower():
                p["status"] = "cancelled"
        plan = {"id": max([p["id"] for p in plans] or [0]) + 1, "goal": goal.strip()[:300],
                "steps": [{"text": s, "status": "pending", "note": ""} for s in steps],
                "status": "active", "conversation_id": conversation_id, "created": _now(), "updated": _now(),
                "touched": time.time(), "attempts": 0}
        plans.append(plan)
        _save(plans)
        return plan


def get(plan_id: int) -> Optional[Dict[str, Any]]:
    with _lock:
        return next((p for p in _load() if p["id"] == int(plan_id)), None)


def open_plans() -> List[Dict[str, Any]]:
    with _lock:
        return [p for p in _load() if p["status"] in ("active", "blocked")]


def latest_active() -> Optional[Dict[str, Any]]:
    active = [p for p in open_plans() if p["status"] == "active"]
    return max(active, key=lambda p: p.get("touched", 0)) if active else None


def last_touched() -> Optional[Dict[str, Any]]:
    with _lock:
        plans = _load()
    return max(plans, key=lambda p: p.get("touched", 0)) if plans else None


def touched_since(t: float) -> Optional[Dict[str, Any]]:
    """The active plan worked on since `t` (this turn), or the one this turn
    was started to continue (``focus``), if any."""
    p = latest_active()
    if p and p.get("touched", 0) >= t:
        return p
    pid = focus.get()
    if pid is not None:
        p = get(pid)
        return p if p and p["status"] == "active" else None
    return None


# Set by her mind loop around a moment spent continuing a plan.
focus: ContextVar[Optional[int]] = ContextVar("sarah_plan_focus", default=None)


def next_step(plan: Dict[str, Any]) -> Optional[int]:
    for i, s in enumerate(plan["steps"]):
        if s["status"] == "pending":
            return i
    return None


def update(plan_id: Optional[int], step: Optional[int] = None, status: Optional[str] = None, note: str = "",
           add_steps: Any = None, plan_status: Optional[str] = None) -> Dict[str, Any]:
    """Mark a step (1-based), add steps, or set the whole plan's status."""
    with _lock:
        plans = _load()
        if plan_id is None:
            active = [p for p in plans if p["status"] == "active"]
            if not active:
                raise ValueError("no active plan; make one with make_plan")
            plan = max(active, key=lambda p: p.get("touched", 0))
        else:
            plan = next((p for p in plans if p["id"] == int(plan_id)), None)
            if plan is None:
                raise ValueError(f"no plan #{plan_id}")
        if step is not None:
            if not 1 <= int(step) <= len(plan["steps"]):
                raise ValueError(f"plan #{plan['id']} has steps 1-{len(plan['steps'])}")
            if status not in STEP_STATUSES:
                raise ValueError(f"step status must be one of {', '.join(STEP_STATUSES)}")
            if status == "done" and len((note or "").strip()) < 4:
                raise ValueError("say in note what you checked that shows this step worked (e.g. 'page shows "
                                 "search results for lofi'); if you didn't check, check first")
            s = plan["steps"][int(step) - 1]
            s["status"], s["note"] = status, (note or "")[:300]
            plan["last"] = status
        extra = _clean_steps(add_steps)
        if extra:
            room = MAX_STEPS - len(plan["steps"])
            # New steps go right after the last finished one (a detour), in order.
            at = next_step(plan)
            at = len(plan["steps"]) if at is None else at
            for j, text in enumerate(extra[:max(0, room)]):
                plan["steps"].insert(at + j, {"text": text, "status": "pending", "note": ""})
        if plan_status:
            if plan_status not in PLAN_STATUSES:
                raise ValueError(f"plan status must be one of {', '.join(PLAN_STATUSES)}")
            plan["status"] = plan_status
            if note and step is None:
                plan["note"] = note[:300]
        elif plan["status"] in ("active", "blocked") and next_step(plan) is None:
            plan["status"] = "done"
        elif plan["status"] == "blocked" and (step is not None or extra):
            plan["status"] = "active"   # working on it again
        plan["updated"], plan["touched"] = _now(), time.time()
        _save(plans)
        return plan


def note_attempt(plan_id: int) -> None:
    """Her mind loop picked the plan up again (to stop endless retries)."""
    with _lock:
        plans = _load()
        for p in plans:
            if p["id"] == plan_id:
                p["attempts"] = int(p.get("attempts", 0)) + 1
                p["touched"] = time.time()
        _save(plans)


def status_line(plan: Dict[str, Any]) -> str:
    done = sum(1 for s in plan["steps"] if s["status"] != "pending")
    head = f"Plan #{plan['id']} \"{plan['goal']}\": {done}/{len(plan['steps'])} steps"
    if plan["status"] == "done":
        return head + " - all finished. Check the goal is really met, then tell Zero the result."
    if plan["status"] in ("blocked", "cancelled"):
        return head + f" - {plan['status']}."
    i = next_step(plan)
    tail = f" Next: step {i + 1}: {plan['steps'][i]['text']}" if i is not None else ""
    if plan.get("last") == "failed":
        tail += " (a step just failed: find another way and add it with add_steps, or move on)"
    return head + "." + tail


def render(plan: Dict[str, Any]) -> str:
    marks = {"pending": "[ ]", "done": "[x]", "failed": "[!]", "skipped": "[-]"}
    lines = [status_line(plan)]
    for i, s in enumerate(plan["steps"], 1):
        lines.append(f"  {marks[s['status']]} {i}. {s['text']}" + (f" ({s['note']})" if s["note"] else ""))
    return "\n".join(lines)


def render_open() -> str:
    """Her open plans, for the 'Right now' block."""
    plans = open_plans()
    if not plans:
        return ""
    lines = ["- Your open plans (continue with update_plan; blocked ones wait for Zero):"]
    for p in plans[-4:]:
        lines.append("  " + status_line(p))
    return "\n".join(lines)


def snapshot(plan: Dict[str, Any]) -> Dict[str, Any]:
    """What the UI shows (a checklist)."""
    return {"id": plan["id"], "goal": plan["goal"], "status": plan["status"],
            "steps": [{"text": s["text"], "status": s["status"]} for s in plan["steps"]]}
