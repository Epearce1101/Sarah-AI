"""Mouth shapes for her speech, timed to the audio.

Each spoken sentence gets a viseme timeline the avatar plays alongside the
clip, so her mouth makes the right shape for each sound rather than
guessing it from the sound's spectrum:

    [[start_ms, code, length_ms], ...]   code: a e i o u (vowel shapes),
                                          m (lips closed: m b p), f (f v),
                                          c (other consonants), _ (silence)

Kokoro gives phonemes; when the model reports their durations they're used
directly, otherwise the phonemes are spread over the voiced parts of the
audio (found from its loudness), phrase by phrase. Piper gets the same
treatment from the spelling.
"""
from __future__ import annotations

import json
import re
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np

# IPA (espeak / misaki, as Kokoro uses) -> viseme code.
_IPA = {
    **dict.fromkeys("aɑæʌɐ", "a"),
    **dict.fromkeys("eɛəɚɜɝ", "e"),
    **dict.fromkeys("iɪyɨ", "i"),
    **dict.fromkeys("oɔɒɵ", "o"),
    **dict.fromkeys("uʊɯw", "u"),
    # misaki diphthongs: A=eɪ I=aɪ O=oʊ W=aʊ Y=ɔɪ (the first part shapes the mouth)
    "A": "e", "I": "a", "O": "o", "W": "a", "Y": "o",
    **dict.fromkeys("mbp", "m"),
    **dict.fromkeys("fv", "f"),
}
_SKIP = set("ˈˌːˑ̃ʰ̩̯‿͡-'")
_PAUSE = set(",.;:!?—…")
VOWELS = set("aeiou")


def units_from_phonemes(phonemes: str) -> List[str]:
    """One code per phoneme: vowels, m, f, c, a space ' ' between words and
    '|' at punctuation (a phrase break)."""
    out: List[str] = []
    for ch in phonemes or "":
        if ch in _SKIP:
            continue
        if ch in _PAUSE:
            out.append("|")
        elif ch.isspace():
            if out and out[-1] not in (" ", "|"):
                out.append(" ")
        else:
            out.append(_IPA.get(ch, "c"))
    return out


_SPELLING = [
    (r"oo|ou|ew|ue|w", "u"), (r"ee|ea|ie|ey", "i"), (r"ai|ay|ei", "e"), (r"oa|ow|oe", "o"), (r"au|aw", "o"),
    (r"igh|y$", "a"), (r"a", "a"), (r"e", "e"), (r"i|y", "i"), (r"o", "o"), (r"u", "a"),
    (r"[mbp]+", "m"), (r"ph|[fv]+", "f"),
]
_SPELL_RE = re.compile("|".join(f"(?P<g{i}>{p})" for i, (p, _) in enumerate(_SPELLING)))


def units_from_text(text: str) -> List[str]:
    """Rough units from spelling (for Piper, which gives no phonemes)."""
    out: List[str] = []
    for token in re.findall(r"[a-z']+|[,.;:!?…—]", (text or "").lower()):
        if token[0] in _PAUSE:
            out.append("|")
            continue
        i = 0
        while i < len(token):
            m = _SPELL_RE.match(token, i)
            if m:
                code = _SPELLING[int(m.lastgroup[1:])][1]
                out.append(code)
                i = m.end()
            else:
                if token[i] != "'":
                    out.append("c")
                i += 1
        out.append(" ")
    while out and out[-1] in (" ", "|"):
        out.pop()
    return out


def voiced_spans(samples: np.ndarray, sr: int, min_gap: float = 0.09) -> List[Tuple[float, float]]:
    """(start, end) seconds of the parts with sound, split at pauses of at
    least `min_gap` seconds."""
    x = np.asarray(samples, dtype=np.float32).ravel()
    if x.size == 0:
        return []
    hop = max(1, int(sr * 0.01))
    n = x.size // hop
    if n == 0:
        return []
    rms = np.sqrt(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1))
    loud = rms > max(1e-4, float(rms.max()) * 0.06)
    spans, start, quiet = [], None, 0
    gap = int(round(min_gap / 0.01))
    for i, on in enumerate(loud):
        if on:
            if start is None:
                start = i
            quiet = 0
        elif start is not None:
            quiet += 1
            if quiet >= gap:
                spans.append((start * 0.01, (i - quiet + 1) * 0.01))
                start, quiet = None, 0
    if start is not None:
        spans.append((start * 0.01, (n - quiet) * 0.01))
    return [(a, b) for a, b in spans if b - a >= 0.03]


_WEIGHT = {"a": 1.6, "e": 1.4, "i": 1.3, "o": 1.6, "u": 1.4, "m": 0.9, "f": 0.9, "c": 0.8, " ": 0.35}


def _spread(units: Sequence[str], start: float, end: float) -> List[Tuple[float, str, float]]:
    """Lay units over [start, end) in proportion to how long each tends to last."""
    units = [u for u in units if u != "|"]
    while units and units[0] == " ":
        units = units[1:]
    while units and units[-1] == " ":
        units = units[:-1]
    if not units or end <= start:
        return []
    total = sum(_WEIGHT.get(u, 1.0) for u in units)
    t, out = start, []
    for u in units:
        d = (end - start) * _WEIGHT.get(u, 1.0) / total
        out.append((t, "_" if u == " " else u, d))
        t += d
    return out


def _best_split(sizes: Sequence[float], targets: Sequence[float]) -> List[Tuple[int, int]]:
    """Cut `sizes` into len(targets) contiguous runs whose shares of the
    total best match `targets`' shares (least squares). Returns (start, end)
    index pairs."""
    n, k = len(sizes), len(targets)
    tot_s, tot_t = float(sum(sizes)) or 1.0, float(sum(targets)) or 1.0
    pre = [0.0]
    for x in sizes:
        pre.append(pre[-1] + x)
    inf = float("inf")
    cost = [[inf] * (n + 1) for _ in range(k + 1)]
    back = [[0] * (n + 1) for _ in range(k + 1)]
    cost[0][0] = 0.0
    for j in range(1, k + 1):
        want = targets[j - 1] / tot_t
        for i in range(j, n - (k - j) + 1):
            for m in range(j - 1, i):
                c = cost[j - 1][m] + ((pre[i] - pre[m]) / tot_s - want) ** 2
                if c < cost[j][i]:
                    cost[j][i], back[j][i] = c, m
    runs, i = [], n
    for j in range(k, 0, -1):
        m = back[j][i]
        runs.append((m, i))
        i = m
    return runs[::-1]


def _spread_over(units: Sequence[str], spans: Sequence[Tuple[float, float]]) -> List[Tuple[float, str, float]]:
    """Spread units over consecutive spans as if they were one, skipping the
    pauses between them (time is counted over sound only)."""
    sound = sum(b - a for a, b in spans)
    flat = _spread(units, 0.0, sound)
    out = []
    for t, code, d in flat:
        # Map "seconds of sound" back onto the real clock.
        acc = 0.0
        for a, b in spans:
            if t < acc + (b - a) or (a, b) == spans[-1]:
                out.append((a + (t - acc), code, d))
                break
            acc += b - a
    return out


def align(units: Sequence[str], samples: np.ndarray, sr: int) -> List[Tuple[float, str, float]]:
    """(start, code, length) seconds for each unit, over the audio's voiced parts."""
    spans = voiced_spans(samples, sr)
    if not spans or not units:
        return []
    phrases: List[List[str]] = [[]]
    for u in units:
        if u == "|":
            if phrases[-1]:
                phrases.append([])
        else:
            phrases[-1].append(u)
    phrases = [p for p in phrases if any(c != " " for c in p)]
    if not phrases:
        return []
    weight = [sum(_WEIGHT.get(u, 1.0) for u in p) for p in phrases]
    length = [b - a for a, b in spans]
    out: List[Tuple[float, str, float]] = []
    if len(phrases) >= len(spans):
        # Some punctuation got no pause: several phrases share a stretch of sound.
        for (p0, p1), span in zip(_best_split(weight, length), spans):
            out += _spread([u for p in phrases[p0:p1] for u in p + [" "]], *span)
    else:
        # Pauses inside a phrase (a breath, a stop consonant): a phrase spans several.
        for (s0, s1), p in zip(_best_split(length, weight), phrases):
            out += _spread_over(p, spans[s0:s1])
    return out


def from_timings(timings: Iterable) -> List[Tuple[float, str, float]]:
    """Kokoro's own phoneme timings -> (start, code, length)."""
    out = []
    for tm in timings:
        ph = getattr(tm, "phoneme", "")
        if ph.isspace() or (ph and ph in _PAUSE):
            code = "_"
        else:
            units = units_from_phonemes(ph)
            if not units:
                continue  # stress / length marks
            code = units[0]
        out.append((float(tm.start), code, max(0.0, float(tm.end) - float(tm.start))))
    return out


def encode(entries: Sequence[Tuple[float, str, float]], limit: int = 6000) -> Optional[str]:
    """Compact JSON for a response header; merges repeats, skips if too long."""
    merged: List[List] = []
    for t, code, d in entries:
        ms, dms = int(round(t * 1000)), max(1, int(round(d * 1000)))
        if merged and merged[-1][1] == code and abs(merged[-1][0] + merged[-1][2] - ms) <= 15:
            merged[-1][2] = ms + dms - merged[-1][0]
        else:
            merged.append([ms, code, dms])
    text = json.dumps(merged, separators=(",", ":"))
    return text if 2 < len(text) <= limit else None
