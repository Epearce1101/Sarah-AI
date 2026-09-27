"""Probe: does piper --json-input accept per-line length_scale/noise_scale/noise_w?

Sends three JSON requests with different length_scale values. If piper honors
them, output WAVs will differ in duration. If not, all three will be identical.
"""
import json
import subprocess
import time
import pathlib
import threading
import wave

BASE = pathlib.Path(__file__).parent.parent / "piper"
PIPER = BASE / "piper.exe"
MODEL = BASE / "models" / "en_GB-jenny_dioco-medium.onnx"
OUT = BASE / "output"
OUT.mkdir(exist_ok=True)


def drain(stream, label, lines):
    for line in iter(stream.readline, ""):
        if not line:
            break
        lines.append((label, time.time(), line.rstrip()))


def wav_duration(path):
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def main():
    cmd = [str(PIPER), "--model", str(MODEL), "--json-input", "--output_dir", str(OUT)]
    print(f"[probe] {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    out_lines, err_lines = [], []
    threading.Thread(target=drain, args=(proc.stdout, "OUT", out_lines), daemon=True).start()
    threading.Thread(target=drain, args=(proc.stderr, "ERR", err_lines), daemon=True).start()

    text = "Hello there, how are you doing today?"
    requests = [
        {"text": text, "output_file": str(OUT / "p_a.wav"), "length_scale": 0.8},
        {"text": text, "output_file": str(OUT / "p_b.wav"), "length_scale": 1.0},
        {"text": text, "output_file": str(OUT / "p_c.wav"), "length_scale": 1.5},
    ]
    for r in requests:
        proc.stdin.write(json.dumps(r) + "\n")
        proc.stdin.flush()
        time.sleep(1.5)

    proc.stdin.close()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.terminate()
        proc.wait(timeout=3)

    print(f"[probe] exit={proc.returncode}")
    for r in requests:
        p = pathlib.Path(r["output_file"])
        if p.exists():
            print(f"  ls={r['length_scale']:.1f} -> dur={wav_duration(p):.3f}s size={p.stat().st_size}")
        else:
            print(f"  ls={r['length_scale']:.1f} -> MISSING")

    print("--- STDOUT ---")
    for label, ts, line in out_lines:
        print(f"  {label}: {line}")
    print("--- STDERR ---")
    for label, ts, line in err_lines:
        print(f"  {label}: {line}")

    for r in requests:
        pathlib.Path(r["output_file"]).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
