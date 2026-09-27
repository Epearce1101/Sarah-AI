# Sarah AI V11 Enhanced Memory System

## Overview

The V11 memory system provides reliable long conversation support through:
- **Rolling Summaries**: Updated every 10 turns with goal/decisions/progress/constraints/next
- **Chunk Summaries**: 2-6 bullet summaries every 20 messages
- **Task State**: Tracks pending questions, choices, and context for short replies
- **Intent Resolution**: Handles "yes", "option 2", "continue" with context awareness
- **Token Budget Management**: Fits everything within ~6000 tokens

**CRITICAL**: All memory operations are SILENT - never shown to the user.

## Architecture

```
backend/memory/
├── __init__.py          # Package exports
├── config.py            # Configuration via environment variables
├── memory_store.py      # SQLite persistence (summaries, chunks, state)
├── summarizer.py        # LLM-powered summarization
├── intent_resolver.py   # Short reply understanding
├── context_builder.py   # Builds LLM API context packets (OpenAI-compatible)
├── openrouter_client.py # Memory-integrated OpenRouter client (OpenAI SDK)
└── README.md            # This file

backend/tests/
├── test_memory_store.py    # Memory persistence tests
├── test_intent_resolver.py # Short reply tests
└── test_context_builder.py # Context building tests
```

## Configuration

All settings are configurable via environment variables:

```bash
# Recent message window (verbatim messages)
SARAH_RECENT_WINDOW_MESSAGES=80    # Default: 80 (was 15)
SARAH_MIN_RECENT_MESSAGES=20       # Minimum to keep when trimming

# Chunk summaries
SARAH_CHUNK_SIZE=20                # Messages per chunk
SARAH_MAX_CHUNKS_IN_CONTEXT=5      # Max chunks to include

# Rolling summary
SARAH_ROLLING_UPDATE_INTERVAL=10   # Turns between updates

# Token budgets
SARAH_TOTAL_TOKEN_BUDGET=6000      # Total context budget
SARAH_SYSTEM_PROMPT_TOKENS=500     # Reserved for system prompt

# Short reply handling
SARAH_SHORT_REPLY_WORD_LIMIT=12    # Words to consider "short"

# OpenRouter / LLM settings
SARAH_OPENROUTER_API_KEY=...
SARAH_OPENROUTER_MODEL=openrouter/auto
SARAH_LLM_MAX_COMPLETION_TOKENS=1400
SARAH_LLM_TEMPERATURE=0.7

# Debug (shows internal memory operations in console)
SARAH_DEBUG_MEMORY=false
```

## Usage Scenarios

### Scenario 1: Basic Conversation with Memory

```python
# User sends a message with conversation_id
# Memory system automatically:
# 1. Loads rolling summary (if exists)
# 2. Loads chunk summaries (if exists)
# 3. Loads task state (pending questions, choices)
# 4. Gets recent messages from database
# 5. Resolves intent if short reply
# 6. Builds context packet within token budget
# 7. Calls OpenRouter (OpenAI-compatible) API
# 8. Saves messages
# 9. Updates summaries in background
```

### Scenario 2: Short Reply Handling

```
User: "Help me build a REST API"
Sarah: "I'd be happy to help! Which framework would you prefer?
        1. FastAPI (recommended for Python)
        2. Express.js (for Node.js)
        3. Flask (lightweight Python)"

User: "1"
# Intent resolver expands "1" to "I choose: FastAPI (recommended for Python)"
# Context includes the pending question for accurate understanding

Sarah: "Great choice! Let's start with FastAPI..."
```

### Scenario 3: Confirmation After Question

```
User: "Add user authentication"
Sarah: "I'll add authentication. Should I use JWT or session-based auth?"

User: "yes"
# Intent resolver sees "yes" + pending_question="Should I use JWT or session-based auth?"
# Expands to "Yes. (Regarding: Should I use JWT or session-based auth?)"
# Sarah understands this is a confirmation, not a choice
```

### Scenario 4: Long Conversation (80+ messages)

```
# Messages 1-20: Discussed project setup
#   -> Chunk summary created: "- Set up project structure\n- Chose Python + FastAPI"

# Messages 21-40: Implemented user model
#   -> Chunk summary created: "- Created User model\n- Added database migrations"

# Messages 41-60: Added authentication
#   -> Chunk summary created: "- Implemented JWT auth\n- Created login/signup endpoints"

# Messages 61-80: Current conversation
#   -> These are sent verbatim

# Rolling summary (updated every 10 turns):
# Goal: Build a REST API for user management
# Decisions: FastAPI, PostgreSQL, JWT auth
# Progress: Basic auth working, need to add roles
# Next: Implement role-based access control
```

## Testing

Run tests with pytest:

```bash
cd backend
pytest tests/test_memory_store.py -v
pytest tests/test_intent_resolver.py -v
pytest tests/test_context_builder.py -v
```

### Test Coverage

**Intent Resolver Tests (12+)**:
- Short reply detection
- Confirmation keywords
- Negation keywords
- Selection extraction (numbers, letters, words)
- Resolution with pending question
- Resolution with pending choices
- Clearing pending state
- Normal message passthrough
- Continuation requests
- Unclear replies
- Question detection
- Message formatting for LLM

**Context Builder Tests**:
- System prompt inclusion
- User message inclusion
- Recent message history
- Rolling summary injection
- Task state injection
- Chunk summary injection
- Vision observation injection
- Token budget enforcement
- Message trimming
- Minimum message retention
- Intent resolution integration

**Memory Store Tests**:
- TaskState CRUD
- RollingSummary CRUD
- ChunkSummary CRUD
- Message helpers
- Unchunked message detection
- Old chunk cleanup

## Troubleshooting

### Memory Not Working

1. Check if memory system initialized:
   ```python
   sarah = get_sarah()
   print(sarah.memory_enabled)  # Should be True
   ```

2. Check database tables exist:
   ```sql
   SELECT name FROM sqlite_master WHERE type='table'
   AND name IN ('conversation_state', 'rolling_summaries', 'chunk_summaries');
   ```

3. Enable debug mode:
   ```bash
   export SARAH_DEBUG_MEMORY=true
   ```

### Short Replies Not Working

1. Check task state has pending data:
   ```python
   from backend.memory import get_memory_store
   store = get_memory_store()
   state = store.get_task_state(conversation_id)
   print(state.pending_question)
   print(state.pending_choices)
   ```

2. Verify intent resolution:
   ```python
   from backend.memory import IntentResolver
   resolver = IntentResolver()
   result = resolver.resolve("yes", conversation_id)
   print(result.type, result.expanded_message)
   ```

### Token Budget Issues

1. Check remaining budget:
   ```python
   from backend.memory import ContextBuilder
   builder = ContextBuilder()
   remaining = builder.get_remaining_token_budget(conversation_id)
   print(f"Remaining: {remaining} tokens")
   ```

2. Increase budget if needed:
   ```bash
   export SARAH_TOTAL_TOKEN_BUDGET=8000
   ```

### Summaries Not Updating

1. Check message count triggers:
   - Rolling: Updates every 20 messages (10 turns × 2 messages)
   - Chunks: Created every 20 messages

2. Force update:
   ```python
   from backend.memory import Summarizer, get_memory_store
   summarizer = Summarizer(llm_call_fn, get_memory_store())
   await summarizer.update_rolling_summary(conversation_id, force=True)
   ```

## Database Schema

New tables added by memory system:

```sql
-- Task state for short reply understanding
CREATE TABLE conversation_state (
    conversation_id INTEGER PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

-- Rolling summaries (one per conversation)
CREATE TABLE rolling_summaries (
    conversation_id INTEGER PRIMARY KEY,
    summary_json TEXT NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

-- Chunk summaries (multiple per conversation)
CREATE TABLE chunk_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    start_message_id INTEGER NOT NULL,
    end_message_id INTEGER NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
```

## Integration with SarahCore

The memory system integrates with SarahCore via:

1. **handle_message()**: Now accepts `conversation_id` parameter
2. **_handle_message_with_memory()**: V11 path using OpenRouterClient
3. **_handle_message_legacy()**: V10 fallback without memory

When `conversation_id` is provided:
- Uses OpenRouterClient for context building
- Automatically saves messages
- Triggers background summary updates
- Resolves short replies using task state

## API Flow

```
POST /api/chat
    ↓
server.py: api_chat()
    ↓
sarah.handle_message(message, conversation_id=X)
    ↓
[if conversation_id provided]
    ↓
_handle_message_with_memory()
    ↓
OpenRouterClient.chat()
    ↓
ContextBuilder.build()
    ├── IntentResolver.resolve()
    ├── MemoryStore.get_rolling_summary()
    ├── MemoryStore.get_task_state()
    ├── MemoryStore.get_chunk_summaries()
    └── MemoryStore.get_recent_messages()
    ↓
OpenRouter API call
    ↓
Summarizer.update_all() [background]
    ↓
Response to user
```
