# Sarah V10

Desktop AI companion: FastAPI backend (`Backend/`) + Electron renderer with a
3D VRM avatar (`frontend/`, Live2D fallback). Chat runs through OpenRouter (or
a local Ollama model), with Piper TTS, Whisper STT, Vosk wake word, screen
capture and vision.

## Run

```bat
start.bat
```

`start.py` starts the backend, waits for `/api/health`, launches Electron, and
generates a per-run API token that both sides share (see *Security* below).
It runs in a console minimized to the taskbar as **Sarah V10 - running**: the
window shows the live backend/UI log, its title shows status (backend health,
uptime), and closing it shuts Sarah down cleanly. Closing the Sarah window
does the same.

## What Sarah can do

- **Live voice** — always listening (echo-cancelled, no wake word), GPU Whisper
  `large-v3-turbo`, talk over her to interrupt. Top bar **Mic**: ON/OFF.
- **Eyes** — watches your screen and webcam, sending a frame to a free cloud
  vision model only when the view changes (frames are never saved). Top bar
  **Camera** and **Screen**: ON/OFF (red dot = camera on). Camera, Screen and
  Mic stay off until you switch them back on.
- **Agency** — uses tools on her own (web search/reading, Python, PowerShell,
  files, apps, mouse/keyboard, fresh looks, reminders) and builds new tools for
  herself (`Backend/data/sarah_workspace/tools`). System hardware/software is
  off limits (`backend/agency/guard.py`); deletes go to the Recycle Bin; every
  action is logged; **■ Stop** pauses her tools.
- **Web** — her own browser (Playwright Chromium in `Backend/models`, run
  `Backend\.venv\Scripts\python -m playwright install chromium` with
  `PLAYWRIGHT_BROWSERS_PATH=Backend\models\ms-playwright` on a fresh machine),
  one-step `research` with cited sources, and `http_request` for web APIs.
- **Skills** — her own repertoire in `Backend/data/sarah_workspace/skills`
  (nothing outside it is read). She adds OpenClaw / Agent-Skills skills from a
  GitHub folder, SKILL.md link, zip or local folder with `add_skill`, and opens
  them with `use_skill`.
- **Memory of her days** — an experience log and a daily journal entry, with
  lasting facts saved to long-term memory (`/api/journal`).
- **Free usage meter** — status bar "Free today": requests used of the daily
  free allowance, by what they were for (`/api/usage`).
- **Initiative** — between conversations she notices things (a game moment, an
  error on screen, an agenda item coming due) and may speak up or act.
  Top bar **Initiative**: ON / QUIET.

Knobs (env): `SARAH_VISION_DAILY_CAP` (400), `SARAH_VISION_MIN_INTERVAL` (10 s),
`SARAH_AUTONOMY` / `SARAH_AUTONOMY_DAILY_CAP` (150) / `SARAH_AUTONOMY_MIN_GAP`
(120 s), `SARAH_AGENCY`, `SARAH_PRESENCE_VOICE`, `SARAH_WHISPER_MODEL`.
Everything downloaded (Whisper models, her Python environment) stays under the
project folder, not C:.

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
   | `frontend/renderer/assets/vrm/sarah.vrm` | Sarah's VRM 1.0 model (personal, non-redistributable) |
   | `frontend/renderer/assets/vrm/animations/*.vrma` | Mocap clips listed in `catalog.json` (from the clawatar animation library) |

   Without the VRM the app falls back to the Live2D avatar.

5. Optional: **Ollama** for local mode and local vision (`SARAH_OLLAMA_EXE_PATH`).
   Without it, vision falls back to OpenRouter (`SARAH_OPENROUTER_VISION_MODEL`).

## Layout

- `Backend/backend/api/` — HTTP routers (one module per domain)
- `Backend/backend/memory/` — context building, summaries, long-term memory
- `Backend/backend/sandbox.py`, `utils/proc_jail.py` — confined code execution
- `frontend/renderer/dashboard.js` — main UI; `renderer/scripts/core/` — extracted modules
- `frontend/renderer/scripts/avatar3d/` — the 3D body: `sarah-vrm.js` (rendering,
  animation, face, gaze, pointing), `director.js` (behaviour, body language),
  `affect.js` (sentence tone → face), `cues.js` (the `<feel>/<face>/<look>/
  <point>/<gesture>` tag parser), `presence.js` (body ↔ mind link)
- `Backend/backend/embodiment/` — Sarah as one self: her feeling (from the
  `<feel>` that opens each reply), her body state and senses as reported by
  the renderer, told back to her every turn ("Right now" block), and
  `impulse.py`: things that happen to her body (touch, the user returning,
  the app opening) that she may answer in her own voice
  (`SARAH_PRESENCE_VOICE=0` keeps her quiet)
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
