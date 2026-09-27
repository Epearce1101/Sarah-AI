import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

print("========== SARAH V10 LAUNCHER ==========")

# ---------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = ROOT / "Backend"
BACKEND_DIR = BACKEND_ROOT / "backend"
ELECTRON_DIR = ROOT / "frontend"
LOG_DIR = BACKEND_ROOT / "SARAH_DRIVE" / "logs"

if not BACKEND_DIR.exists():
    print("[ERROR] Backend folder not found:", BACKEND_DIR)
    sys.exit(1)
if not ELECTRON_DIR.exists():
    print("[ERROR] Electron app folder not found:", ELECTRON_DIR)
    sys.exit(1)

VENV_PY = BACKEND_ROOT / ".venv" / "Scripts" / "python.exe"
if not VENV_PY.exists():
    print("[ERROR] Could not find virtual environment Python at:", VENV_PY)
    print("[Launcher] Falling back to system python.")
    VENV_PY = Path(sys.executable)

# ---------------------------------------------------------------------
# Load settings (also loads .env at repo root)
# ---------------------------------------------------------------------
sys.path.insert(0, str(BACKEND_ROOT))
from backend.config import settings  # noqa: E402

HEALTH_URL = settings.health_url
NPM_PATH = settings.npm_path

# Per-run shared secret: the backend rejects /api calls without it and
# Electron's main process attaches it, so web pages open in a normal browser
# can't reach the file-writing / code-execution endpoints on localhost.
# An explicitly configured SARAH_API_TOKEN is respected.
API_TOKEN = settings.api_token or secrets.token_urlsafe(32)
# Read by sarah_api_auth.backend_headers() so the check_*.py scripts work
# against a running session. Removed on shutdown.
API_TOKEN_FILE = BACKEND_ROOT / "data" / ".api_token"


def _publish_token() -> None:
    try:
        API_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        API_TOKEN_FILE.write_text(API_TOKEN, encoding="utf-8")
    except OSError as exc:
        print(f"[Launcher] Could not write API token file ({exc}); helper scripts need SARAH_API_TOKEN.")


def _withdraw_token() -> None:
    try:
        API_TOKEN_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _resolve_npm_start_command() -> list[str]:
    npm_candidate = Path(str(NPM_PATH))
    if npm_candidate.exists():
        npm_bin = str(npm_candidate)
    else:
        npm_bin = shutil.which("npm.cmd") or shutil.which("npm")
        if not npm_bin:
            raise FileNotFoundError(f"npm executable not found (configured path: {NPM_PATH})")
        print(f"[Launcher] npm path not found at configured location, using discovered binary: {npm_bin}")
    if npm_bin.lower().endswith(".cmd") or npm_bin.lower().endswith(".bat"):
        return ["cmd", "/c", npm_bin, "start"]
    return [npm_bin, "start"]


# ---------------------------------------------------------------------
# Backend helpers
# ---------------------------------------------------------------------
def stream_backend_output(process, log_path: Path):
    with log_path.open("a", encoding="utf-8", errors="replace") as log:
        for line in process.stdout:
            print("[BACKEND]", line, end="")
            log.write(line)
            log.flush()


def start_backend_process(log_path: Path):
    backend_env = os.environ.copy()
    backend_env["PYTHONUNBUFFERED"] = "1"
    backend_env["SARAH_API_TOKEN"] = API_TOKEN

    backend_process = subprocess.Popen(
        [str(VENV_PY), "-u", "-m", "backend.server"],
        cwd=str(BACKEND_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=backend_env,
    )
    threading.Thread(
        target=stream_backend_output,
        args=(backend_process, log_path),
        daemon=True,
    ).start()
    return backend_process


def stop_process(process, label: str) -> None:
    if not process or process.poll() is not None:
        return

    print(f"[Sarah Launcher] Stopping {label}...")
    process.terminate()
    try:
        process.wait(timeout=5)
        print(f"[Sarah Launcher] {label} stopped gracefully")
    except subprocess.TimeoutExpired:
        print(f"[Sarah Launcher] {label} did not stop gracefully, force killing...")
        process.kill()
        process.wait()
        print(f"[Sarah Launcher] {label} killed")


def wait_for_backend(timeout: int = 60) -> bool:
    print("[Sarah Launcher] Waiting for backend to come online...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(HEALTH_URL, timeout=1)
            if r.status_code == 200:
                print("[Sarah Launcher] Backend is online.")
                return True
        except Exception:
            pass
        time.sleep(1)
    print("[Sarah Launcher] ERROR: Backend did not respond in time.")
    return False


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
def main() -> None:
    print("[Launcher] Using Python:", VENV_PY)
    print("[Launcher] Health URL:", HEALTH_URL)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    backend_log_path = LOG_DIR / f"launcher_backend_{datetime.now():%Y%m%d_%H%M%S}.log"
    print("[Launcher] Backend log:", backend_log_path)
    print("[Sarah Launcher] Starting backend Python server...")

    backend_process = start_backend_process(backend_log_path)

    if not wait_for_backend():
        print("[Sarah Launcher] Shutting down backend...")
        stop_process(backend_process, "backend")
        sys.exit(1)

    print("[Sarah Launcher] Launching Sarah's desktop UI...")
    # Inject backend port into the Electron process so main.js / preload.js
    # can forward it to the renderer (window.PY_PORT) instead of hardcoding 8907.
    electron_env = os.environ.copy()
    electron_env["SARAH_PY_PORT"] = str(settings.backend_port)
    electron_env["SARAH_API_TOKEN"] = API_TOKEN
    npm_start_cmd = _resolve_npm_start_command()
    print("[Launcher] Electron start command:", " ".join(npm_start_cmd))
    electron_process = subprocess.Popen(
        npm_start_cmd,
        cwd=str(ELECTRON_DIR),
        env=electron_env,
    )

    print("[Sarah Launcher] Sarah AI is now running!")
    print("[Sarah Launcher] Close Electron to shut everything down.")

    backend_restarts = 0
    max_backend_restarts = 3

    while electron_process.poll() is None:
        if backend_process.poll() is not None:
            backend_restarts += 1
            print(
                "[Sarah Launcher] Backend exited while UI was open "
                f"(code={backend_process.returncode}); restart {backend_restarts}/{max_backend_restarts}."
            )
            if backend_restarts > max_backend_restarts:
                print("[Sarah Launcher] Backend restart limit reached; closing Electron.")
                stop_process(electron_process, "Electron")
                break

            backend_process = start_backend_process(backend_log_path)
            if not wait_for_backend(timeout=60):
                print("[Sarah Launcher] Backend restart did not become healthy.")
        time.sleep(2)

    print("[Sarah Launcher] UI closed. Shutting down backend...")
    stop_process(backend_process, "backend")


if __name__ == "__main__":
    _publish_token()
    try:
        main()
    except KeyboardInterrupt:
        print("\n[Sarah Launcher] Keyboard interrupt received, shutting down...")
        sys.exit(0)
    finally:
        _withdraw_token()
