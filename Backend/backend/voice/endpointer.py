"""Streaming voice activity detection and turn endpointing.

The renderer streams microphone audio continuously (16 kHz mono PCM, echo
cancelled in the browser). ``StreamingVAD`` scores each 32 ms chunk with the
Silero model that ships inside faster-whisper, carrying the model's state
between chunks. ``Endpointer`` turns those scores into conversation events:

- ``speech_start``   someone started talking (her body turns to listen; if
                     she is speaking this is a barge-in)
- ``partial_due``    time to transcribe the speech so far (live captions)
- ``utterance``      the turn ended: here is the audio to transcribe
- ``speech_cancel``  it was only a blip (cough, click)

While Sarah is talking (``speaking=True``) starting needs a louder, longer
voice, so leftovers of her own voice that the echo canceller missed don't
interrupt her.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, List, Optional, Tuple

import numpy as np

SAMPLE_RATE = 16000
CHUNK = 512                         # samples per VAD step (32 ms)
CHUNK_MS = CHUNK * 1000 // SAMPLE_RATE


class StreamingVAD:
    """Silero VAD (faster-whisper's bundled ONNX) one chunk at a time."""

    CONTEXT = 64

    def __init__(self) -> None:
        from faster_whisper.vad import get_vad_model

        self._session = get_vad_model().session
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, self.CONTEXT), dtype=np.float32)

    def __call__(self, chunk: np.ndarray) -> float:
        x = np.concatenate([self._context, chunk.reshape(1, CHUNK)], axis=1).astype(np.float32)
        out, self._h, self._c = self._session.run(None, {"input": x, "h": self._h, "c": self._c})
        self._context = x[:, -self.CONTEXT:]
        return float(np.asarray(out).ravel()[0])


@dataclass
class EndpointConfig:
    start_prob: float = 0.5
    stop_prob: float = 0.35
    start_ms: int = 128              # voiced this long -> speech_start (clicks span <=3 chunks)
    guard_prob: float = 0.8          # ...while she is speaking (barge-in)
    guard_start_ms: int = 256
    end_silence_ms: int = 800        # silence this long ends the turn
    pause_ms: int = 416              # ...but at this point, check if the sentence is finished
    preroll_ms: int = 320            # audio kept from before the start
    min_speech_ms: int = 220         # shorter than this -> speech_cancel
    max_utterance_ms: int = 30000
    partial_every_ms: int = 900


@dataclass
class Event:
    type: str
    audio: Optional[np.ndarray] = None
    speech_ms: int = 0
    info: dict = field(default_factory=dict)


class Endpointer:
    def __init__(self, vad: Callable[[np.ndarray], float], config: Optional[EndpointConfig] = None) -> None:
        self.vad = vad
        self.cfg = config or EndpointConfig()
        self.speaking = False        # Sarah is talking (echo guard)
        self._pending = np.zeros(0, dtype=np.float32)
        self.reset()

    def reset(self) -> None:
        self.in_speech = False
        self._voiced_ms = 0
        self._silence_ms = 0
        self._speech_ms = 0
        self._since_partial = 0
        self._pause_sent = False
        self.pause_serial = 0        # bumps whenever speech resumes after a pause
        self._pre: Deque[np.ndarray] = deque(maxlen=max(1, self.cfg.preroll_ms // CHUNK_MS))
        self._utt: List[np.ndarray] = []
        if hasattr(self.vad, "reset"):
            self.vad.reset()

    def current_audio(self) -> np.ndarray:
        return np.concatenate(self._utt) if self._utt else np.zeros(0, dtype=np.float32)

    def feed(self, samples: np.ndarray) -> List[Event]:
        events: List[Event] = []
        self._pending = np.concatenate([self._pending, samples.astype(np.float32, copy=False)])
        n = len(self._pending) // CHUNK
        for i in range(n):
            chunk = self._pending[i * CHUNK:(i + 1) * CHUNK]
            events.extend(self._step(chunk, self.vad(chunk)))
        self._pending = self._pending[n * CHUNK:]
        return events

    def _step(self, chunk: np.ndarray, prob: float) -> List[Event]:
        cfg = self.cfg
        if not self.in_speech:
            self._pre.append(chunk)
            threshold = cfg.guard_prob if self.speaking else cfg.start_prob
            need = cfg.guard_start_ms if self.speaking else cfg.start_ms
            self._voiced_ms = self._voiced_ms + CHUNK_MS if prob >= threshold else 0
            if self._voiced_ms >= need:
                self.in_speech = True
                self._utt = list(self._pre)
                self._speech_ms = self._voiced_ms
                self._silence_ms = 0
                self._since_partial = 0
                return [Event("speech_start", info={"barge_in": self.speaking})]
            return []

        self._utt.append(chunk)
        self._since_partial += CHUNK_MS
        if prob < cfg.stop_prob:
            self._silence_ms += CHUNK_MS
        else:
            if self._pause_sent:
                self.pause_serial += 1   # they kept talking: that pause is void
            self._pause_sent = False
            self._silence_ms = 0
            self._speech_ms += CHUNK_MS
        total_ms = len(self._utt) * CHUNK_MS

        if self._silence_ms >= cfg.end_silence_ms or total_ms >= cfg.max_utterance_ms:
            return [self._finish()]
        if self._silence_ms >= cfg.pause_ms and not self._pause_sent and self._speech_ms >= cfg.min_speech_ms:
            self._pause_sent = True
            return [Event("pause", speech_ms=self._speech_ms, info={"serial": self.pause_serial})]
        if self._since_partial >= cfg.partial_every_ms and self._speech_ms >= 500:
            self._since_partial = 0
            return [Event("partial_due", speech_ms=self._speech_ms)]
        return []

    def paused_since(self, serial: int) -> bool:
        """Still in the same pause (no speech since `serial` was issued)."""
        return self.in_speech and self._pause_sent and self.pause_serial == serial

    def force_end(self) -> Event:
        """End the turn now (the sentence was clearly finished)."""
        return self._finish()

    def _finish(self) -> Event:
        # Keep a little of the trailing silence; Whisper likes a clean ending.
        keep_tail = max(0, (self._silence_ms - 200) // CHUNK_MS)
        chunks = self._utt[: len(self._utt) - keep_tail] if keep_tail else self._utt
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        speech_ms = self._speech_ms
        self.in_speech = False
        self._pause_sent = False
        self._voiced_ms = 0
        self._utt = []
        self._pre.clear()
        if speech_ms < self.cfg.min_speech_ms:
            return Event("speech_cancel", speech_ms=speech_ms)
        return Event("utterance", audio=audio, speech_ms=speech_ms)


# Whisper's classic inventions on noise / breaths.
_HALLUCINATIONS = {
    "you", "thank you", "thank you.", "thanks for watching", "thanks for watching!",
    "bye", "bye.", ".", "so", "hmm", "uh", "um", "okay.", "the end", "subscribe",
}


# Whisper ends almost everything with a period, even half a sentence, so a
# period alone doesn't mean they're done: not when the last word is one that
# leads into more ("I want to go to the...", "it's for", "and").
_LEADS_ON = {
    "and", "but", "or", "so", "because", "cause", "for", "to", "the", "a", "an", "of", "with", "in", "on", "at",
    "if", "that", "like", "my", "your", "our", "their", "his", "her", "is", "was", "are", "were", "then", "um",
    "uh", "just", "about", "from", "into", "when", "which", "who", "as", "than", "what", "where", "how", "i",
    "we", "you", "it's", "i'm", "gonna", "wanna", "maybe", "also", "plus", "well", "by", "since", "until",
}


def sounds_finished(text: str) -> bool:
    """Does this read like the end of what they meant to say?"""
    import re

    t = (text or "").strip()
    if not t or t.endswith(("...", "…", ",", "-", "—")):
        return False
    if not re.search(r"[.?!][\"')\]]*$", t):
        return False
    if t.endswith(("?", "!")):
        return True
    words = re.findall(r"[a-z']+", t.lower())
    return len(words) >= 2 and words[-1] not in _LEADS_ON


def plausible(text: str, speech_ms: int) -> bool:
    t = (text or "").strip().lower()
    if not t or not any(ch.isalnum() for ch in t):
        return False
    if speech_ms < 900 and t.strip(" .!?,") in {h.strip(" .!?,") for h in _HALLUCINATIONS}:
        return False
    return True
