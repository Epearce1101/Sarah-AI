"""Her Kokoro voice through the real /api/tts endpoint."""
import io

import pytest
import soundfile as sf
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import tts_kokoro


@pytest.mark.skipif(not (tts_kokoro.settings.models_dir / "kokoro" / "kokoro-v1.0.onnx").exists(),
                    reason="Kokoro model not installed")
def test_api_tts_speaks_with_kokoro():
    from backend.api.tts import router

    app = FastAPI()
    app.include_router(router)
    res = TestClient(app).post("/api/tts", json={"text": "Hi, it's Sarah."})
    assert res.status_code == 200 and res.headers["content-type"] == "audio/wav"
    data, sr = sf.read(io.BytesIO(res.content))
    assert sr == 24000 and 0.5 < len(data) / sr < 4
    assert tts_kokoro.voice() == "af_bella"


def test_speed_follows_length_scale(monkeypatch):
    calls = []

    class Fake:
        def create(self, text, voice, speed, lang):
            calls.append((voice, round(speed, 2), lang))
            import numpy as np
            return np.zeros(2400, dtype="float32"), 24000

    monkeypatch.setattr(tts_kokoro, "_load", lambda: Fake())
    tts_kokoro.synthesize("hello", length_scale=1.25)
    tts_kokoro.synthesize("hello", length_scale=None)
    assert calls == [("af_bella", 0.8, "en-us"), ("af_bella", 1.0, "en-us")]
