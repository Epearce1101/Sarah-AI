"""Live voice: turn endpointing and the /ws/voice protocol."""
import json

import numpy as np
import pytest

from backend.voice.endpointer import CHUNK, EndpointConfig, Endpointer, StreamingVAD, plausible

SR = 16000


def energy_vad(chunk):
    return 0.9 if float(np.sqrt(np.mean(chunk ** 2))) > 0.05 else 0.05


def tone(ms, amp=0.3):
    n = SR * ms // 1000
    return (np.sin(2 * np.pi * 200 * np.arange(n) / SR) * amp).astype(np.float32)


def silence(ms):
    return np.zeros(SR * ms // 1000, dtype=np.float32)


def run(ep, *pieces):
    events = []
    for p in pieces:
        # Arrive in odd-sized frames like a real audio callback.
        for i in range(0, len(p), 700):
            events += ep.feed(p[i:i + 700])
    return events


def test_a_turn_starts_and_ends():
    ep = Endpointer(energy_vad)
    events = run(ep, silence(400), tone(1500), silence(800))
    kinds = [e.type for e in events]
    assert kinds[0] == "speech_start" and kinds[-1] == "utterance"
    assert "partial_due" in kinds  # long enough for a live caption
    utt = events[-1]
    assert 1400 <= utt.speech_ms <= 1600
    # Pre-roll keeps the very start of the word; the tail keeps a little silence.
    assert len(utt.audio) >= SR * 1.5 and len(utt.audio) <= SR * (1.5 + 0.32 + 0.3)
    assert not ep.in_speech


def test_blips_and_short_noises_are_ignored():
    ep = Endpointer(energy_vad)
    assert run(ep, silence(200), tone(64), silence(800)) == []
    events = run(ep, tone(160), silence(800))
    assert [e.type for e in events] == ["speech_start", "speech_cancel"]


def test_pauses_shorter_than_the_endpoint_keep_the_turn_open():
    ep = Endpointer(energy_vad, EndpointConfig(end_silence_ms=550))
    events = run(ep, tone(600), silence(300), tone(600), silence(700))
    kinds = [e.type for e in events if e.type != "partial_due"]
    assert kinds == ["speech_start", "pause", "pause", "utterance"]
    # The first pause was voided when speech resumed.
    first, second = [e for e in events if e.type == "pause"]
    assert second.info["serial"] == first.info["serial"] + 1


def test_pause_can_end_the_turn_early():
    ep = Endpointer(energy_vad)
    events = run(ep, tone(800), silence(290))
    pause = [e for e in events if e.type == "pause"][-1]
    assert ep.paused_since(pause.info["serial"])
    utt = ep.force_end()
    assert utt.type == "utterance" and not ep.in_speech
    assert not ep.paused_since(pause.info["serial"])
    assert run(ep, silence(600)) == []  # no second end for the same turn


def test_echo_guard_while_she_speaks():
    ep = Endpointer(energy_vad)
    ep.speaking = True
    assert run(ep, tone(180), silence(600)) == []  # a leftover of her own voice
    events = run(ep, tone(500), silence(700))
    assert events[0].type == "speech_start" and events[0].info["barge_in"] is True


def test_plausibility_filter():
    assert not plausible("Thank you.", 500)
    assert plausible("Thank you.", 1500)
    assert not plausible(" ... ", 2000)
    assert plausible("What's on my screen?", 800)


def test_real_silero_stream_is_quiet_on_silence():
    vad = StreamingVAD()
    probs = [vad(np.zeros(CHUNK, dtype=np.float32)) for _ in range(10)]
    assert max(probs) < 0.2


@pytest.mark.parametrize("heard, early", [("hello sarah", False), ("What's for dinner?", True)])
def test_websocket_protocol(monkeypatch, heard, early):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import backend.api.voice_live as voice_live
    import backend.voice.endpointer as endpointer_mod
    import backend.whisper_stt as whisper_stt

    class FakeSTT:
        device, model_size = "cpu", "fake"

        def transcribe_array(self, audio, prompt=None):
            return heard

    monkeypatch.setattr(whisper_stt, "get_whisper_stt", lambda: FakeSTT())

    class FakeVAD:
        def __call__(self, chunk):
            return energy_vad(chunk)

        def reset(self):
            pass

    monkeypatch.setattr(voice_live, "StreamingVAD", FakeVAD)
    monkeypatch.setattr(voice_live.settings.__class__, "api_token", property(lambda self: ""), raising=False)

    app = FastAPI()
    app.include_router(voice_live.router)
    to_pcm = lambda a: (a * 32767).astype(np.int16).tobytes()
    with TestClient(app).websocket_connect("/ws/voice") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_text(json.dumps({"type": "tts", "speaking": False}))
        for piece in (silence(300), tone(1200), silence(800)):
            for i in range(0, len(piece), 1024):
                ws.send_bytes(to_pcm(piece[i:i + 1024]))
        seen = []
        while not seen or seen[-1]["type"] != "final":
            seen.append(ws.receive_json())
        assert seen[0]["type"] == "speech_start"
        assert seen[-1]["text"] == heard and seen[-1]["speech_ms"] >= 1100
        # A finished sentence ends at the pause; an unfinished one waits.
        assert bool(seen[-1].get("early")) is early
        assert [m["type"] for m in seen].count("final") == 1
