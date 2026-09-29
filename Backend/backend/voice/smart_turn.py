"""Smart Turn (pipecat-ai/smart-turn v3, BSD-2): has the speaker finished?

Silence alone can't tell "I want to go to the... [thinking]" from "I'm done."
Smart Turn listens to the last 8 s of the turn itself (intonation, pace,
grammar in the sound) and gives the probability the turn is complete. It's
a small ONNX model (8.7 MB, CPU, ~10-30 ms), kept in Backend/models.

The endpointer asks it after a short silence: likely complete -> end the turn
now (fast replies); likely unfinished -> keep listening (up to a limit).
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np

logger = logging.getLogger("sarah.voice")

REPO = "pipecat-ai/smart-turn-v3"
FILE = "smart-turn-v3.2-cpu.onnx"
SAMPLE_RATE = 16000
SECONDS = 8

_lock = threading.Lock()
_session = None
_features = None
_failed = False


def _load() -> bool:
    global _session, _features, _failed
    if _session is not None or _failed:
        return _session is not None
    with _lock:
        if _session is not None or _failed:
            return _session is not None
        try:
            import onnxruntime as ort
            from faster_whisper.feature_extractor import FeatureExtractor
            from huggingface_hub import hf_hub_download

            opts = ort.SessionOptions()
            opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 2
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            _session = ort.InferenceSession(hf_hub_download(REPO, FILE), sess_options=opts,
                                            providers=["CPUExecutionProvider"])
            _features = FeatureExtractor(feature_size=80)
            probability(np.zeros(SAMPLE_RATE, dtype=np.float32))  # warm up
            logger.info("[VOICE] Smart Turn ready (%s)", FILE)
        except Exception as exc:
            _failed = True
            _session = None
            logger.warning("[VOICE] Smart Turn unavailable, using silence only: %s", exc)
    return _session is not None


def available() -> bool:
    return _load()


def probability(audio: np.ndarray) -> Optional[float]:
    """P(turn complete) for 16 kHz mono float audio, or None if unavailable."""
    if _session is None and not _load():
        return None
    a = np.asarray(audio, dtype=np.float32)[-SECONDS * SAMPLE_RATE:]
    if len(a) < SECONDS * SAMPLE_RATE:  # the model was trained with padding in front
        a = np.concatenate([np.zeros(SECONDS * SAMPLE_RATE - len(a), dtype=np.float32), a])
    a = (a - a.mean()) / np.sqrt(a.var() + 1e-7)
    feats = _features(a, padding=0)[:, : SECONDS * 100]
    if feats.shape[1] < SECONDS * 100:
        feats = np.pad(feats, ((0, 0), (SECONDS * 100 - feats.shape[1], 0)))
    out = _session.run(None, {"input_features": feats[None].astype(np.float32)})[0]
    p = float(np.asarray(out).ravel()[0])
    return p if 0.0 <= p <= 1.0 else float(1 / (1 + np.exp(-p)))  # logits on some exports
