"""B6 P3.6 — TTS bench script.

Run with:
    cd backend && B6_TIMING=1 python tests/bench_b6_tts.py

NOT a pytest test — produces stdout only. Captures cold + warm Piper timings
plus STT decode timings (if a sample wav is available). Output is consumed
by `BENCH_B6.md`.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from statistics import median

# Force B6_TIMING on for this run.
os.environ["B6_TIMING"] = "1"

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.piper.piper_tts import piper_tts


SAMPLE_TEXTS = [
    "Hello, how can I help you today?",
    "Sure, I'll take care of that for you.",
    "I noticed you've been working on the backend audit.",
    "Let me check the configuration and get back to you.",
    "That sounds reasonable — I'll proceed with the change.",
    "Thank you for confirming. I'll handle the rest.",
    "I'm here whenever you need me.",
    "Absolutely, that's a good approach.",
    "Give me a moment to look that up.",
    "All done — let me know if you'd like anything adjusted.",
    "Is there anything else you'd like me to take care of?",
]


def run_one(text: str) -> float:
    t0 = time.perf_counter()
    out_path = piper_tts(text)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    # Cleanup output file to avoid filling disk on repeat runs.
    try:
        Path(out_path).unlink(missing_ok=True)
    except Exception:
        pass
    return elapsed_ms


OUT_PATH = Path(__file__).parent.parent.parent.parent / "BENCH_B6.md"


def writeln(f, s: str = ""):
    f.write(s + "\n")
    f.flush()


def main():
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        writeln(f, "# B6 P3.6 — Piper TTS Bench Results")
        writeln(f)
        writeln(f, f"Samples: {len(SAMPLE_TEXTS)} (1 cold + {len(SAMPLE_TEXTS) - 1} warm)")
        writeln(f, "Wall-clock around `piper_tts()` including subprocess startup.")
        writeln(f)
        writeln(f, "## Per-sample")
        writeln(f)
        writeln(f, "| # | Kind | ms | text_len |")
        writeln(f, "|---|---|---:|---:|")

        timings = []
        for i, text in enumerate(SAMPLE_TEXTS):
            kind = "COLD" if i == 0 else "warm"
            ms = run_one(text)
            timings.append(ms)
            writeln(f, f"| {i} | {kind} | {ms:.1f} | {len(text)} |")

        cold_ms = timings[0]
        warm = timings[1:]
        p50 = median(warm)
        p95 = sorted(warm)[int(len(warm) * 0.95) - 1] if len(warm) >= 5 else max(warm)
        writeln(f)
        writeln(f, "## Summary")
        writeln(f)
        writeln(f, f"- cold:     **{cold_ms:.0f} ms**")
        writeln(f, f"- warm p50: **{p50:.0f} ms**")
        writeln(f, f"- warm p95: **{p95:.0f} ms**")
        writeln(f, f"- warm min: {min(warm):.0f} ms")
        writeln(f, f"- warm max: {max(warm):.0f} ms")
        writeln(f)
        writeln(f, "## Notes")
        writeln(f)
        writeln(f, "- The `[TIMING] stage=tts.synth` log lines (when `B6_TIMING=1`) isolate the `subprocess.communicate` window inside this wall-clock.")
        writeln(f, "- Cold reflects first piper.exe spawn + ONNX voice load. Subsequent calls re-spawn but voice load is OS-cached.")


if __name__ == "__main__":
    main()
