# backend/tests/test_context_builder.py
"""
Tests for Context Builder
=========================
Tests context building, token management, and message trimming.
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
from memory.intent_resolver import IntentResolver
from memory.context_builder import ContextBuilder, LLMContextPacket


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
def config():
    """Create test configuration."""
    return MemoryConfig(
        recent_window_messages=80,
        min_recent_messages=10,
        total_token_budget=6000,
        system_prompt_tokens=500,
        # Keep the completion reserve proportionate to the small test budget.
        # The production default (sourced from settings) is ~4096, which would
        # consume two-thirds of a 6000-token budget and starve message history.
        llm_max_completion_tokens=1000,
        chars_per_token=3.5,
        debug_memory=False,
    )


@pytest.fixture
def store(temp_db, config):
    """Create MemoryStore with temp database."""
    return MemoryStore(db_path=temp_db, config=config)


@pytest.fixture
def builder(store, config):
    """Create ContextBuilder with store."""
    return ContextBuilder(store=store, config=config)


@pytest.fixture
def conversation_id(temp_db):
    """Create a test conversation."""
    conn = sqlite3.connect(temp_db)
    conn.execute("INSERT INTO conversations (title) VALUES ('Test')")
    conn.commit()
    result = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return result


def add_messages(db_path, conversation_id, messages):
    """Helper to add test messages."""
    conn = sqlite3.connect(db_path)
    for role, content in messages:
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (conversation_id, role, content)
        )
    conn.commit()
    conn.close()


class TestBasicContextBuilding:
    """Test basic context building functionality."""

    def test_builds_with_system_prompt(self, builder, conversation_id):
        """Context should always include system prompt with identity wiring.

        Post-B5: persona content (Sarah's voice/identity) is sourced from
        OpenClaw IDENTITY.md/SOUL.md at request time. With no persona file
        present in the test environment, the persona block is empty but
        the creator-note line + length guidelines still anchor the prompt.
        """
        packet = builder.build(conversation_id, "Hello")

        assert len(packet.messages) >= 2  # system + user
        assert packet.messages[0]["role"] == "system"
        content = packet.messages[0]["content"]
        # The creator-note line is template-static and load-bearing for identity.
        assert "Creator note" in content
        assert "Response Length Guidelines" in content

    def test_includes_user_message(self, builder, conversation_id):
        """Context should include user message."""
        packet = builder.build(conversation_id, "Hello, how are you?")

        # Last message should be from user
        assert packet.messages[-1]["role"] == "user"
        assert "Hello" in packet.messages[-1]["content"]

    def test_readonly_context_window_does_not_append_user_turn(self, builder, conversation_id, temp_db):
        """Avatar context snapshots should reuse the context window without fake turns."""
        add_messages(temp_db, conversation_id, [
            ("user", "I love this."),
            ("assistant", "That is great."),
        ])

        packet = builder.build(
            conversation_id,
            "",
            process_mood=False,
            append_user_message=False,
        )

        assert packet.debug_info["readonly_context_window"] is True
        assert packet.messages[-1]["role"] == "assistant"
        assert packet.messages[-1]["content"] == "That is great."
        assert not (
            packet.messages[-1]["role"] == "user"
            and packet.messages[-1]["content"] == ""
        )

    def test_includes_recent_messages(self, builder, conversation_id, temp_db):
        """Context should include recent conversation history."""
        add_messages(temp_db, conversation_id, [
            ("user", "What is Python?"),
            ("assistant", "Python is a programming language."),
        ])

        packet = builder.build(conversation_id, "Tell me more")

        # Should have: system, user (Python), assistant, user (new)
        assert len(packet.messages) >= 4

        # Check history is included
        content_str = str(packet.messages)
        assert "Python" in content_str

    def test_returns_token_estimate(self, builder, conversation_id):
        """Packet should include token estimate."""
        packet = builder.build(conversation_id, "Test message")

        assert packet.estimated_tokens > 0
        assert isinstance(packet.estimated_tokens, int)


class TestInternalContextInjection:
    """Test internal context (summaries, state) injection."""

    def test_includes_rolling_summary(self, builder, store, conversation_id):
        """Rolling summary should be injected into context."""
        # Add a rolling summary
        summary = RollingSummary(
            goal="Build a web application",
            decisions=["Use React", "Use TypeScript"],
            progress="Created project structure",
            next_step="Implement authentication",
        )
        store.save_rolling_summary(conversation_id, summary)

        packet = builder.build(conversation_id, "Continue")

        # Check summary is in system message
        system_content = packet.messages[0]["content"]
        assert "web application" in system_content.lower()

        # Check debug info
        assert "rolling_summary" in packet.debug_info

    def test_includes_task_state(self, builder, store, conversation_id):
        """Task state should be injected into context."""
        store.update_task_state(
            conversation_id,
            goal="Implement login feature",
            current_task="Writing auth middleware",
            pending_question="Should we use sessions or JWT?",
        )

        packet = builder.build(conversation_id, "JWT please")

        system_content = packet.messages[0]["content"]
        # Internal context should be included but marked as internal
        assert "INTERNAL" in system_content or "login" in system_content.lower()

    def test_includes_chunk_summaries(self, builder, store, conversation_id):
        """Chunk summaries should be injected into context."""
        # Add chunk summaries
        store.add_chunk_summary(
            conversation_id, 1, 20,
            "- Discussed project requirements\n- Chose tech stack"
        )
        store.add_chunk_summary(
            conversation_id, 21, 40,
            "- Implemented user model\n- Added database migrations"
        )

        packet = builder.build(conversation_id, "What's next?")

        # Check debug info includes chunk count
        assert packet.debug_info.get("chunk_count", 0) >= 1

    def test_includes_vision_observation(self, builder, conversation_id):
        """Vision observation should be injected when provided."""
        vision_obs = "The screenshot shows a login form with email and password fields."

        packet = builder.build(
            conversation_id,
            "What do you see?",
            vision_observation=vision_obs,
        )

        system_content = packet.messages[0]["content"]
        assert "login form" in system_content.lower()
        assert packet.debug_info.get("has_vision", False)


class TestTokenBudgetManagement:
    """Test token budget enforcement and trimming."""

    def test_estimates_tokens_reasonably(self, builder):
        """Token estimation should be reasonable."""
        # ~3.5 chars per token
        short_text = "Hello"
        long_text = "A" * 350  # Should be ~100 tokens

        short_tokens = builder._estimate_tokens(short_text)
        long_tokens = builder._estimate_tokens(long_text)

        assert short_tokens < 10
        assert 80 < long_tokens < 120

    def test_trims_messages_when_over_budget(self, builder, conversation_id, temp_db):
        """Messages should be trimmed when exceeding token budget."""
        # Add many messages to exceed budget
        messages = []
        for i in range(100):
            messages.append(("user", f"Message number {i}: " + "x" * 200))
            messages.append(("assistant", f"Response to message {i}: " + "y" * 200))
        add_messages(temp_db, conversation_id, messages)

        packet = builder.build(conversation_id, "Final message")

        # Should have trimmed to fit budget
        assert packet.estimated_tokens <= builder.config.total_token_budget
        assert packet.debug_info.get("trimmed_messages_count", 0) < 200

    def test_keeps_minimum_messages(self, builder, conversation_id, temp_db, config):
        """Should keep at least minimum messages even if trimming."""
        # Add many messages
        messages = []
        for i in range(50):
            messages.append(("user", f"Message {i}: " + "x" * 500))
            messages.append(("assistant", f"Response {i}: " + "y" * 500))
        add_messages(temp_db, conversation_id, messages)

        packet = builder.build(conversation_id, "Test")

        # Should keep at least min_recent_messages
        assert packet.debug_info.get("trimmed_messages_count", 0) >= config.min_recent_messages

    def test_overlarge_system_context_warns_and_emergency_trims(self, temp_db, conversation_id):
        """Persona/skills/internal blocks can exceed the history budget.

        In that case the builder should not silently preserve the minimum
        recent-message floor and blow the budget without diagnostics.
        """
        tiny_config = MemoryConfig(
            recent_window_messages=40,
            min_recent_messages=10,
            total_token_budget=500,
            llm_max_completion_tokens=100,
            chars_per_token=1.0,
            debug_memory=False,
        )
        store = MemoryStore(db_path=temp_db, config=tiny_config)
        builder = ContextBuilder(store=store, config=tiny_config)

        add_messages(temp_db, conversation_id, [
            ("user" if i % 2 == 0 else "assistant", f"History {i}: " + "x" * 120)
            for i in range(20)
        ])

        packet = builder.build(
            conversation_id,
            "keep going",
            include_internal_context=False,
        )

        warnings = packet.debug_info.get("budget_warnings", [])
        assert "system_context_exceeds_message_budget" in warnings
        assert "recent_messages_trimmed_below_minimum" in warnings
        assert packet.debug_info["trimmed_messages_count"] < tiny_config.min_recent_messages
        assert packet.debug_info["recent_message_budget"] < tiny_config.min_recent_messages

    def test_local_mode_token_budget_expands_recent_history(self, temp_db, conversation_id):
        """Local LLM mode should use its advertised larger context budget."""
        tiny_config = MemoryConfig(
            recent_window_messages=40,
            min_recent_messages=5,
            total_token_budget=1200,
            llm_max_completion_tokens=100,
            chars_per_token=1.0,
            debug_memory=False,
        )
        store = MemoryStore(db_path=temp_db, config=tiny_config)
        builder = ContextBuilder(store=store, config=tiny_config)

        add_messages(temp_db, conversation_id, [
            ("user" if i % 2 == 0 else "assistant", f"History {i}: " + "x" * 180)
            for i in range(30)
        ])

        online_packet = builder.build(
            conversation_id,
            "Final",
            include_internal_context=False,
        )
        local_packet = builder.build(
            conversation_id,
            "Final",
            include_internal_context=False,
            llm_mode_info={
                "mode": "local",
                "model_name": "llama3.1",
                "provider": "Ollama",
                "token_budget": 32000,
            },
        )

        assert online_packet.debug_info["effective_total_token_budget"] == tiny_config.total_token_budget
        assert local_packet.debug_info["configured_total_token_budget"] == tiny_config.total_token_budget
        assert local_packet.debug_info["effective_total_token_budget"] == 32000
        assert local_packet.debug_info["message_budget"] > online_packet.debug_info["message_budget"]
        assert local_packet.debug_info["trimmed_messages_count"] == 30
        assert local_packet.debug_info["trimmed_messages_count"] > online_packet.debug_info["trimmed_messages_count"]
        assert local_packet.debug_info["fits_total_budget"] is True
        assert "packet_exceeds_total_budget" not in local_packet.debug_info.get("budget_warnings", [])

    def test_remaining_budget_calculation(self, builder, store, conversation_id, temp_db):
        """Should accurately calculate remaining token budget."""
        add_messages(temp_db, conversation_id, [
            ("user", "Hello"),
            ("assistant", "Hi there!"),
        ])

        remaining = builder.get_remaining_token_budget(conversation_id)

        # Should have most of budget remaining for small conversation
        assert remaining > builder.config.total_token_budget / 2


class TestIntentResolution:
    """Test integration with intent resolver."""

    def test_resolves_confirmation(self, builder, store, conversation_id):
        """Confirmation should be resolved based on pending state."""
        store.update_task_state(
            conversation_id,
            pending_question="Should I deploy to production?",
        )

        packet = builder.build(conversation_id, "yes")

        # User message should be expanded
        user_msg = packet.messages[-1]["content"]
        assert "deploy" in user_msg.lower() or "production" in user_msg.lower()

        # Debug info should show intent
        assert packet.debug_info.get("intent_type") == "confirmation"

    def test_resolves_selection(self, builder, store, conversation_id):
        """Selection should pick from pending choices."""
        store.update_task_state(
            conversation_id,
            pending_choices=["Option A", "Option B", "Option C"],
        )

        packet = builder.build(conversation_id, "2")

        # Should have selected Option B
        user_msg = packet.messages[-1]["content"]
        assert "Option B" in user_msg

    def test_normal_message_unchanged(self, builder, conversation_id):
        """Long messages should pass through unchanged."""
        original = "Please help me implement a comprehensive error handling system"

        packet = builder.build(conversation_id, original)

        # Message should be preserved
        user_msg = packet.messages[-1]["content"]
        assert original in user_msg


class TestDebugInfo:
    """Test debug information in context packets."""

    def test_includes_all_debug_fields(self, builder, store, conversation_id, temp_db):
        """Debug info should include all relevant fields."""
        add_messages(temp_db, conversation_id, [
            ("user", "Hello"),
            ("assistant", "Hi!"),
        ])
        store.update_task_state(conversation_id, goal="Test goal")
        store.save_rolling_summary(conversation_id, RollingSummary(goal="Summary goal"))

        packet = builder.build(conversation_id, "Test")

        assert "system_tokens" in packet.debug_info
        assert "message_budget" in packet.debug_info
        assert "recent_messages_count" in packet.debug_info
        assert "intent_type" in packet.debug_info
        assert "total_estimated_tokens" in packet.debug_info


class TestSimpleBuild:
    """Test simple build interface."""

    def test_build_simple_returns_messages_only(self, builder, conversation_id):
        """build_simple should return just the messages list."""
        messages = builder.build_simple(conversation_id, "Hello")

        assert isinstance(messages, list)
        assert all("role" in m and "content" in m for m in messages)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
