# backend/utils/text_similarity.py
"""
Tiny, dependency-free text similarity helpers.

Used by procedure memory, the screen timeline and the proactive engine
so Sarah can match "similar" text without paid embedding APIs.
"""
import re
from typing import Iterable, Set

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "to", "of", "in", "on", "for",
    "with", "at", "by", "from", "is", "are", "was", "were", "be", "been",
    "it", "this", "that", "these", "those", "me", "my", "i", "you", "your",
    "please", "can", "could", "would", "will", "should", "do", "does",
    "some", "any", "into", "about", "up", "so", "then", "just", "sarah",
}

_WORD_RE = re.compile(r"[a-z0-9]+")


def _stem(word: str) -> str:
    # Very light stemming so "files" ~ "file", "created" ~ "create",
    # "running" ~ "run", "boxes" ~ "box"
    if len(word) > 4 and word.endswith("es") and word[-3] in "sxz":
        word = word[:-2]
    else:
        for suffix in ("ing", "ed", "s"):
            if len(word) > len(suffix) + 2 and word.endswith(suffix):
                word = word[: -len(suffix)]
                break
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]
    if len(word) > 3 and word[-1] == word[-2]:
        word = word[:-1]
    return word


def tokenize(text: str) -> Set[str]:
    """Lowercase, split into words, drop stopwords, lightly stem."""
    if not text:
        return set()
    words = _WORD_RE.findall(text.lower())
    return {_stem(w) for w in words if w not in _STOPWORDS}


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def similarity(text_a: str, text_b: str) -> float:
    """0.0 (nothing in common) .. 1.0 (same words)."""
    return jaccard(tokenize(text_a), tokenize(text_b))
