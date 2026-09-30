# backend/ai_/tasks_engine.py
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from backend.db import get_connection


@dataclass
class Task:
    id: int
    title: str
    description: str
    status: str
    priority: int
    context_json: Optional[str]
    result_summary: Optional[str]
    created_at: str
    updated_at: str


class TaskEngine:
    """
    Simple SQL-backed task manager for Sarah.
    Lets Sarah create, update, and summarize tasks.
    """

    def create_task(
        self,
        title: str,
        description: str = "",
        priority: int = 0,
        context_json: Optional[str] = None,
    ) -> int:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO tasks (title, description, priority, context_json)
            VALUES (?, ?, ?, ?)
            """,
            (title, description, priority, context_json),
        )
        task_id = cur.lastrowid
        conn.commit()
        conn.close()
        return task_id

    def update_status(self, task_id: int, status: str):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE tasks
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, task_id),
        )
        conn.commit()
        conn.close()

    def set_result(self, task_id: int, result_summary: str):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE tasks
            SET result_summary = ?, status = 'done', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (result_summary, task_id),
        )
        conn.commit()
        conn.close()

    def list_tasks(self, status: Optional[str] = None, limit: int = 50) -> List[Task]:
        conn = get_connection()
        cur = conn.cursor()
        if status:
            cur.execute(
                """
                SELECT * FROM tasks
                WHERE status = ?
                ORDER BY priority DESC, created_at DESC
                LIMIT ?
                """,
                (status, limit),
            )
        else:
            cur.execute(
                """
                SELECT * FROM tasks
                ORDER BY priority DESC, created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        rows = cur.fetchall()
        conn.close()

        tasks: List[Task] = []
        for r in rows:
            tasks.append(
                Task(
                    id=r["id"],
                    title=r["title"],
                    description=r["description"] or "",
                    status=r["status"],
                    priority=r["priority"] or 0,
                    context_json=r["context_json"],
                    result_summary=r["result_summary"],
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                )
            )
        return tasks

    def summarize_tasks_for_prompt(self, limit: int = 10) -> str:
        tasks = self.list_tasks(limit=limit)
        if not tasks:
            return ""
        lines = []
        for t in tasks:
            lines.append(
                f"- [#{t.id}] ({t.status}, prio={t.priority}) {t.title}: {t.description[:80]}"
            )
        return "\n".join(lines)
