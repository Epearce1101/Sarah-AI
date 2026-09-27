"""Parse and load `USER.md` into the in-memory identity snapshot.

The loader is one-shot: called once at boot, plus on demand via
`/api/identity/reload`. No file watcher, no poll loop. Failures degrade
gracefully — boot continues with the empty default snapshot.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Mapping, Optional

from backend.config.settings import settings

from .state import UserIdentity, set_user

logger = logging.getLogger(__name__)

# Match a template line like `- **Name:** value`, `- Name: value`, or
# `- **Pronouns:** *(optional)*`. In Markdown, `**Name:**` puts the colon
# inside the bold delimiters, so the trailing `**` follows the colon.
# The italic placeholder `*(...)*` is stripped from the value.
_FIELD_RE = re.compile(
    r"^\s*-\s*\*{0,2}\s*(?P<label>[A-Za-z][A-Za-z ]+?)\s*:\s*\*{0,2}\s*(?P<value>.*?)\s*$"
)
_PLACEHOLDER_RE = re.compile(r"\*\([^)]*\)\*")

# Map normalised label → UserIdentity field name. Labels are matched
# case-insensitive with whitespace collapsed.
_LABEL_MAP = {
    "name": "name",
    "what to call them": "call_name",
    "pronouns": "pronouns",
    "timezone": "timezone",
    "notes": "notes",
}


def _normalise(label: str) -> str:
    return " ".join(label.lower().split())


def parse_user_md(text: str) -> dict:
    """Extract template fields from USER.md text. Unknown fields are ignored."""
    fields: dict = {}
    for line in text.splitlines():
        match = _FIELD_RE.match(line)
        if not match:
            continue
        key = _LABEL_MAP.get(_normalise(match.group("label")))
        if not key:
            continue
        value = _PLACEHOLDER_RE.sub("", match.group("value")).strip()
        fields[key] = value
    return fields


def load(path: Optional[Path] = None) -> UserIdentity:
    """Read USER.md from disk, parse it, and install the snapshot."""
    source = path or (settings.openclaw_workspace_path / "USER.md")
    snapshot = _build_snapshot(source)
    set_user(snapshot)
    name_for_log = snapshot.call_name or snapshot.name or "(empty → fallback)"
    logger.info(
        "[identity] loaded source=%s name=%s",
        snapshot.source_path,
        name_for_log,
    )
    return snapshot


def reload() -> UserIdentity:
    """Re-read USER.md and replace the snapshot. Same as load()."""
    return load()


def save_user_md(fields: Mapping[str, str], path: Optional[Path] = None) -> UserIdentity:
    """Write a canonical OpenClaw USER.md, then reload the identity snapshot."""
    source = path or (settings.openclaw_workspace_path / "USER.md")
    source.parent.mkdir(parents=True, exist_ok=True)

    def clean(key: str) -> str:
        return str(fields.get(key, "") or "").strip()

    text = "\n".join([
        "# USER",
        "",
        "- **Name:** " + clean("name"),
        "- **What to call them:** " + clean("call_name"),
        "- **Pronouns:** " + clean("pronouns"),
        "- **Timezone:** " + clean("timezone"),
        "- **Notes:** " + clean("notes"),
        "",
    ])
    source.write_text(text, encoding="utf-8")
    return load(source)


def _build_snapshot(source: Path) -> UserIdentity:
    try:
        text = source.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("[identity] USER.md not found at %s — using fallback", source)
        return UserIdentity(loaded_at=time.time())
    except OSError as exc:
        logger.warning("[identity] failed to read %s: %s — using fallback", source, exc)
        return UserIdentity(loaded_at=time.time())

    try:
        fields = parse_user_md(text)
    except Exception as exc:  # noqa: BLE001 — defensive; never abort boot
        logger.warning("[identity] parse failed for %s: %s — using fallback", source, exc)
        return UserIdentity(source_path=source, loaded_at=time.time())

    return UserIdentity(
        name=fields.get("name", ""),
        call_name=fields.get("call_name", ""),
        pronouns=fields.get("pronouns", ""),
        timezone=fields.get("timezone", ""),
        notes=fields.get("notes", ""),
        source_path=source,
        loaded_at=time.time(),
    )
