"""B6 P4 smoke: 3 calls through the warm-daemon piper_tts.

Writes timings to /tmp/smoke_warm_tts.txt as it goes so output survives
even if the process hangs.
"""
import os
import sys
import time
from pathlib import Path

os.environ["B6_TIMING"] = "1"
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

LOG = Path("/tmp/smoke_warm_tts.txt")


def log(msg: str):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\n")
        f.flush()


def main():
    if LOG.exists():
        LOG.unlink()
    log(f"start ts={time.time():.2f}")
    log("importing piper_tts...")
    from backend.piper.piper_tts import piper_tts
    log("imported")

    texts = [
        "Hello, how can I help you today?",
        "Sure, I'll take care of that.",
        "Let me check that for you.",
    ]
    for i, text in enumerate(texts):
        log(f"call {i} starting ts={time.time():.2f}")
        t0 = time.perf_counter()
        try:
            out = piper_tts(text)
            ms = (time.perf_counter() - t0) * 1000
            size = Path(out).stat().st_size if Path(out).exists() else 0
            log(f"call {i} ok ms={ms:.1f} size={size}")
            Path(out).unlink(missing_ok=True)
        except Exception as e:
            log(f"call {i} FAILED: {type(e).__name__}: {e}")
            import traceback
            log(traceback.format_exc())
            return
    log("done")


if __name__ == "__main__":
    main()
