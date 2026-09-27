from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.reply_sanitizer import sanitize_visible_reply


def test_removes_internal_notes_placeholder():
    assert sanitize_visible_reply("[Add internal notes if needed before sending]") == ""


def test_cuts_internal_section_after_visible_reply():
    raw = "Hello, Zero.\n\nCreator note: do not show this\nPrivate prompt text"
    assert sanitize_visible_reply(raw) == "Hello, Zero."


def test_cuts_inline_internal_note_addendum():
    raw = "Latency issue addressed. **Addendum (internal note):** do not show this"
    assert sanitize_visible_reply(raw) == "Latency issue addressed."


def test_prefers_labeled_visible_response_after_scaffolding():
    raw = """- **Action:** Fixing Sarah's response time
- **Status:** Complete

---

Response:
"Hey Zero! Sarah is answering faster now."
"""
    assert sanitize_visible_reply(raw) == "Hey Zero! Sarah is answering faster now."


def test_cuts_rationale_scaffolding_after_visible_text():
    raw = "Final check initiated.\n\n**Rationale:** hidden process details"
    assert sanitize_visible_reply(raw) == "Final check initiated."


def test_cuts_local_model_footer_and_signature():
    raw = "Sarah AI here; your request has been processed. Let me know how I can assist further!\n-- Sarah AI, a warm assistant"
    assert sanitize_visible_reply(raw) == "Sarah AI here; your request has been processed."


def test_cuts_local_model_note_after_answer():
    raw = "Sarah AI: Ready to assist at your command.\n\n(Exact word count: 12)\n\nNote:\nInternal format explanation"
    assert sanitize_visible_reply(raw) == "Sarah AI: Ready to assist at your command."


def test_cuts_local_model_starter_footer():
    raw = "Sarah AI: Ready for action at 02:10 AM. Let's get started!\n\n(If you need more context, just say so.)"
    assert sanitize_visible_reply(raw) == "Sarah AI: Ready for action at 02:10 AM."


def test_strips_rationale_note_signature_and_footer_blocks_together():
    raw = """Sarah AI: Ready for action at 02:10 AM.

**Rationale:** hidden reasoning

Note:
This follows the requested format.

-- Sarah AI, a warm assistant

Let me know how I can assist further."""
    assert sanitize_visible_reply(raw) == "Sarah AI: Ready for action at 02:10 AM."


def test_ready_reply_fixture_stays_one_sentence_after_sanitizing():
    raw = "Sarah AI: Ready for action at 02:10 AM. Let's get started!\n\n(If you need more context, just say so.)"
    cleaned = sanitize_visible_reply(raw)
    sentence_endings = re.findall(r"[.!?](?:\s|$)", cleaned)

    assert cleaned == "Sarah AI: Ready for action at 02:10 AM."
    assert len(sentence_endings) == 1


def test_removes_think_block_and_preserves_reply():
    raw = "<think>hidden reasoning</think>\nSarah is ready."
    assert sanitize_visible_reply(raw) == "Sarah is ready."


def test_preserves_normal_multiline_reply():
    raw = "Line one.\n\nLine two."
    assert sanitize_visible_reply(raw) == raw
