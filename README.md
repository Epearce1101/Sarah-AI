# Sarah V10

Desktop AI companion: FastAPI backend (`Backend/`) + Electron renderer with a
Live2D avatar (`frontend/`). Chat runs through OpenRouter (or a local Ollama
model), with Piper TTS, Whisper STT, Vosk wake word, screen capture and vision.

## Run

```bat
start.bat
```

`start.py` starts the backend, waits for `/api/health`, launches Electron, and
generates a per-run API token that both sides share (see *Security* below).

## Setup on a fresh machine

1. **Python 3.11** venv at `Backend/.venv`:
   `python -m venv Backend\.venv` then
   `Backend\.venv\Scripts\pip install -r Backend\requirements.lock.txt`
2. **Node 20+** (the code sandbox needs `node --permission`), then
   `cd frontend && npm install`.
3. Copy `.env.example` to `.env` and set `SARAH_OPENROUTER_API_KEY`.
4. Restore the large assets git does not track (see `.gitignore`):

   | Path | What |
   |---|---|
   | `Backend/backend/screen/ffmpeg.exe`, `ffprobe.exe`, `ffplay.exe` | FFmpeg build (screen recording) |
   | `Backend/backend/screen/sarahvision.exe` | Vision helper |
   | `Backend/backend/piper/piper.exe` + `*.dll`, `libtashkeel_model.ort`, `espeak-ng-data/` | Piper TTS runtime (Windows release) |
   | `Backend/backend/piper/models/en_GB-jenny_dioco-medium.onnx(.json)` | Piper voice |
   | `Backend/backend/wake/models/vosk-model-small-en-us-0.15/` | Vosk wake-word model |

5. Optional: **Ollama** for local mode and local vision (`SARAH_OLLAMA_EXE_PATH`).
   Without it, vision falls back to OpenRouter (`SARAH_OPENROUTER_VISION_MODEL`).

## Layout

- `Backend/backend/api/` — HTTP routers (one module per domain)
- `Backend/backend/memory/` — context building, summaries, long-term memory
- `Backend/backend/sandbox.py`, `utils/proc_jail.py` — confined code execution
- `frontend/renderer/dashboard.js` — main UI; `renderer/scripts/core/` — extracted modules
- `personalities/sarah/` — Sarah's persona (IDENTITY.md / SOUL.md)
- `Issues found.md` — issue log with fixes and verification notes

## Tests

```bat
cd Backend && .venv\Scripts\python -m pytest backend/tests -q
cd frontend && npm test
```

The Electron renderer smokes (`frontend/tests/*_smoke.js`, `avatar_runtime_check.js`)
need a backend on port 8907 started **without** `SARAH_API_TOKEN`.

## Security

- The backend only listens on 127.0.0.1 and, when started by `start.py`,
  requires the `X-Sarah-Token` header on every `/api` call. Electron injects it;
  helper scripts read it from `Backend/data/.api_token` via `sarah_api_auth.py`.
- `/api/sandbox-execute` runs code in a Windows Job Object with an audit-hook
  (Python) or `--permission` (Node) file/network jail.
- `/api/apply-code` only writes inside registered project roots.
