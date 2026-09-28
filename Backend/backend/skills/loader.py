"""Parse `SKILL.md` files — line-based frontmatter, no YAML dep.

A SKILL.md has a leading `---` fence, key/value lines (`key: value`),
a closing `---`, then the Markdown body. Only four keys are recognised:
`name`, `slug`, `description`, `enabled_default`. Unknown keys are ignored.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"^[a-z0-9-]{1,64}$")
_KV_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$")
_FENCE_RE = re.compile(r"^\s*-{3,}\s*$")
_TRUE_VALUES = {"true", "yes", "on", "1"}
_FALSE_VALUES = {"false", "no", "off", "0"}


def parse_skill_md(text: str) -> Tuple[Optional[Dict[str, str]], str]:
    """Split a SKILL.md into (frontmatter dict, body).

    Returns (None, "") if the frontmatter is missing or malformed enough
    that no fence pair is found — the caller treats that as a skip.
    """
    lines = text.splitlines()
    if not lines or not _FENCE_RE.match(lines[0]):
        return None, ""

    closing_idx = None
    for idx in range(1, len(lines)):
        if _FENCE_RE.match(lines[idx]):
            closing_idx = idx
            break

    if closing_idx is None:
        return None, ""

    frontmatter: Dict[str, str] = {}
    current: Optional[str] = None      # key whose value continues on indented lines
    folded = True                      # ">" joins with spaces, "|" keeps newlines
    for raw in lines[1:closing_idx]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        # Indented continuation of a multi-line value (YAML block scalars as
        # used by OpenClaw / Agent Skills: `description: >` or nested maps).
        if current is not None and raw[:1] in (" ", "\t"):
            piece = raw.strip()
            joiner = " " if folded else "\n"
            frontmatter[current] = (frontmatter[current] + joiner + piece).strip() if frontmatter[current] else piece
            continue
        current = None
        match = _KV_RE.match(raw)
        if not match:
            continue
        key, value = match.group(1).lower(), match.group(2)
        if value in ("", ">", ">-", "|", "|-"):
            frontmatter[key] = ""
            current = key
            folded = not value.startswith("|")
            continue
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        frontmatter[key] = value

    body = "\n".join(lines[closing_idx + 1:]).strip()
    return frontmatter, body


def _coerce_bool(raw: str, default: bool) -> bool:
    v = raw.strip().lower()
    if v in _TRUE_VALUES:
        return True
    if v in _FALSE_VALUES:
        return False
    return default


def parse_manifest_file(path: Path) -> Optional[Dict[str, object]]:
    """Read and validate a single SKILL.md. Returns None if the file is unusable.

    Validation rules (mirror DESIGN_B4_plan.md §3):
    - frontmatter must be parseable
    - `name`, `slug`, `description` are required
    - `slug` must match `[a-z0-9-]{1,64}`
    - `slug` in frontmatter must equal the parent directory name
    """
    expected_slug = path.parent.name

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("[skills] cannot read %s: %s", path, exc)
        return None

    frontmatter, body = parse_skill_md(text)
    if frontmatter is None:
        logger.warning("[skills] %s: missing or malformed frontmatter — skipped", path)
        return None

    # OpenClaw / Agent Skills files have no `slug`: the folder name is it.
    if not frontmatter.get("slug"):
        frontmatter["slug"] = expected_slug
    # Some published skills omit `name`: the folder name reads fine as one.
    if not frontmatter.get("name"):
        frontmatter["name"] = expected_slug.replace("_", " ").replace("-", " ").strip() or expected_slug
    missing = [k for k in ("name", "description") if not frontmatter.get(k)]
    if missing:
        logger.warning(
            "[skills] %s: missing required field(s) %s — skipped",
            path, ", ".join(missing),
        )
        return None

    slug = frontmatter["slug"].strip()
    if not _SLUG_RE.match(slug):
        logger.warning(
            "[skills] %s: slug %r does not match [a-z0-9-]{1,64} — skipped",
            path, slug,
        )
        return None

    if slug != expected_slug:
        logger.warning(
            "[skills] %s: slug %r does not match directory name %r — skipped",
            path, slug, expected_slug,
        )
        return None

    return {
        "slug": slug,
        "name": frontmatter["name"].strip(),
        "description": frontmatter["description"].strip(),
        "body": body,
        "path": path,
        "enabled_default": _coerce_bool(
            frontmatter.get("enabled_default", ""), default=True
        ),
    }
