"""Lip-sync timelines: phonemes/spelling -> mouth shapes timed to the audio."""
import json
from types import SimpleNamespace

import numpy as np

from backend import tts_kokoro, visemes


def _speech(parts, sr=24000):
    """Bursts of tone (sound) and zeros (pauses): [(seconds, loud?), ...]."""
    out = []
    for secs, loud in parts:
        n = int(secs * sr)
        out.append(np.sin(np.arange(n) * 0.05).astype(np.float32) * 0.5 if loud else np.zeros(n, np.float32))
    return np.concatenate(out), sr


def test_units_from_kokoro_phonemes():
    # "hello, world" in espeak IPA
    assert visemes.units_from_phonemes("həlˈoʊ, wˈɜːld") == ["c", "e", "c", "o", "u", "|", "u", "e", "c", "c"]
    assert visemes.units_from_phonemes("mˈaɪ bˈɔɪ") == ["m", "a", "i", " ", "m", "o", "i"]


def test_units_from_spelling():
    u = visemes.units_from_text("Moon, beep!")
    assert u[0] == "m" and "u" in u and "|" in u and "i" in u and u[-1] != "|"


def test_voiced_spans_split_at_pauses():
    samples, sr = _speech([(0.1, False), (0.5, True), (0.25, False), (0.4, True), (0.1, False)])
    spans = visemes.voiced_spans(samples, sr)
    assert len(spans) == 2
    assert abs(spans[0][0] - 0.1) < 0.03 and abs(spans[1][1] - 1.25) < 0.03


def test_align_puts_phrases_in_their_own_sound():
    samples, sr = _speech([(0.1, False), (0.5, True), (0.25, False), (0.4, True)])
    entries = visemes.align(visemes.units_from_phonemes("hˈaɪ, ðˈɛɹ"), samples, sr)
    first = [e for e in entries if e[0] < 0.7]
    second = [e for e in entries if e[0] >= 0.7]
    assert first and second
    assert all(0.08 <= t <= 0.62 for t, _, _ in first)       # "hai" inside the first burst
    assert all(0.83 <= t <= 1.27 for t, _, _ in second)      # "ðɛɹ" inside the second
    assert "a" in [c for _, c, _ in first] and "e" in [c for _, c, _ in second]


def test_encode_is_compact_and_capped():
    enc = visemes.encode([(0.0, "a", 0.1), (0.1, "a", 0.05), (0.15, "m", 0.08)])
    assert json.loads(enc) == [[0, "a", 150], [150, "m", 80]]
    assert visemes.encode([(i * 0.01, "aeiou"[i % 5], 0.01) for i in range(5000)]) is None
    assert visemes.encode([]) is None


def test_kokoro_timings_used_when_the_model_has_them(monkeypatch):
    class Fake:
        def create_timed(self, text, voice, speed, lang):
            T = SimpleNamespace
            return np.zeros(4800, np.float32), 24000, [T(phoneme="h", start=0.0, end=0.05),
                                                       T(phoneme="a", start=0.05, end=0.2),
                                                       T(phoneme=" ", start=0.2, end=0.22)]

    monkeypatch.setattr(tts_kokoro, "_load", lambda: Fake())
    wav, entries = tts_kokoro.synthesize_timed("ha")
    assert wav[:4] == b"RIFF"
    assert [(c, round(t, 2)) for t, c, _ in entries] == [("c", 0.0), ("a", 0.05), ("_", 0.2)]


def test_kokoro_without_timings_aligns_its_phonemes(monkeypatch):
    samples, sr = _speech([(0.05, False), (0.6, True), (0.05, False)])

    class Fake:
        tokenizer = SimpleNamespace(phonemize=lambda text, lang: "mˈuːn")

        def create(self, text, voice, speed, lang):
            return samples, sr

    monkeypatch.setattr(tts_kokoro, "_load", lambda: Fake())
    _, entries = tts_kokoro.synthesize_timed("moon")
    assert [c for _, c, _ in entries] == ["m", "u", "c"]
    assert 0.03 <= entries[0][0] <= 0.08 and entries[-1][0] + entries[-1][2] <= 0.68
