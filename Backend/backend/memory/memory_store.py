# backend/memory/memory_store.py
"""
Memory Store
============
Persistent storage for conversation state, summaries, and chunks.

All operations are SILENT - data is for internal use only, never shown to user.

Tables:
- conversation_state: goal, current_task, next_step, pending_question, pending_choices
- rolling_summaries: per-conversation rolling summary
- chunk_summaries: 2-6 bullet summaries every N messages
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict

from .config import MemoryConfig, get_memory_config
import logging

logger = logging.getLogger(__name__)


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class TaskState:
    """
    Tracks the current task context for short-reply reliability.
    """
    goal: str = ""                        # High-level goal
    current_task: str = ""                # What we're doing now
    next_step: str = ""                   # Immediate next action
    pending_question: str = ""            # Question awaiting answer
    pending_choices: List[str] = None     # Options if applicable
    last_assistant_action: str = ""       # What Sarah just did/said
    turn_count: int = 0                   # Messages since last reset

    def __post_init__(self):
        if self.pending_choices is None:
            self.pending_choices = []

    def has_pending_question(self) -> bool:
        return bool(self.pending_question.strip())

    def has_pending_choices(self) -> bool:
        return len(self.pending_choices) > 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskState":
        return cls(
            goal=data.get("goal", ""),
            current_task=data.get("current_task", ""),
            next_step=data.get("next_step", ""),
            pending_question=data.get("pending_question", ""),
            pending_choices=data.get("pending_choices", []),
            last_assistant_action=data.get("last_assistant_action", ""),
            turn_count=data.get("turn_count", 0),
        )


@dataclass
class RollingSummary:
    """
    Rolling summary updated every N turns.
    """
    goal: str = ""                # What we're trying to achieve
    decisions: List[str] = None   # Key decisions made
    progress: str = ""            # What's been accomplished
    constraints: List[str] = None # Constraints/requirements discovered
    next_step: str = ""           # Where we're headed
    updated_at: str = ""          # ISO timestamp of last update
    message_count: int = 0        # Total messages when updated

    def __post_init__(self):
        if self.decisions is None:
            self.decisions = []
        if self.constraints is None:
            self.constraints = []

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RollingSummary":
        return cls(
            goal=data.get("goal", ""),
            decisions=data.get("decisions", []),
            progress=data.get("progress", ""),
            constraints=data.get("constraints", []),
            next_step=data.get("next_step", ""),
            updated_at=data.get("updated_at", ""),
            message_count=data.get("message_count", 0),
        )

    def to_prompt_block(self) -> str:
        """Format for injection into LLM context."""
        lines = []
        if self.goal:
            lines.append(f"Goal: {self.goal}")
        if self.decisions:
            lines.append("Decisions: " + "; ".join(self.decisions))
        if self.progress:
            lines.append(f"Progress: {self.progress}")
        if self.constraints:
            lines.append("Constraints: " + "; ".join(self.constraints))
        if self.next_step:
            lines.append(f"Next: {self.next_step}")
        return "\n".join(lines)


@dataclass
class ChunkSummary:
    """
    Summary of a chunk of N messages.
    """
    id: int = 0
    conversation_id: int = 0
    start_message_id: int = 0
    end_message_id: int = 0
    summary: str = ""              # 2-6 bullet points
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# MEMORY STORE
# ============================================================

class MemoryStore:
    """
    Persistent storage for conversation memory components.
    """

    def __init__(self, db_path: Optional[Path] = None, config: Optional[MemoryConfig] = None):
        self.config = config or get_memory_config()

        if db_path is None:
            # Use same database as main app
            from backend.db import DB_PATH
            db_path = DB_PATH

        self.db_path = db_path
        self._ensure_tables()

        if self.config.debug_memory:
            logger.info(f"[MemoryStore] Initialized with db={self.db_path}")

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _ensure_tables(self):
        """Create memory-specific tables if they don't exist."""
        conn = self._get_conn()
        cur = conn.cursor()

        # Table: conversation_state
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversation_state (
                conversation_id INTEGER PRIMARY KEY,
                state_json TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
        """)

        # Table: rolling_summaries
        cur.execute("""
            CREATE TABLE IF NOT EXISTS rolling_summaries (
                conversation_id INTEGER PRIMARY KEY,
                summary_json TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
        """)

        # Table: chunk_summaries
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chunk_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                start_message_id INTEGER NOT NULL,
                end_message_id INTEGER NOT NULL,
                summary TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
        """)

        # Index for faster chunk lookups
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_chunk_summaries_conv
            ON chunk_summaries(conversation_id, created_at DESC);
        """)

        conn.commit()
        conn.close()

    # ============================================================
    # TASK STATE OPERATIONS
    # ============================================================

    def get_task_state(self, conversation_id: int) -> TaskState:
        """Get task state for a conversation, or return empty state."""
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT state_json FROM conversation_state WHERE conversation_id = ?",
            (conversation_id,)
        )
        row = cur.fetchone()
        conn.close()

        if row and row["state_json"]:
            try:
                data = json.loads(row["state_json"])
                return TaskState.from_dict(data)
            except json.JSONDecodeError:
                pass

        return TaskState()

    def save_task_state(self, conversation_id: int, state: TaskState):
        """Save or update task state for a conversation."""
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO conversation_state (conversation_id, state_json, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(conversation_id) DO UPDATE SET
                state_json = excluded.state_json,
                updated_at = CURRENT_TIMESTAMP;
        """, (conversation_id, json.dumps(state.to_dict())))
        conn.commit()
        conn.close()

        if self.config.debug_memory:
            logger.info(f"[MemoryStore] Saved task state for conv={conversation_id}")

    def update_task_state(
        self,
        conversation_id: int,
        goal: Optional[str] = None,
        current_task: Optional[str] = None,
        next_step: Optional[str] = None,
        pending_question: Optional[str] = None,
        pending_choices: Optional[List[str]] = None,
        last_assistant_action: Optional[str] = None,
        increment_turn: bool = False,
        clear_pending: bool = False,
    ) -> TaskState:
        """
        Update specific fields of task state.
        Returns the updated state.
        """
        state = self.get_task_state(conversation_id)

        if goal is not None:
            state.goal = goal
        if current_task is not None:
            state.current_task = current_task
        if next_step is not None:
            state.next_step = next_step
        if pending_question is not None:
            state.pending_question = pending_question
        if pending_choices is not None:
            state.pending_choices = pending_choices
        if last_assistant_action is not None:
            state.last_assistant_action = last_assistant_action
        if increment_turn:
            state.turn_count += 1
        if clear_pending:
            state.pending_question = ""
            state.pending_choices = []

        self.save_task_state(conversation_id, state)
        return state

    def clear_task_state(self, conversation_id: int):
        """Clear task state for a conversation."""
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM conversation_state WHERE conversation_id = ?",
            (conversation_id,)
        )
        conn.commit()
        conn.close()

    # ============================================================
    # ROLLING SUMMARY OPERATIONS
    # ============================================================

    def get_rolling_summary(self, conversation_id: int) -> RollingSummary:
        """Get rolling summary for a conversation, or return empty."""
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT summary_json FROM rolling_summaries WHERE conversation_id = ?",
            (conversation_id,)
        )
        row = cur.fetchone()
        conn.close()

        if row and row["summary_json"]:
            try:
                data = json.loads(row["summary_json"])
                return RollingSummary.from_dict(data)
            except json.JSONDecodeError:
                pass

        return RollingSummary()

    def save_rolling_summary(self, conversation_id: int, summary: RollingSummary):
        """Save or update rolling summary for a conversation."""
        summary.updated_at = datetime.utcnow().isoformat()

        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO rolling_summaries (conversation_id, summary_json, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(conversation_id) DO UPDATE SET
                summary_json = excluded.summary_json,
                updated_at = CURRENT_TIMESTAMP;
        """, (conversation_id, json.dumps(summary.to_dict())))
        conn.commit()
        conn.close()

        if self.config.debug_memory:
            logger.info(f"[MemoryStore] Saved rolling summary for conv={conversation_id}")

    # ============================================================
    # CHUNK SUMMARY OPERATIONS
    # ============================================================

    def add_chunk_summary(
        self,
        conversation_id: int,
        start_message_id: int,
        end_message_id: int,
        summary: str,
    ) -> int:
        """Add a new chunk summary. Returns the chunk ID."""
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO chunk_summaries
            (conversation_id, start_message_id, end_message_id, summary, created_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (conversation_id, start_message_id, end_message_id, summary))
        chunk_id = cur.lastrowid
        conn.commit()
        conn.close()

        if self.config.debug_memory:
            logger.info(f"[MemoryStore] Added chunk {chunk_id} for conv={conversation_id}")

        return chunk_id

    def get_chunk_summaries(
        self,
        conversation_id: int,
        limit: Optional[int] = None,
    ) -> List[ChunkSummary]:
        """
        Get chunk summaries for a conversation in chronological order.
        If limit is None, uses config.max_chunks_in_context.
        """
        if limit is None:
            limit = self.config.max_chunks_in_context

        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, conversation_id, start_message_id, end_message_id, summary, created_at
            FROM chunk_summaries
            WHERE conversation_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, (conversation_id, limit))
        rows = cur.fetchall()
        conn.close()

        chunks = []
        for row in rows:
            chunks.append(ChunkSummary(
                id=row["id"],
                conversation_id=row["conversation_id"],
                start_message_id=row["start_message_id"],
                end_message_id=row["end_message_id"],
                summary=row["summary"],
                created_at=row["created_at"],
            ))

        # Reverse so oldest is first (chronological order)
        return list(reversed(chunks))

    def get_last_chunk_end_message_id(self, conversation_id: int) -> Optional[int]:
        """Get the end_message_id of the most recent chunk."""
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT end_message_id
            FROM chunk_summaries
            WHERE conversation_id = ?
            ORDER BY id DESC
            LIMIT 1
        """, (conversation_id,))
        row = cur.fetchone()
        conn.close()

        return row["end_message_id"] if row else None

    def delete_old_chunks(self, conversation_id: int, keep_count: int):
        """Delete oldest chunks, keeping only the most recent N."""
        conn = self._get_conn()
        cur = conn.cursor()

        # Get IDs to keep
        cur.execute("""
            SELECT id FROM chunk_summaries
            WHERE conversation_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, (conversation_id, keep_count))
        keep_ids = [row["id"] for row in cur.fetchall()]

        if keep_ids:
            placeholders = ",".join("?" * len(keep_ids))
            cur.execute(f"""
                DELETE FROM chunk_summaries
                WHERE conversation_id = ? AND id NOT IN ({placeholders})
            """, [conversation_id] + keep_ids)
        else:
            # If no chunks to keep, delete all
            cur.execute(
                "DELETE FROM chunk_summaries WHERE conversation_id = ?",
                (conversation_id,)
            )

        conn.commit()
        conn.close()

    # ============================================================
    # MESSAGE HELPERS
    # ============================================================

    def get_message_count(self, conversation_id: int) -> int:
        """Get total message count for a conversation."""
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE conversation_id = ?",
            (conversation_id,)
        )
        row = cur.fetchone()
        conn.close()
        return row["cnt"] if row else 0

    def get_messages_since(
        self,
        conversation_id: int,
        since_message_id: int,
    ) -> List[Dict[str, Any]]:
        """Get messages after a specific message ID."""
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, role, content, meta_json, created_at
            FROM messages
            WHERE conversation_id = ? AND id > ?
            ORDER BY id ASC
        """, (conversation_id, since_message_id))
        rows = cur.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_recent_messages(
        self,
        conversation_id: int,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get the most recent N messages for a conversation.
        If limit is None, uses config.recent_window_messages.
        """
        if limit is None:
            limit = self.config.recent_window_messages

        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, role, content, meta_json, created_at
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, (conversation_id, limit))
        rows = cur.fetchall()
        conn.close()

        # Reverse to chronological order
        messages = [dict(row) for row in rows]
        return list(reversed(messages))

    def get_unchunked_messages(self, conversation_id: int) -> List[Dict[str, Any]]:
        """
        Get messages that haven't been summarized into a chunk yet.
        """
        last_chunk_end = self.get_last_chunk_end_message_id(conversation_id)

        if last_chunk_end is None:
            # No chunks yet, get all messages
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                SELECT id, role, content, meta_json, created_at
                FROM messages
                WHERE conversation_id = ?
                ORDER BY id ASC
            """, (conversation_id,))
            rows = cur.fetchall()
            conn.close()
            return [dict(row) for row in rows]
        else:
            return self.get_messages_since(conversation_id, last_chunk_end)


# ============================================================
# SINGLETON
# ============================================================

_memory_store: Optional[MemoryStore] = None


def get_memory_store(db_path: Optional[Path] = None) -> MemoryStore:
    """Get or create global MemoryStore instance."""
    global _memory_store
    if _memory_store is None:
        _memory_store = MemoryStore(db_path=db_path)
    return _memory_store


def reset_memory_store():
    """Reset store singleton (for testing)."""
    global _memory_store
    _memory_store = None
