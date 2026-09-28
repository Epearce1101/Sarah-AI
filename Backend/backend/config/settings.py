from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
BACKEND_ROOT = BACKEND_DIR.parent
REPO_ROOT = BACKEND_ROOT.parent


def _load_dotenv() -> None:
    for candidate in (REPO_ROOT / ".env", BACKEND_ROOT / ".env"):
        if not candidate.exists():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


_load_dotenv()

# Downloaded models (Whisper etc.) live inside the project, never in the
# user-profile caches on C:.
MODELS_DIR = Path(os.environ.get("SARAH_MODELS_DIR") or (BACKEND_ROOT / "models"))
os.environ.setdefault("HF_HOME", str(MODELS_DIR / "hf"))
# Her web browser (Playwright's Chromium) also lives in the project folder.
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(MODELS_DIR / "ms-playwright"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw else default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if not raw:
        return default
    values = tuple(v.strip() for v in raw.split(",") if v.strip())
    return values or default


def _path(name: str, default) -> Path:
    raw = os.environ.get(name)
    return Path(raw) if raw else Path(default)


@dataclass(frozen=True)
class Settings:
    # Secrets
    openrouter_api_key: str = field(default_factory=lambda: _str("SARAH_OPENROUTER_API_KEY") or _str("OPENROUTER_API_KEY"))
    google_api_key: str = field(default_factory=lambda: _str("SARAH_GOOGLE_API_KEY"))

    # LLM (OpenRouter — single auto-routing model for all cloud surfaces)
    openrouter_base_url: str = field(default_factory=lambda: _str("SARAH_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"))
    # openrouter/owl-alpha (the previous default) was retired by OpenRouter and
    # now 404s. Free, 1M-context, and reachable with a plain API key (unlike
    # the inkling:free models, which are restricted to approved apps).
    openrouter_model: str = field(default_factory=lambda: _str("SARAH_OPENROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free"))
    openrouter_context_window_tokens: int = field(default_factory=lambda: _int("SARAH_OPENROUTER_CONTEXT_WINDOW_TOKENS", 1000000))
    # Tried in order when the primary fails (OpenRouter allows 3 models total).
    # Both answered consistently in a 2026-09-27 probe; openrouter/free and
    # nemotron-3.5-lightning did not (safety-classifier output / leaked reasoning).
    openrouter_fallback_models: tuple[str, ...] = field(default_factory=lambda: _csv(
        "SARAH_OPENROUTER_FALLBACK_MODELS",
        ("dots-studio/dots-3-note-preview:free", "poolside/laguna-s-2.1:free"),
    ))
    # Image-capable model for screenshot/vision analysis (the chat model is
    # text-only). Used when local Ollama vision isn't available. Free; read a
    # test image correctly in a 2026-09-27 probe given enough output tokens.
    openrouter_vision_model: str = field(default_factory=lambda: _str("SARAH_OPENROUTER_VISION_MODEL", "dots-studio/dots-3-note-preview:free"))
    openrouter_reasoning_effort: str = field(default_factory=lambda: _str("SARAH_OPENROUTER_REASONING_EFFORT", "high"))
    llm_max_completion_tokens: int = field(default_factory=lambda: _int("SARAH_LLM_MAX_COMPLETION_TOKENS", 4096))
    llm_temperature: float = field(default_factory=lambda: _float("SARAH_LLM_TEMPERATURE", 0.7))

    # Ollama
    ollama_base_url: str = field(default_factory=lambda: _str("SARAH_OLLAMA_BASE_URL", "http://localhost:11434"))
    ollama_vision_model: str = field(default_factory=lambda: _str("SARAH_OLLAMA_VISION_MODEL", "qwen3-vl:8b"))
    default_local_model: str = field(default_factory=lambda: _str("SARAH_LOCAL_MODEL", "jessie:latest"))
    local_context_window_tokens: int = field(default_factory=lambda: _int("SARAH_LOCAL_CONTEXT_WINDOW_TOKENS", 4096))
    local_effective_context_tokens: int = field(default_factory=lambda: _int("SARAH_LOCAL_EFFECTIVE_CONTEXT_TOKENS", 3072))
    local_max_completion_tokens: int = field(default_factory=lambda: _int("SARAH_LOCAL_MAX_COMPLETION_TOKENS", 48))
    local_request_timeout_seconds: int = field(default_factory=lambda: _int("SARAH_LOCAL_REQUEST_TIMEOUT_SECONDS", 90))
    local_stop_sequences: tuple[str, ...] = field(default_factory=lambda: _csv(
        "SARAH_LOCAL_STOP_SEQUENCES",
        (
            "\nReason",
            "\nReasoning",
            "\n\n**Rationale",
            "\nRationale",
            "\nThought",
            "<think>",
            "</think>",
            "**Addendum",
            "\nAddendum",
            "internal note",
            "\n[TOC]",
            "\n### ",
            "\n---",
            "Let me know",
            "Feel free to",
            "\nSarah AI, a warm",
            "\u2014 Sarah",
            "(1 min)",
            "(Exact word count",
            "(If",
            "If you need",
            "Let's get started",
            "\nNote:",
            "\n\nNote:",
        ),
    ))
    local_temperature: float = field(default_factory=lambda: _float("SARAH_LOCAL_TEMPERATURE", 0.25))
    llm_mode: str = field(default_factory=lambda: _str("SARAH_LLM_MODE", "online"))

    # Reflections are only consumed by the legacy (no-conversation) prompt, so
    # generating one after every memory-mode turn is an extra LLM call per
    # message. Opt back in if you want them collected anyway.
    reflections_in_memory_mode: bool = field(default_factory=lambda: _bool("SARAH_REFLECTIONS_IN_MEMORY_MODE", False))

    # Server
    backend_host: str = field(default_factory=lambda: _str("SARAH_BACKEND_HOST", "127.0.0.1"))
    backend_port: int = field(default_factory=lambda: _int("SARAH_BACKEND_PORT", 8907))
    # Shared secret required on every /api request when set. The launcher
    # generates one per run and hands it to both the backend and Electron, so
    # other local processes and web pages open in a browser can't drive the
    # file-writing / code-execution / git endpoints. Unset = no enforcement.
    api_token: str = field(default_factory=lambda: _str("SARAH_API_TOKEN"))

    # External tool paths
    ffmpeg_path: Path = field(default_factory=lambda: _path("SARAH_FFMPEG_PATH", r"C:\ffmpeg\bin\ffmpeg.exe"))
    ffprobe_path: Path = field(default_factory=lambda: _path("SARAH_FFPROBE_PATH", r"C:\ffmpeg\bin\ffprobe.exe"))
    node_path: Path = field(default_factory=lambda: _path("SARAH_NODE_PATH", r"C:\Program Files\nodejs\node.exe"))
    npm_path: Path = field(default_factory=lambda: _path("SARAH_NPM_PATH", r"C:\Program Files\nodejs\npm.cmd"))
    ollama_exe_path: Path = field(default_factory=lambda: _path(
        "SARAH_OLLAMA_EXE_PATH",
        r"C:\Users\Zero\AppData\Local\Programs\Ollama\ollama.exe",
    ))

    # SARAH paths
    wake_model_path: Path = field(default_factory=lambda: _path(
        "SARAH_WAKE_MODEL_PATH",
        BACKEND_DIR / "wake" / "models" / "vosk-model-small-en-us-0.15",
    ))
    wake_words: tuple[str, ...] = field(default_factory=lambda: _csv(
        "SARAH_WAKE_WORDS",
        (
            "hey sarah",
            "ok sarah",
            "okay sarah",
            "hi sarah",
            "hello sarah",
            "yo sarah",
            "wake up sarah",
            "sarah wake up",
            "sarah listen",
            "sarah are you there",
            "sarah",
        ),
    ))
    wake_input_device: str = field(default_factory=lambda: _str("SARAH_WAKE_INPUT_DEVICE", ""))
    # The legacy Vosk wake-word listener holds the microphone in the backend
    # permanently. Live voice (renderer mic, /ws/voice) replaced it, and the
    # app's Mic OFF must really mean off, so it only runs when opted in.
    wake_listener_enabled: bool = field(default_factory=lambda: _bool("SARAH_WAKE_LISTENER", False))
    wake_input_samplerate: int = field(default_factory=lambda: _int("SARAH_WAKE_INPUT_SAMPLERATE", 48000))
    wake_cooldown_seconds: float = field(default_factory=lambda: _float("SARAH_WAKE_COOLDOWN_SECONDS", 2.0))
    wake_min_confidence: float = field(default_factory=lambda: _float("SARAH_WAKE_MIN_CONFIDENCE", 0.35))
    # Issue #30: default to quiet boot — set SARAH_WAKE_RAW_TRANSCRIPT_LOGGING=true
    # to re-enable per-utterance debug + verbose engine config dumps.
    wake_raw_transcript_logging: bool = field(default_factory=lambda: _bool("SARAH_WAKE_RAW_TRANSCRIPT_LOGGING", False))
    wake_raw_log_interval_seconds: float = field(default_factory=lambda: _float("SARAH_WAKE_RAW_LOG_INTERVAL_SECONDS", 0.75))
    # Issue #25 (and #1): prefix fallback let a single confident "hey" wake the
    # system before the full "hey sarah" phrase finished, which clipped the
    # actual voice command. Default OFF — only the configured full wake phrases
    # ("hey sarah", "hi sarah", etc.) trigger a wake. Enable via env var if a
    # downstream user really wants single-word wake behavior.
    wake_prefix_fallback_enabled: bool = field(default_factory=lambda: _bool("SARAH_WAKE_PREFIX_FALLBACK_ENABLED", False))
    wake_prefix_fallback_hits: int = field(default_factory=lambda: _int("SARAH_WAKE_PREFIX_FALLBACK_HITS", 2))
    wake_prefix_fallback_window_seconds: float = field(default_factory=lambda: _float("SARAH_WAKE_PREFIX_FALLBACK_WINDOW_SECONDS", 2.0))
    # Issue #25: when prefix fallback is re-enabled, require a much higher
    # single-utterance confidence (effectively unreachable by default) so a
    # one-shot "hey" can never bypass the multi-hit-in-window guard. Override
    # via env if you've measured a confidence range that matches your mic.
    wake_prefix_single_confidence: float = field(default_factory=lambda: _float("SARAH_WAKE_PREFIX_SINGLE_CONFIDENCE", 1.10))
    wake_prefix_fallback_phrases: tuple[str, ...] = field(default_factory=lambda: _csv(
        "SARAH_WAKE_PREFIX_FALLBACK_PHRASES",
        ("hey", "hi", "hello", "ok", "okay", "yo"),
    ))
    sarah_drive_path: Path = field(default_factory=lambda: _path(
        "SARAH_DRIVE_PATH", BACKEND_ROOT / "SARAH_DRIVE",
    ))
    openclaw_workspace_path: Path = field(default_factory=lambda: _path(
        "OPENCLAW_WORKSPACE_PATH", Path.home() / ".openclaw" / "workspace",
    ))
    # IANA zone used when a conversation has none stored. If it can't be
    # resolved, the OS local zone is used (DST-aware).
    default_timezone: str = field(default_factory=lambda: _str("SARAH_DEFAULT_TIMEZONE", "America/Denver"))
    user_display_name_fallback: str = field(default_factory=lambda: _str(
        "SARAH_USER_DISPLAY_NAME_FALLBACK", "Creator",
    ))
    db_path: Path = field(default_factory=lambda: _path(
        "SARAH_DB_PATH", BACKEND_ROOT / "data" / "sarah.db",
    ))

    # Daily database snapshots (backend/backup.py)
    backup_enabled: bool = field(default_factory=lambda: _bool("SARAH_BACKUP_ENABLED", True))
    backup_dir: Path = field(default_factory=lambda: _path(
        "SARAH_BACKUP_DIR", BACKEND_ROOT / "data" / "backups",
    ))
    backup_keep: int = field(default_factory=lambda: _int("SARAH_BACKUP_KEEP", 7))

    # Skills: Sarah's own repertoire, stored inside her workspace next to the
    # tools she builds. Only this folder is ever read: skills from elsewhere
    # (OpenClaw/ClawHub, GitHub, a zip, a folder) are *installed* into it.
    skills_enabled: bool = field(default_factory=lambda: _bool("SARAH_SKILLS_ENABLED", True))
    skills_inject_char_cap: int = field(default_factory=lambda: _int("SARAH_SKILLS_INJECT_CHAR_CAP", 8000))
    skills_path: Path = field(default_factory=lambda: _path(
        "SARAH_SKILLS_PATH", BACKEND_ROOT / "data" / "sarah_workspace" / "skills",
    ))

    # Persona (B5) - re-enabled after Issue #23 cleanup. Sarah persona files now
    # live in the project at personalities/sarah/ (see persona_workspace_path). Top-level OpenClaw
    # IDENTITY/SOUL fallback was removed in persona/loader.py, so if the slug
    # folder is missing or empty the snapshot stays empty (no Jessie bleed).
    persona_enabled: bool = field(default_factory=lambda: _bool("SARAH_PERSONA_ENABLED", True))
    # Root holding `personalities/<slug>/{IDENTITY,SOUL}.md` and
    # `personalities/_personality_state.json`. Lives in the project so the
    # persona survives the OpenClaw workspace moving or being deleted.
    persona_workspace_path: Path = field(default_factory=lambda: _path(
        "SARAH_PERSONA_WORKSPACE_PATH", REPO_ROOT,
    ))
    persona_inject_char_cap: int = field(default_factory=lambda: _int("SARAH_PERSONA_INJECT_CHAR_CAP", 8000))
    persona_use_active_state: bool = field(default_factory=lambda: _bool("SARAH_PERSONA_USE_ACTIVE_STATE", True))

    # Speech recognition. "auto" uses the GPU when CUDA is available (NVIDIA
    # wheels in the venv), else CPU with a smaller model.
    models_dir: Path = field(default_factory=lambda: MODELS_DIR)
    whisper_model: str = field(default_factory=lambda: _str("SARAH_WHISPER_MODEL", "large-v3-turbo"))
    whisper_cpu_model: str = field(default_factory=lambda: _str("SARAH_WHISPER_CPU_MODEL", "base.en"))
    whisper_device: str = field(default_factory=lambda: _str("SARAH_WHISPER_DEVICE", "auto"))
    # Her voice: "kokoro" (natural, GPU) or "piper" (old). Voice picked by Zero.
    tts_engine: str = field(default_factory=lambda: _str("SARAH_TTS_ENGINE", "kokoro"))
    tts_voice: str = field(default_factory=lambda: _str("SARAH_TTS_VOICE", "af_bella"))
    # Live voice (continuous mic over /ws/voice): silence that ends a turn.
    voice_end_silence_ms: int = field(default_factory=lambda: _int("SARAH_VOICE_END_SILENCE_MS", 550))

    # Sight (camera + screen) via free cloud vision models only; tried in
    # order by OpenRouter. Anything without ":free" is refused (no paid use).
    vision_models: tuple = field(default_factory=lambda: _csv("SARAH_VISION_MODELS", (
        "dots-studio/dots-3-note-preview:free",
        "google/gemma-4-31b-it:free",
        "qwen/qwen3.8-27b:free",
    )))
    vision_daily_cap: int = field(default_factory=lambda: _int("SARAH_VISION_DAILY_CAP", 400))
    vision_min_interval_seconds: int = field(default_factory=lambda: _int("SARAH_VISION_MIN_INTERVAL", 10))

    # Free-model requests per day on OpenRouter (1000 once $10 of credit was
    # ever bought, 50 otherwise). Shown in the app's usage meter.
    free_daily_request_limit: int = field(default_factory=lambda: _int("SARAH_FREE_DAILY_LIMIT", 1000))

    # Agency: tools in chat turns (web, code, files, apps, input, her own
    # tools), guarded by backend/agency/guard.py.
    agency_enabled: bool = field(default_factory=lambda: _bool("SARAH_AGENCY", True))
    # Initiative: her mind loop between conversations (backend/agency/mind.py).
    autonomy_enabled: bool = field(default_factory=lambda: _bool("SARAH_AUTONOMY", True))
    autonomy_daily_cap: int = field(default_factory=lambda: _int("SARAH_AUTONOMY_DAILY_CAP", 150))
    autonomy_min_gap_seconds: int = field(default_factory=lambda: _int("SARAH_AUTONOMY_MIN_GAP", 120))
    # She starts a conversation when it's been this quiet while Zero is
    # around, at most once per spacing.
    autonomy_chat_after_minutes: int = field(default_factory=lambda: _int("SARAH_CHAT_AFTER_MINUTES", 12))
    autonomy_chat_spacing_minutes: int = field(default_factory=lambda: _int("SARAH_CHAT_SPACING_MINUTES", 25))

    # Embodiment: Sarah may speak on her own when something happens to her
    # body or presence (a head pat, the user coming back). Off = she still
    # reacts physically and remembers it, but stays quiet.
    presence_voice_enabled: bool = field(default_factory=lambda: _bool("SARAH_PRESENCE_VOICE", True))
    presence_voice_cooldown_seconds: int = field(default_factory=lambda: _int("SARAH_PRESENCE_VOICE_COOLDOWN", 40))

    # Safety
    autopilot_tier: int = field(default_factory=lambda: _int("SARAH_AUTOPILOT_TIER", 0))
    respect_permissions: bool = field(default_factory=lambda: _bool("SARAH_RESPECT_PERMISSIONS", True))
    override_phrase: str = field(default_factory=lambda: _str("SARAH_OVERRIDE_PHRASE", "Creator override: baseline mode"))

    @property
    def health_url(self) -> str:
        return f"http://{self.backend_host}:{self.backend_port}/api/health"


settings = Settings()
