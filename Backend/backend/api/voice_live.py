"""Live voice over a WebSocket.

The renderer streams echo-cancelled microphone audio as binary frames
(16 kHz mono int16). Text frames carry control messages:

    {"type": "tts", "speaking": true|false}   Sarah is (not) talking
    {"type": "reset"}                          drop any half-heard turn

The server answers with JSON events:

    {"type": "ready", "device": "cuda", "model": "large-v3-turbo"}
    {"type": "speech_start", "barge_in": bool}
    {"type": "partial", "text": "..."}
    {"type": "final", "text": "...", "speech_ms": n, "stt_ms": n}
    {"type": "speech_cancel"}

Transcription runs in a worker thread so listening never stops; finals are
serialised so they arrive in order.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.config import settings
from backend.voice.endpointer import EndpointConfig, Endpointer, StreamingVAD, plausible, sounds_finished

router = APIRouter()
logger = logging.getLogger("sarah.voice")


def _authorised(ws: WebSocket) -> bool:
    expected = settings.api_token
    if not expected:
        return True
    return hmac.compare_digest(ws.headers.get("x-sarah-token", ""), expected)


@router.websocket("/ws/voice")
async def ws_voice(ws: WebSocket):
    if not _authorised(ws):
        await ws.close(code=4401)
        return
    await ws.accept()

    from backend.whisper_stt import get_whisper_stt

    try:
        stt = await asyncio.to_thread(get_whisper_stt)
    except Exception as exc:
        await ws.send_json({"type": "error", "detail": f"speech recognition unavailable: {exc}"})
        await ws.close()
        return

    endpointer = Endpointer(StreamingVAD(), EndpointConfig(end_silence_ms=settings.voice_end_silence_ms))
    stt_lock = asyncio.Lock()
    partial_busy = False
    # Stretches of speech are numbered: a transcript that comes back after
    # they already started talking again is part of a longer thought
    # ("more_coming"), so the app waits and joins them into one turn.
    started = 0
    blips: set = set()   # stretches that turned out to be a cough/click

    def more_after(seq: int) -> bool:
        return any(s not in blips for s in range(seq + 1, started + 1))
    tasks: set = set()
    await ws.send_json({"type": "ready", "device": stt.device, "model": stt.model_size})
    logger.info("[VOICE] live session open (%s on %s)", stt.model_size, stt.device)

    async def send(payload: dict) -> None:
        try:
            await ws.send_json(payload)
        except Exception:
            pass

    async def partial(audio: np.ndarray) -> None:
        nonlocal partial_busy
        try:
            async with stt_lock:
                text = await asyncio.to_thread(stt.transcribe_array, audio)
            if text and endpointer.in_speech:
                await send({"type": "partial", "text": text})
        finally:
            partial_busy = False

    async def final(audio: np.ndarray, speech_ms: int, seq: int) -> None:
        t0 = time.perf_counter()
        async with stt_lock:
            text = await asyncio.to_thread(stt.transcribe_array, audio)
        stt_ms = int((time.perf_counter() - t0) * 1000)
        more = more_after(seq)  # they spoke again after this part ended
        if plausible(text, speech_ms):
            logger.info("[VOICE] heard (%d ms speech, stt %d ms%s): %s", speech_ms, stt_ms,
                        ", more coming" if more else "", text)
            await send({"type": "final", "text": text, "speech_ms": speech_ms, "stt_ms": stt_ms,
                        "finished": sounds_finished(text), "more_coming": more})
        else:
            blips.add(seq)
            await send({"type": "speech_cancel", "reason": "noise", "text": text, "more_coming": more})

    async def early_end(serial: int, speech_ms: int, seq: int) -> None:
        """Semantic endpointing: a short pause after what reads as a finished
        sentence ends the turn now, reusing this transcript (saves the rest
        of the silence wait and a second transcription)."""
        t0 = time.perf_counter()
        async with stt_lock:
            if not endpointer.paused_since(serial):
                return
            audio = endpointer.current_audio()
            text = await asyncio.to_thread(stt.transcribe_array, audio)
        if not endpointer.paused_since(serial):
            return  # they went on talking, or the normal endpoint already fired
        if not sounds_finished(text) or not plausible(text, speech_ms):
            return
        endpointer.force_end()
        stt_ms = int((time.perf_counter() - t0) * 1000)
        logger.info("[VOICE] heard early (%d ms speech, stt %d ms): %s", speech_ms, stt_ms, text)
        await send({"type": "final", "text": text, "speech_ms": speech_ms, "stt_ms": stt_ms, "early": True,
                    "finished": True, "more_coming": more_after(seq)})

    def spawn(coro) -> None:
        task = asyncio.create_task(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    try:
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data:
                samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                for event in endpointer.feed(samples):
                    if event.type == "speech_start":
                        started += 1
                        await send({"type": "speech_start", **event.info})
                    elif event.type == "partial_due" and not partial_busy:
                        partial_busy = True
                        spawn(partial(endpointer.current_audio()))
                    elif event.type == "pause":
                        spawn(early_end(event.info["serial"], event.speech_ms, started))
                    elif event.type == "utterance":
                        spawn(final(event.audio, event.speech_ms, started))
                    elif event.type == "speech_cancel":
                        blips.add(started)
                        await send({"type": "speech_cancel", "more_coming": False})
                continue
            text = message.get("text")
            if text:
                try:
                    control = json.loads(text)
                except ValueError:
                    continue
                if control.get("type") == "tts":
                    endpointer.speaking = bool(control.get("speaking"))
                elif control.get("type") == "reset":
                    endpointer.reset()
    except WebSocketDisconnect:
        pass
    finally:
        for task in list(tasks):
            task.cancel()
        logger.info("[VOICE] live session closed")
