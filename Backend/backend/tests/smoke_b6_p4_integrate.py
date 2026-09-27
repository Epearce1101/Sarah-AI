"""B6 P4 integrate smoke: verify end-to-end TTS through HTTP layer.

Walks the production stack:
  create_app() → lifespan startup (incl. prewarm) → /api/tts POST → WAV bytes.

Also exercises a second call to confirm warm-path reuse, and the shutdown
hook to confirm clean daemon teardown.
"""
import os
import sys
import time
from pathlib import Path

os.environ["B6_TIMING"] = "1"
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi.testclient import TestClient

from backend.app import create_app


def main():
    print("[smoke] building app...")
    app = create_app()

    with TestClient(app) as client:
        print("[smoke] lifespan started")

        for i, text in enumerate([
            "Hello, this is a smoke test.",
            "Second call should hit the warm daemon.",
            "Third call as well.",
        ]):
            t0 = time.perf_counter()
            r = client.post("/api/tts", json={"text": text})
            dt_ms = (time.perf_counter() - t0) * 1000
            ok = r.status_code == 200 and r.headers.get("content-type", "").startswith("audio/wav")
            wav_size = len(r.content) if r.status_code == 200 else 0
            print(f"[smoke] call {i}: status={r.status_code} wav_bytes={wav_size} wall_ms={dt_ms:.1f} ok={ok}")
            if not ok:
                print(f"  body={r.text[:300]}")

        print("[smoke] exiting client (triggers shutdown hook)...")

    print("[smoke] done")


if __name__ == "__main__":
    main()
