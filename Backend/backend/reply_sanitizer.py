"""Helpers for stripping private prompt fragments from user-visible replies."""
from __future__ import annotations

import re

_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
_INTERNAL_PLACEHOLDER_RE = re.compile(
    r"\[\s*(?:add\s+)?internal\s+notes?[\w\s,.;:'\"!?/-]*\]",
    re.IGNORECASE,
)
_INTERNAL_INLINE_CUTOFF_RE = re.compile(
    r"\s*(?:\*\*)?\s*(?:addendum\s*)?\(?internal\s+notes?\)?[\s\S]*$",
    re.IGNORECASE,
)
_VISIBLE_RESPONSE_RE = re.compile(
    r"(?ims)^\s*(?:final\s+)?(?:visible\s+)?response\s*:\s*(.*)$",
)
_SCAFFOLD_CUTOFF_RE = re.compile(
    r"\n\s*(?:---|\[TOC\]|#{1,6}\s+|\(Exact\s+word\s+count|"
    r"(?:\*\*)?(?:rationale|reasoning|note)\b)[\s\S]*$",
    re.IGNORECASE,
)
_FOOTER_CUTOFF_RE = re.compile(
    r"\s+(?:let\s+me\s+know\s+(?:if|how)|feel\s+free\s+to|"
    r"let'?s\s+get\s+started|\(if\s+you\s+need|if\s+you\s+need|"
    r"[^\nA-Za-z0-9]*Sarah[^\n]*AI,\s+a\s+warm)[\s\S]*$",
    re.IGNORECASE,
)
_TASK_META_LINE_RE = re.compile(
    r"^\s*[-*]\s*\*\*(?:action|status|resolution|next\s+steps?|rationale)\s*:\*\*.*$",
    re.IGNORECASE,
)
_INTERNAL_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:[*_`]*|\[)?"
    r"(?:creator\s+note|zero\s+note|jessie\s+note|sarah\s+note|internal\s+notes?|"
    r"assistant\s+notes?|private\s+notes?|intent|reasoning|system\s+prompt)"
    r"(?:[*_`]*|\])?\s*(?::|-|$)",
    re.IGNORECASE,
)


def _strip_wrapping_quotes(text: str) -> str:
    stripped = text.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in ("'", '"'):
        return stripped[1:-1].strip()
    return stripped


def sanitize_visible_reply(text: str) -> str:
    """Return only content that is safe to show in chat.

    Local models sometimes echo system prompt labels or scaffolding such as
    "Creator note" and "[Add internal notes ...]". Those strings are private
    implementation details, so they are removed before saving or rendering.
    """
    if not text:
        return ""

    cleaned = str(text).replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _THINK_BLOCK_RE.sub("", cleaned)
    cleaned = _INTERNAL_PLACEHOLDER_RE.sub("", cleaned)
    cleaned = _INTERNAL_INLINE_CUTOFF_RE.sub("", cleaned)

    # Some local models prepend task-status scaffolding, then label the actual
    # chat content as "Response:". Prefer the labeled visible reply.
    response_match = _VISIBLE_RESPONSE_RE.search(cleaned)
    if response_match:
        cleaned = response_match.group(1).strip()

    cleaned = _SCAFFOLD_CUTOFF_RE.sub("", cleaned)
    cleaned = _FOOTER_CUTOFF_RE.sub("", cleaned)

    kept: list[str] = []
    for line in cleaned.split("\n"):
        if _TASK_META_LINE_RE.match(line):
            continue
        if _INTERNAL_HEADING_RE.match(line):
            if "\n".join(kept).strip():
                break
            continue
        kept.append(line)

    cleaned = "\n".join(kept)
    cleaned = re.sub(r"\n\s*(?:---|\*\*\*)\s*$", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = _FOOTER_CUTOFF_RE.sub("", cleaned)
    return _strip_wrapping_quotes(cleaned)
