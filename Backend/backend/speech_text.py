"""What she says out loud vs what she writes.

Replies are written for the chat: emoji, ✅ checkmarks, markdown, code,
links, file paths, quotation marks. Read aloud, those come out as "check mark
button", "asterisk asterisk", whole URLs and folder names. ``for_speech``
keeps the words and drops (or shortens) the rest; the chat text is untouched.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

_CODE_BLOCK = re.compile(r"```.*?(```|$)", re.S)
_MD_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]+)\)")
_URL = re.compile(r"\bhttps?://[^\s)>\]]+", re.I)
_WIN_PATH = re.compile(r"\b[A-Za-z]:[\\/][^\s,;:'\"`)]*")
# data/sarah_workspace/notes.txt, src/app/main.py (not "and/or", "24/7", 9/28/2026)
_REL_PATH = re.compile(r"(?<![\w@])(?:[\w.-]+[\\/]){2,}[\w.-]+|(?<![\w@])(?:[\w.-]+[\\/])+[\w-]+\.[A-Za-z]{1,5}\b")
# Emoji, pictographs, dingbats (✅ ✓ ✗ ★ ➜ ...), arrows, box drawing, variation selectors.
_SYMBOLS = re.compile(
    "[\U0001F000-\U0001FAFF\U00002190-\U000021FF\U00002300-\U000023FF\U00002460-\U000027BF"
    "\U00002900-\U00002BFF\U0000FE00-\U0000FE0F\U0001F1E6-\U0001F1FF‍•■-◿]")
_MARKUP = re.compile(r"(\*\*|__|\*|~~|`|^#{1,6}\s*|^>\s?)", re.M)
_BULLET = re.compile(r"^\s*[-*+•]\s+", re.M)
_QUOTES = re.compile("[\"“”„«»]")


def _host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _last_part(path: str) -> str:
    parts = [p for p in re.split(r"[\\/]", path) if p]
    return parts[-1] if parts else ""


def for_speech(text: str) -> str:
    text = _CODE_BLOCK.sub(" ", text or "")
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub(lambda m: _host(m.group(0)), text)
    text = _WIN_PATH.sub(lambda m: _last_part(m.group(0)), text)
    text = _REL_PATH.sub(lambda m: m.group(0) if re.fullmatch(r"[\d/\\.-]+", m.group(0))
                         else _last_part(m.group(0)), text)
    text = _SYMBOLS.sub(" ", text)
    text = _BULLET.sub("", text)
    text = _MARKUP.sub("", text)
    text = _QUOTES.sub("", text)
    text = re.sub(r"[ \t]+([,.!?;:])", r"\1", text)
    text = re.sub(r"\s*\n\s*", ". ", text.strip())
    text = re.sub(r"(\.\s*){2,}", ". ", text)
    text = re.sub(r"([!?:,])\.", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()
