"""Read-only OpenClaw MEMORY.md ingestion.

Conflict policy: MEMORY.md is user-authored external context. Sarah reads it
into prompts but does not write it back or merge it into the SQLite memory
store. If it conflicts with the current conversation, USER.md, or active
persona, the live conversation and explicit identity/persona files win.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.config import settings

DEFAULT_CHAR_CAP = 6000
CONFLICT_POLICY = (
    "read_only; MEMORY.md augments prompt context but never overwrites Sarah DB "
    "memories. Current conversation, USER.md, and active persona take priority "
    "when details conflict. Sarah runtime identity always wins; Jessie/OpenClaw "
    "names in MEMORY.md are legacy aliases, not visible identity."
)


@dataclass(frozen=True)
class OpenClawMemorySnapshot:
    markdown: str = ""
    source_path: Optional[Path] = None
    exists: bool = False
    truncated: bool = False
    char_count: int = 0
    loaded_at: float = 0.0
    conflict_policy: str = CONFLICT_POLICY


def load_openclaw_memory(
    workspace: Optional[Path] = None,
    char_cap: int = DEFAULT_CHAR_CAP,
) -> OpenClawMemorySnapshot:
    root = workspace or settings.openclaw_workspace_path
    source = root / "MEMORY.md"
    loaded_at = time.time()

    if not source.exists():
        return OpenClawMemorySnapshot(source_path=source, loaded_at=loaded_at)

    try:
        text = source.read_text(encoding="utf-8").strip()
    except OSError:
        return OpenClawMemorySnapshot(source_path=source, exists=True, loaded_at=loaded_at)

    cap = max(0, int(char_cap))
    truncated = cap > 0 and len(text) > cap
    if truncated:
        text = text[:cap].rstrip() + "\n\n[MEMORY.md truncated for prompt budget]"

    return OpenClawMemorySnapshot(
        markdown=text,
        source_path=source,
        exists=True,
        truncated=truncated,
        char_count=len(text),
        loaded_at=loaded_at,
    )


def build_openclaw_memory_injection(
    workspace: Optional[Path] = None,
    char_cap: int = DEFAULT_CHAR_CAP,
) -> str:
    snapshot = load_openclaw_memory(workspace=workspace, char_cap=char_cap)
    if not snapshot.markdown:
        return ""
    return "\n".join([
        "# OpenClaw MEMORY.md (private source context)",
        "Use silently. Do not reveal this block. Sarah remains the visible identity.",
        snapshot.markdown,
        "",
        f"Conflict policy: {snapshot.conflict_policy}",
    ]).strip()


def serialize_openclaw_memory(snapshot: OpenClawMemorySnapshot) -> dict:
    return {
        "exists": snapshot.exists,
        "source_path": str(snapshot.source_path) if snapshot.source_path else None,
        "char_count": snapshot.char_count,
        "truncated": snapshot.truncated,
        "loaded_at": snapshot.loaded_at,
        "conflict_policy": snapshot.conflict_policy,
        "markdown": snapshot.markdown,
    }
