# backend/tests/test_memory_store.py
"""
Tests for Memory Store
======================
Tests persistent storage of task state, summaries, and chunks.
"""

import pytest
import tempfile
import sqlite3
from pathlib import Path

# Setup path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from memory.config import MemoryConfig, reset_memory_config
from memory.memory_store import (
    MemoryStore, TaskState, RollingSummary, ChunkSummary, reset_memory_store
)


@pytest.fixture
def temp_db():
    """Create temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    # Initialize base tables needed by memory store
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            role TEXT,
            content TEXT,
            meta_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

    yield db_path

    # Cleanup
    db_path.unlink(missing_ok=True)
    reset_memory_config()
    reset_memory_store()


@pytest.fixture
def store(temp_db):
    """Create MemoryStore with temp database."""
    config = MemoryConfig(debug_memory=False)
    return MemoryStore(db_path=temp_db, config=config)


@pytest.fixture
def conversation_id(temp_db):
    """Create a test conversation."""
    conn = sqlite3.connect(temp_db)
    conn.execute("INSERT INTO conversations (title) VALUES ('Test')")
    conn.commit()
    result = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return result


def add_test_messages(db_path, conversation_id, count=10):
    """Helper to add test messages."""
    conn = sqlite3.connect(db_path)
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (conversation_id, role, f"Message {i}")
        )
    conn.commit()
    conn.close()


class TestTaskState:
    """Test TaskState dataclass."""

    def test_default_values(self):
        """TaskState should have sensible defaults."""
        state = TaskState()
        assert state.goal == ""
        assert state.current_task == ""
        assert state.pending_choices == []
        assert state.turn_count == 0

    def test_has_pending_question(self):
        """has_pending_question should detect pending questions."""
        state = TaskState()
        assert not state.has_pending_question()

        state.pending_question = "What should I do?"
        assert state.has_pending_question()

    def test_has_pending_choices(self):
        """has_pending_choices should detect pending choices."""
        state = TaskState()
        assert not state.has_pending_choices()

        state.pending_choices = ["A", "B"]
        assert state.has_pending_choices()

    def test_to_dict_and_from_dict(self):
        """TaskState should round-trip through dict."""
        original = TaskState(
            goal="Test goal",
            current_task="Current",
            next_step="Next",
            pending_question="Question?",
            pending_choices=["A", "B"],
            turn_count=5,
        )

        data = original.to_dict()
        restored = TaskState.from_dict(data)

        assert restored.goal == original.goal
        assert restored.pending_choices == original.pending_choices
        assert restored.turn_count == original.turn_count


class TestRollingSummary:
    """Test RollingSummary dataclass."""

    def test_to_prompt_block(self):
        """to_prompt_block should format nicely."""
        summary = RollingSummary(
            goal="Build a REST API",
            decisions=["Use FastAPI", "Use PostgreSQL"],
            progress="Created project structure",
            next_step="Implement authentication",
        )

        block = summary.to_prompt_block()

        assert "REST API" in block
        assert "FastAPI" in block
        assert "PostgreSQL" in block
        assert "authentication" in block

    def test_empty_summary_prompt_block(self):
        """Empty summary should return empty string."""
        summary = RollingSummary()
        assert summary.to_prompt_block() == ""


class TestTaskStateStorage:
    """Test task state persistence."""

    def test_get_nonexistent_returns_empty(self, store, conversation_id):
        """Getting nonexistent state should return empty TaskState."""
        state = store.get_task_state(conversation_id)
        assert state.goal == ""
        assert state.turn_count == 0

    def test_save_and_get(self, store, conversation_id):
        """State should persist across save/get."""
        state = TaskState(
            goal="Test goal",
            current_task="Working on tests",
            pending_question="Ready?",
        )
        store.save_task_state(conversation_id, state)

        loaded = store.get_task_state(conversation_id)

        assert loaded.goal == "Test goal"
        assert loaded.current_task == "Working on tests"
        assert loaded.pending_question == "Ready?"

    def test_update_partial_fields(self, store, conversation_id):
        """update_task_state should update only specified fields."""
        store.save_task_state(conversation_id, TaskState(goal="Original goal"))

        store.update_task_state(
            conversation_id,
            current_task="New task",
            increment_turn=True,
        )

        state = store.get_task_state(conversation_id)
        assert state.goal == "Original goal"  # Unchanged
        assert state.current_task == "New task"  # Updated
        assert state.turn_count == 1  # Incremented

    def test_clear_pending(self, store, conversation_id):
        """clear_pending should clear question and choices."""
        store.save_task_state(conversation_id, TaskState(
            pending_question="Question?",
            pending_choices=["A", "B"],
        ))

        store.update_task_state(conversation_id, clear_pending=True)

        state = store.get_task_state(conversation_id)
        assert not state.has_pending_question()
        assert not state.has_pending_choices()

    def test_clear_task_state(self, store, conversation_id):
        """clear_task_state should delete the state."""
        store.save_task_state(conversation_id, TaskState(goal="To be deleted"))
        store.clear_task_state(conversation_id)

        state = store.get_task_state(conversation_id)
        assert state.goal == ""


class TestRollingSummaryStorage:
    """Test rolling summary persistence."""

    def test_get_nonexistent_returns_empty(self, store, conversation_id):
        """Getting nonexistent summary should return empty."""
        summary = store.get_rolling_summary(conversation_id)
        assert summary.goal == ""

    def test_save_and_get(self, store, conversation_id):
        """Summary should persist across save/get."""
        summary = RollingSummary(
            goal="Build API",
            decisions=["Use Python", "Use FastAPI"],
            progress="In progress",
        )
        store.save_rolling_summary(conversation_id, summary)

        loaded = store.get_rolling_summary(conversation_id)

        assert loaded.goal == "Build API"
        assert "Use Python" in loaded.decisions
        assert loaded.progress == "In progress"

    def test_updates_timestamp(self, store, conversation_id):
        """Saving should update the timestamp."""
        summary = RollingSummary(goal="Test")
        store.save_rolling_summary(conversation_id, summary)

        loaded = store.get_rolling_summary(conversation_id)
        assert loaded.updated_at != ""


class TestChunkSummaryStorage:
    """Test chunk summary persistence."""

    def test_add_chunk_returns_id(self, store, conversation_id):
        """add_chunk_summary should return chunk ID."""
        chunk_id = store.add_chunk_summary(
            conversation_id, 1, 20, "- Test summary"
        )
        assert chunk_id > 0

    def test_get_chunks_returns_chronological(self, store, conversation_id):
        """get_chunk_summaries should return in chronological order."""
        store.add_chunk_summary(conversation_id, 1, 20, "First chunk")
        store.add_chunk_summary(conversation_id, 21, 40, "Second chunk")
        store.add_chunk_summary(conversation_id, 41, 60, "Third chunk")

        chunks = store.get_chunk_summaries(conversation_id)

        assert len(chunks) == 3
        assert "First" in chunks[0].summary
        assert "Second" in chunks[1].summary
        assert "Third" in chunks[2].summary

    def test_get_chunks_respects_limit(self, store, conversation_id):
        """get_chunk_summaries should respect limit."""
        for i in range(10):
            store.add_chunk_summary(
                conversation_id, i*20+1, (i+1)*20, f"Chunk {i}"
            )

        chunks = store.get_chunk_summaries(conversation_id, limit=3)

        assert len(chunks) == 3

    def test_get_last_chunk_end_message_id(self, store, conversation_id):
        """get_last_chunk_end_message_id should return correct ID."""
        store.add_chunk_summary(conversation_id, 1, 20, "First")
        store.add_chunk_summary(conversation_id, 21, 45, "Second")

        last_id = store.get_last_chunk_end_message_id(conversation_id)
        assert last_id == 45

    def test_delete_old_chunks(self, store, conversation_id):
        """delete_old_chunks should keep only recent chunks."""
        for i in range(10):
            store.add_chunk_summary(
                conversation_id, i*20+1, (i+1)*20, f"Chunk {i}"
            )

        store.delete_old_chunks(conversation_id, keep_count=3)

        chunks = store.get_chunk_summaries(conversation_id, limit=100)
        assert len(chunks) == 3


class TestMessageHelpers:
    """Test message helper functions."""

    def test_get_message_count(self, store, conversation_id, temp_db):
        """get_message_count should return correct count."""
        add_test_messages(temp_db, conversation_id, count=15)

        count = store.get_message_count(conversation_id)
        assert count == 15

    def test_get_recent_messages(self, store, conversation_id, temp_db):
        """get_recent_messages should return most recent."""
        add_test_messages(temp_db, conversation_id, count=50)

        recent = store.get_recent_messages(conversation_id, limit=10)

        assert len(recent) == 10
        # Should be in chronological order (oldest to newest)
        assert recent[-1]["content"] == "Message 49"

    def test_get_messages_since(self, store, conversation_id, temp_db):
        """get_messages_since should return messages after ID."""
        add_test_messages(temp_db, conversation_id, count=10)

        # Get all messages first to find an ID
        all_msgs = store.get_recent_messages(conversation_id, limit=100)
        mid_id = all_msgs[4]["id"]  # 5th message

        since = store.get_messages_since(conversation_id, mid_id)

        # Should get messages after the 5th
        assert len(since) == 5

    def test_get_unchunked_messages_no_chunks(self, store, conversation_id, temp_db):
        """get_unchunked_messages should return all when no chunks."""
        add_test_messages(temp_db, conversation_id, count=10)

        unchunked = store.get_unchunked_messages(conversation_id)
        assert len(unchunked) == 10

    def test_get_unchunked_messages_with_chunks(self, store, conversation_id, temp_db):
        """get_unchunked_messages should return only new messages."""
        add_test_messages(temp_db, conversation_id, count=30)

        # Get messages and find the 20th
        all_msgs = store.get_recent_messages(conversation_id, limit=100)
        msg_20_id = all_msgs[19]["id"]

        # Add a chunk covering first 20
        store.add_chunk_summary(conversation_id, 1, msg_20_id, "First 20")

        unchunked = store.get_unchunked_messages(conversation_id)
        assert len(unchunked) == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
