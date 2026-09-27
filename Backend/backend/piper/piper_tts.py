import json
import subprocess
import pathlib
import threading
import time
import uuid
import random
import wave
from typing import Optional, Dict

from backend.timing import stage as _timing_stage

_first_synth_call = True

# B6 P4: warm Piper daemon. Spawned lazily on first piper_tts() call, kept alive
# across calls. Amortizes the ~376ms ONNX voice load that previously hit every
# call. See backend/tests/probe_piper_json.py for the persistence verification.
#
# Per-call length_scale/noise_scale/noise_w become no-ops on the warm path:
# piper.exe --json-input bakes those at spawn time (verified empirically — see
# probe_piper_json_params.py). The schema defaults from backend/api/schemas.py
# (1.0 / 0.6 / 0.8) are baked in here to match current production behavior.
_WARM_LENGTH_SCALE = 1.0
_WARM_NOISE_SCALE = 0.6
_WARM_NOISE_W = 0.8

_warm_proc: Optional[subprocess.Popen] = None
_warm_lock = threading.Lock()

# ================================================
# PATHS
# ================================================
BASE_DIR = pathlib.Path(__file__).parent
PIPER_EXE = BASE_DIR / "piper.exe"

# Your existing anime-ish English model
MODEL_PATH = BASE_DIR / "models" / "en_GB-jenny_dioco-medium.onnx"
MODEL_JSON = BASE_DIR / "models" / "en_GB-jenny_dioco-medium.onnx.json"

# Fallback output dir (server.py usually provides output_path)
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# ================================================
# ANIME SOUND LIBRARIES (PG-13)
# ================================================
# B6: banks tuned for mature-anime-woman persona. Moe/childlike tokens removed,
# stutters dropped, kawaii laughs replaced with warm adult exhales.
BREATH_SOUNDS = [
    "*haa...*",
    "*hnn...*",
    "*hmm*",
    "*nhh...*",
]

# B6: emptied — every entry was an explicit moe-shy stutter ("u-uhm", "s-sorry",
# "C-Creator"). add_anime_fillers handles empty banks via the existing
# `if not inserts` early-return at line 128.
SHY_SOUNDS: list[str] = []

# B6: replaced kawaii laughs (hehe/eheh/teehee) with warm adult versions.
HAPPY_SOUNDS = [
    "*ah*",
    "*ha*",
    "*mm-hmm*",
    "*mmm~*",
]

SAD_SOUNDS = [
    "*haa...*",
    "*mm... sorry...*",
    "*nh...*",
]

ANGRY_SOUNDS = [
    "*tch*",
    "*hnn!*",
]

GASPS = [
    "*ah!*",
    "*hnn!*",
    "*hah!*",
]

HUMS = [
    "*mm~*",
    "*hmm~*",
    "*mnh~*",
]

WHISPER_TAGS = [
    "*whispering*",
    "*speaking softly*",
    "*voice a bit quiet*",
]

# B6: emptied — every entry was explicitly moe-flustered ("w-wait", "n-no",
# "s-s-so close"). Mature voice doesn't fluster.
FLUSTERED_NOISES: list[str] = []

HEARTBEATS = [
    "*thump...*",
    "*thump-thump...*",
    "*ba-dump...*",
]

# ================================================
# TEXT EFFECT HELPERS
# ================================================

def add_moe_stutter(text: str, intensity: float) -> str:
    if intensity < 0.20:
        return text

    words = text.split()
    new_words = []

    for w in words:
        if len(w) > 2 and random.random() < (0.10 * intensity):
            new_words.append(f"{w[0]}-{w}")
        else:
            new_words.append(w)

    return " ".join(new_words)


def add_anime_fillers(text: str, emotion: str, intensity: float, style: str) -> str:
    inserts = []

    if emotion in ("shy", "flustered") or style == "moe":
        inserts = SHY_SOUNDS + BREATH_SOUNDS + FLUSTERED_NOISES

    elif emotion == "happy" or style == "genki":
        inserts = HAPPY_SOUNDS + HUMS

    elif emotion == "sad":
        inserts = SAD_SOUNDS

    elif emotion == "angry":
        inserts = ANGRY_SOUNDS

    if random.random() < 0.08 * (0.5 + intensity):
        inserts += BREATH_SOUNDS

    if not inserts or intensity < 0.12:
        return text

    if random.random() < 0.5:
        return random.choice(inserts) + " " + text

    words = text.split()
    if len(words) > 4 and random.random() < 0.4:
        idx = random.randint(1, len(words) - 2)
        words.insert(idx, random.choice(inserts))
        return " ".join(words)

    return text


def add_whisper_tags(text: str, emotion: str, intensity: float, style: str) -> str:
    # B6: dropped style=="moe" trigger; restricted to intimate/soft emotions
    # for the mature-anime-woman persona.
    if emotion in ("affectionate", "sad") and intensity > 0.45:
        if random.random() < 0.4:
            return random.choice(WHISPER_TAGS) + " " + text
    return text


def add_heartbeat_effect(text: str, emotion: str, intensity: float, style: str) -> str:
    # B6: tightened trigger — affectionate + high intensity only. Heartbeat is
    # an intentional flourish, not a default for any shy/happy reply.
    if emotion == "affectionate" and intensity > 0.6:
        if random.random() < 0.35:
            return random.choice(HEARTBEATS) + " " + text
    return text


def add_anime_breath_trail(text: str, emotion: str, intensity: float, style: str) -> str:
    if intensity > 0.35 and emotion in ["shy", "sad", "happy"]:
        if random.random() < 0.5:
            return text + " " + random.choice(BREATH_SOUNDS)

    if style == "moe" and random.random() < 0.3:
        return text + " " + random.choice(BREATH_SOUNDS)

    return text


def add_micro_pauses(text: str, style: str, intensity: float) -> str:
    if intensity < 0.2 and style == "neutral":
        return text

    words = text.split()
    if len(words) < 6:
        return text

    chance = 0.08 + intensity * 0.12
    i = 0
    while i < len(words) - 3:
        if random.random() < chance:
            words.insert(i + 1, "...")
            i += 2
        else:
            i += 1

    return " ".join(words)


# ================================================
# STYLE / EMOTION MAPPING
# ================================================

def _auto_detect_style_from_text(text: str) -> str:
    # B6: removed "sorry", "thank you", "um", "uhm", "uh" from the moe trigger.
    # Polite/apologetic phrases are not moe — they're normal mature speech.
    lowered = text.lower()

    if any(x in lowered for x in ["!", "!!", "so excited", "let's go", "yay", "woo"]):
        return "genki"

    if any(x in lowered for x in ["i'm fine", "it's okay", "no problem", "as you wish"]):
        return "cool"

    if any(x in lowered for x in ["embarrassed"]):
        return "moe"

    return "neutral"


def _resolve_style_and_emotion(
    text: str,
    style: Optional[str],
    emotion: str,
    intensity: float,
) -> Dict[str, object]:

    style = (style or "").strip().lower()
    emotion = (emotion or "neutral").strip().lower()
    intensity = max(0.0, min(1.0, intensity))

    if style in ("moe", "genki", "cool", "neutral"):
        final_style = style
    else:
        final_style = _auto_detect_style_from_text(text)

    if emotion == "neutral":
        if final_style == "moe":
            emotion = "shy"
        elif final_style == "genki":
            emotion = "happy"
        elif final_style == "cool":
            emotion = "calm"

    if intensity == 0.0 and any(ch in text for ch in ["!", "?!", "!!"]):
        intensity = 0.4

    return {
        "style": final_style,
        "emotion": emotion,
        "intensity": intensity,
    }


# ================================================
# EMOTION → PIPER PARAMS
# ================================================

def _base_params() -> Dict[str, float]:
    return {
        "length_scale": 1.35,     # ⬅ slower anime speech
        "noise_scale": 0.40,
        "noise_w": 0.45,
        "phoneme_length": 0.80,   # kept for future use
        "sentence_silence": 0.06, # kept for future use
    }


def get_voice_params(
    style: str,
    emotion: str,
    intensity: float,
    rate: float,
    pitch: float,
) -> Dict[str, float]:

    params = _base_params()
    intensity = max(0.0, min(1.0, intensity))

    if style == "moe":
        params["length_scale"] -= 0.10 * (0.5 + intensity)
        params["noise_scale"] -= 0.05 * (0.5 + intensity)
        params["phoneme_length"] -= 0.08 * (0.3 + intensity)
        params["noise_w"] -= 0.05 * intensity

    elif style == "genki":
        params["length_scale"] -= 0.12 * (0.4 + intensity)
        params["noise_scale"] += 0.10 * (0.3 + intensity)
        params["noise_w"] += 0.06 * intensity

    elif style == "cool":
        params["length_scale"] += 0.05 * (0.3 + intensity)
        params["phoneme_length"] += 0.05 * (0.4 + intensity)
        params["noise_scale"] -= 0.04 * intensity

    if emotion == "shy":
        params["length_scale"] -= 0.08 * intensity
        params["noise_scale"] -= 0.08 * intensity
        params["phoneme_length"] -= 0.10 * intensity

    elif emotion == "happy":
        params["length_scale"] -= 0.05 * intensity
        params["noise_scale"] += 0.06 * intensity

    elif emotion == "sad":
        params["length_scale"] += 0.10 * intensity
        params["phoneme_length"] += 0.10 * intensity
        params["noise_scale"] += 0.04 * intensity
        params["noise_w"] -= 0.03 * intensity

    elif emotion == "angry":
        params["length_scale"] -= 0.03 * intensity
        params["noise_scale"] += 0.10 * intensity
        params["noise_w"] += 0.08 * intensity

    # Rate → speed multiplier
    if rate > 0:
        params["length_scale"] /= max(0.5, min(2.0, rate))

    # Pitch → mild shading
    pitch = max(0.5, min(2.0, pitch))
    params["noise_w"] *= 0.8 + 0.2 * pitch

    return params


# ================================================
# MAIN TTS FUNCTION (MATCHES server.py)
# ================================================

def _ensure_paths_ok():
    if not PIPER_EXE.exists():
        raise FileNotFoundError(f"Piper executable not found at: {PIPER_EXE}")
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Piper model not found at: {MODEL_PATH}")


def _spawn_warm_proc() -> subprocess.Popen:
    cmd = [
        str(PIPER_EXE),
        "--model", str(MODEL_PATH),
        "--length_scale", str(_WARM_LENGTH_SCALE),
        "--noise_scale", str(_WARM_NOISE_SCALE),
        "--noise_w", str(_WARM_NOISE_W),
        "--volume", "0.4",
        "--json-input",
        "--quiet",
    ]
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )


def _ensure_warm_proc():
    global _warm_proc
    if _warm_proc is None or _warm_proc.poll() is not None:
        _warm_proc = _spawn_warm_proc()


def prewarm() -> None:
    """Spawn the persistent piper daemon now so the first user call is fast.

    Safe to call from app startup. Returns immediately after spawning — the
    ~376ms ONNX voice load happens asynchronously inside piper while the
    backend continues booting.
    """
    _ensure_paths_ok()
    with _warm_lock:
        _ensure_warm_proc()


def cleanup_warm_proc() -> None:
    """Tear down the persistent piper daemon. Called from app shutdown."""
    global _warm_proc
    with _warm_lock:
        if _warm_proc is None:
            return
        try:
            if _warm_proc.poll() is None:
                try:
                    _warm_proc.stdin.close()
                except Exception:
                    pass
                try:
                    _warm_proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    _warm_proc.terminate()
                    try:
                        _warm_proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        _warm_proc.kill()
        finally:
            _warm_proc = None


def _synth_via_warm(text: str, wav_path: pathlib.Path) -> None:
    """Single roundtrip on the persistent piper daemon.

    Caller must hold _warm_lock. Writes one JSON line, reads the WAV-path ack
    line from stdout, then verifies the WAV is fully flushed (tight poll, 100ms
    ceiling) so HTTP callers don't race piper's file write.
    """
    assert _warm_proc is not None
    req = {"text": text, "output_file": str(wav_path)}
    _warm_proc.stdin.write(json.dumps(req) + "\n")
    _warm_proc.stdin.flush()
    ack = _warm_proc.stdout.readline()
    if not ack:
        raise RuntimeError("piper warm daemon closed stdout")

    deadline = time.perf_counter() + 0.1
    while time.perf_counter() < deadline:
        try:
            with wave.open(str(wav_path), "rb") as w:
                if w.getnframes() > 0:
                    return
        except (wave.Error, FileNotFoundError, EOFError):
            pass
        time.sleep(0.005)
    raise RuntimeError(f"piper output did not flush within 100ms: {wav_path}")


def piper_tts(
    text: str,
    speaker: str = "default",
    output_path: Optional[str] = None,
    rate: float = 1.0,
    pitch: float = 1.0,
    breathiness: float = 0.0,
    flanger: float = 0.0,
    reverb: float = 0.0,
    style: Optional[str] = None,
    emotion: str = "neutral",
    intensity: float = 0.0,
    # 🔥 NEW: keep compatibility with server.py + dashboard.js
    length_scale: Optional[float] = None,
    noise_scale: Optional[float] = None,
    noise_w: Optional[float] = None,
) -> str:
    """
    Main Piper TTS wrapper used by Sarah AI.

    NOTE:
    - server.py calls this with length_scale / noise_scale / noise_w
    - dashboard.js tunes those values per emotion
    """

    _ensure_paths_ok()

    if not text or not text.strip():
        raise ValueError("piper_tts: text is empty.")

    text = text.strip()

    style_info = _resolve_style_and_emotion(
        text=text,
        style=style,
        emotion=emotion,
        intensity=intensity,
    )
    final_style = style_info["style"]
    final_emotion = style_info["emotion"]
    final_intensity = style_info["intensity"]

    # TEXT ADD-ONS
    # B6: add_moe_stutter removed from pipeline — letter+hyphen stutters are
    # explicitly moe and don't fit the mature-anime-woman persona. The function
    # itself is left defined in case a future persona wants to opt back in.
    modified = text
    modified = add_anime_fillers(modified, final_emotion, final_intensity, final_style)
    modified = add_heartbeat_effect(modified, final_emotion, final_intensity, final_style)
    modified = add_anime_breath_trail(modified, final_emotion, final_intensity, final_style)
    modified = add_whisper_tags(modified, final_emotion, final_intensity, final_style)
    modified = add_micro_pauses(modified, final_style, final_intensity)

    # OUTPUT PATH
    if output_path:
        wav_path = pathlib.Path(output_path)
    else:
        wav_path = OUTPUT_DIR / f"sarah_{uuid.uuid4().hex}.wav"

    wav_path.parent.mkdir(parents=True, exist_ok=True)

    # VOICE PARAMETERS (base + overrides from caller)
    params = get_voice_params(
        style=final_style,
        emotion=final_emotion,
        intensity=final_intensity,
        rate=rate,
        pitch=pitch,
    )

    if length_scale is not None:
        params["length_scale"] = float(length_scale)
    if noise_scale is not None:
        params["noise_scale"] = float(noise_scale)
    if noise_w is not None:
        params["noise_w"] = float(noise_w)

    # B6 P4: warm-daemon path. Per-call length_scale/noise_scale/noise_w params
    # are no longer plumbed into piper — `--json-input` bakes them at spawn
    # time. The daemon uses _WARM_* constants matching schema defaults.
    _ = params  # kept for diagnostic readability; not sent to piper anymore

    global _first_synth_call
    cold = _first_synth_call
    _first_synth_call = False

    with _timing_stage("tts.synth", cold=cold, text_len=len(modified), path="warm"):
        with _warm_lock:
            _ensure_warm_proc()
            try:
                _synth_via_warm(modified, wav_path)
            except (BrokenPipeError, RuntimeError):
                # Daemon died mid-call — respawn once and retry.
                global _warm_proc
                _warm_proc = None
                _ensure_warm_proc()
                _synth_via_warm(modified, wav_path)

    return str(wav_path)
