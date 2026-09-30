# backend/ai_/reflection_engine.py
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from backend.db import get_connection


@dataclass
class Reflection:
    id: int
    created_at: str
    problem: str
    improvement: str
    new_rule: str
    meta_json: Optional[str]


class ReflectionEngine:
    """
    Sarah's self-improvement module.
    After each interaction, we can call `generate_and_store_reflection(...)`.
    At prompt-building time, we can inject recent reflections.
    """

    def __init__(self, llm_call_fn):
        self.llm_call_fn = llm_call_fn

    def _insert_reflection(
        self,
        conversation_id: Optional[int],
        message_id: Optional[int],
        problem: str,
        improvement: str,
        new_rule: str,
        meta_json: Optional[str] = None,
    ):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO reflections (
                conversation_id, message_id, problem, improvement, new_rule, meta_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (conversation_id, message_id, problem, improvement, new_rule, meta_json),
        )
        conn.commit()
        conn.close()

    def get_recent_reflections(self, limit: int = 10) -> List[Reflection]:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, created_at, problem, improvement, new_rule, meta_json
            FROM reflections
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        conn.close()
        result: List[Reflection] = []
        for r in rows:
            result.append(
                Reflection(
                    id=r["id"],
                    created_at=r["created_at"],
                    problem=r["problem"] or "",
                    improvement=r["improvement"] or "",
                    new_rule=r["new_rule"] or "",
                    meta_json=r["meta_json"],
                )
            )
        return result

    async def generate_and_store_reflection(
        self,
        user_message: str,
        assistant_reply: str,
        conversation_id: Optional[int] = None,
        message_id: Optional[int] = None,
    ):
        """
        Call after sending a reply.
        This asks the LLM "how could I do better next time?",
        then persists that as a reflection.
        """
        prompt = f"""
You are Sarah AI's internal self-improvement process.

The Creator asked:
\"\"\"{user_message}\"\"\"

You replied:
\"\"\"{assistant_reply}\"\"\"

1. Briefly state if there was any problem or weakness in the reply.
2. Suggest exactly ONE concrete improvement.
3. Suggest exactly ONE new, short rule Sarah should adopt in the future.

Reply in strict JSON with keys:
- problem
- improvement
- new_rule
"""
        import json

        raw = await self.llm_call_fn(prompt, max_tokens=220)
        try:
            data = json.loads(raw)
        except Exception:
            # If model returns junk, ignore
            return

        problem = (data.get("problem") or "").strip()
        improvement = (data.get("improvement") or "").strip()
        new_rule = (data.get("new_rule") or "").strip()

        if not (problem or improvement or new_rule):
            return

        self._insert_reflection(
            conversation_id=conversation_id,
            message_id=message_id,
            problem=problem,
            improvement=improvement,
            new_rule=new_rule,
        )

    def build_reflection_summary(self, limit: int = 5) -> str:
        """
        Returns a short textual summary of recent reflections
        to inject into the system prompt.
        """
        reflections = self.get_recent_reflections(limit=limit)
        if not reflections:
            return ""

        lines = []
        for r in reflections:
            if r.new_rule:
                lines.append(f"- Rule: {r.new_rule}")
            elif r.improvement:
                lines.append(f"- Lesson: {r.improvement}")

        return "\n".join(lines[:limit])
