# backend/tests/test_intent_resolver.py
"""
Tests for Intent Resolver
=========================
Tests short reply understanding and intent resolution.
"""

import pytest
import tempfile
import sqlite3
from pathlib import Path

# Setup path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from memory.config import MemoryConfig, reset_memory_config
from memory.memory_store import MemoryStore, TaskState, reset_memory_store
from memory.intent_resolver import IntentResolver, IntentType, ResolvedIntent


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
def resolver(store):
    """Create IntentResolver with store."""
    config = MemoryConfig(debug_memory=False)
    return IntentResolver(store=store, config=config)


@pytest.fixture
def conversation_id(temp_db):
    """Create a test conversation."""
    conn = sqlite3.connect(temp_db)
    conn.execute("INSERT INTO conversations (title) VALUES ('Test')")
    conn.commit()
    result = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return result


class TestShortReplyDetection:
    """Test that short replies are correctly identified."""

    def test_short_reply_under_limit(self, resolver):
        """Messages under word limit should be short replies."""
        assert resolver._is_short_reply("yes")
        assert resolver._is_short_reply("option 1")
        assert resolver._is_short_reply("sounds good")
        assert resolver._is_short_reply("the first one please")

    def test_long_reply_over_limit(self, resolver):
        """Messages over word limit should not be short replies."""
        long_msg = "This is a much longer message that contains many words and should not be considered a short reply"
        assert not resolver._is_short_reply(long_msg)


class TestConfirmationDetection:
    """Test confirmation keyword detection."""

    def test_basic_confirmations(self, resolver):
        """Basic confirmation words should be detected."""
        assert resolver._is_confirmation("yes")
        assert resolver._is_confirmation("Yeah")
        assert resolver._is_confirmation("sure")
        assert resolver._is_confirmation("ok")
        assert resolver._is_confirmation("okay")
        assert resolver._is_confirmation("go ahead")

    def test_confirmation_with_punctuation(self, resolver):
        """Confirmations with punctuation should work."""
        assert resolver._is_confirmation("yes!")
        assert resolver._is_confirmation("sure.")
        assert resolver._is_confirmation("okay?")

    def test_non_confirmations(self, resolver):
        """Non-confirmation words should not match."""
        assert not resolver._is_confirmation("maybe")
        assert not resolver._is_confirmation("possibly")
        assert not resolver._is_confirmation("I think so")


class TestNegationDetection:
    """Test negation keyword detection."""

    def test_basic_negations(self, resolver):
        """Basic negation words should be detected."""
        assert resolver._is_negation("no")
        assert resolver._is_negation("nope")
        assert resolver._is_negation("cancel")
        assert resolver._is_negation("stop")

    def test_negation_phrases(self, resolver):
        """Negation phrases should be detected."""
        assert resolver._is_negation("never mind")
        assert resolver._is_negation("forget it")

    def test_non_negations(self, resolver):
        """Non-negation words should not match."""
        assert not resolver._is_negation("maybe not")
        assert not resolver._is_negation("I'm not sure")


class TestSelectionExtraction:
    """Test selection/choice extraction."""

    def test_numeric_selection(self, resolver):
        """Numeric selections should be extracted."""
        assert resolver._extract_selection("1") == 0
        assert resolver._extract_selection("2") == 1
        assert resolver._extract_selection("3") == 2

    def test_letter_selection(self, resolver):
        """Letter selections should be extracted."""
        assert resolver._extract_selection("a") == 0
        assert resolver._extract_selection("b") == 1
        assert resolver._extract_selection("c") == 2

    def test_word_selection(self, resolver):
        """Word selections should be extracted."""
        assert resolver._extract_selection("first") == 0
        assert resolver._extract_selection("second") == 1
        assert resolver._extract_selection("the first one") == 0

    def test_option_prefix(self, resolver):
        """Option prefix should work."""
        assert resolver._extract_selection("option 1") == 0
        assert resolver._extract_selection("option 2") == 1

    def test_invalid_selection(self, resolver):
        """Invalid selections should return None."""
        assert resolver._extract_selection("hello") is None
        assert resolver._extract_selection("something else") is None


class TestIntentResolution:
    """Test full intent resolution with context."""

    def test_confirmation_with_pending_question(self, store, resolver, conversation_id):
        """Confirmation should expand based on pending question."""
        # Set up pending question
        store.update_task_state(
            conversation_id,
            pending_question="Should I proceed with the refactoring?"
        )

        resolved = resolver.resolve("yes", conversation_id)

        assert resolved.type == IntentType.CONFIRMATION
        assert resolved.confidence >= 0.8
        assert "refactoring" in resolved.expanded_message.lower()

    def test_selection_with_pending_choices(self, store, resolver, conversation_id):
        """Selection should pick from pending choices."""
        # Set up pending choices
        store.update_task_state(
            conversation_id,
            pending_choices=["Use React", "Use Vue", "Use Svelte"]
        )

        resolved = resolver.resolve("2", conversation_id)

        assert resolved.type == IntentType.SELECTION
        assert resolved.selected_index == 1
        assert resolved.selected_choice == "Use Vue"
        assert "Vue" in resolved.expanded_message

    def test_negation_clears_pending(self, store, resolver, conversation_id):
        """Negation should clear pending state."""
        store.update_task_state(
            conversation_id,
            pending_question="Ready to deploy?",
            pending_choices=["Production", "Staging"]
        )

        resolved = resolver.resolve("no", conversation_id)

        assert resolved.type == IntentType.NEGATION

        # Check state was cleared
        state = store.get_task_state(conversation_id)
        assert not state.has_pending_question()
        assert not state.has_pending_choices()

    def test_normal_message_passes_through(self, resolver, conversation_id):
        """Long messages should pass through unchanged."""
        long_msg = "I would like you to help me implement a new feature for user authentication using OAuth2"

        resolved = resolver.resolve(long_msg, conversation_id)

        assert resolved.type == IntentType.NORMAL
        assert resolved.expanded_message == long_msg
        assert resolved.confidence == 1.0

    def test_continuation_request(self, store, resolver, conversation_id):
        """Continuation request should use next_step context."""
        store.update_task_state(
            conversation_id,
            next_step="Add unit tests for the authentication module"
        )

        resolved = resolver.resolve("continue", conversation_id)

        assert resolved.type == IntentType.CONTINUATION
        assert "tests" in resolved.expanded_message.lower() or "authentication" in resolved.expanded_message.lower()

    def test_unclear_short_reply(self, store, resolver, conversation_id):
        """Unclear short reply should have low confidence."""
        resolved = resolver.resolve("hmm", conversation_id)

        assert resolved.type == IntentType.UNCLEAR
        assert resolved.confidence < 0.5

    def test_question_detected(self, resolver, conversation_id):
        """Short questions should be detected."""
        resolved = resolver.resolve("why?", conversation_id)

        assert resolved.type == IntentType.QUESTION


class TestGetMessageForLLM:
    """Test the message transformation for LLM."""

    def test_high_confidence_uses_expanded(self, store, resolver, conversation_id):
        """High confidence resolutions should use expanded message."""
        store.update_task_state(
            conversation_id,
            pending_question="Should I add logging?"
        )

        result = resolver.get_message_for_llm("yes", conversation_id)

        assert "logging" in result.lower()

    def test_normal_message_unchanged(self, resolver, conversation_id):
        """Normal messages should pass through unchanged."""
        original = "Please help me write a function to parse JSON"

        result = resolver.get_message_for_llm(original, conversation_id)

        assert result == original


class TestTaskStateFormatting:
    """Test task state formatting for context."""

    def test_format_with_all_fields(self, store, resolver, conversation_id):
        """Full task state should format correctly."""
        store.update_task_state(
            conversation_id,
            goal="Build a REST API",
            current_task="Implementing authentication",
            next_step="Add JWT validation",
            pending_question="Which library to use?",
            pending_choices=["jsonwebtoken", "jose"]
        )

        formatted = resolver.format_task_state_for_context(conversation_id)

        assert "REST API" in formatted
        assert "authentication" in formatted
        assert "JWT" in formatted
        assert "library" in formatted.lower()
        assert "jsonwebtoken" in formatted

    def test_format_empty_state(self, resolver, conversation_id):
        """Empty task state should return empty string."""
        formatted = resolver.format_task_state_for_context(conversation_id)

        assert formatted == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
