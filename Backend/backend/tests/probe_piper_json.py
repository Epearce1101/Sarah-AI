"""Probe: does piper.exe --json-input stay alive across multiple lines?

Sends two JSON requests on stdin, observes:
  - process still alive between requests?
  - what comes out on stdout per request?
  - WAV files written to output_dir as expected?
"""
import json
import subprocess
import time
import pathlib
import threading
import sys

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


def main():
    cmd = [
        str(PIPER),
        "--model", str(MODEL),
        "--json-input",
        "--output_dir", str(OUT),
    ]
    print(f"[probe] spawning: {' '.join(cmd)}")
    t_spawn = time.time()
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    out_lines = []
    err_lines = []
    threading.Thread(target=drain, args=(proc.stdout, "OUT", out_lines), daemon=True).start()
    threading.Thread(target=drain, args=(proc.stderr, "ERR", err_lines), daemon=True).start()

    # Send first request
    req1 = {"text": "First request, hello there.", "output_file": str(OUT / "probe_1.wav")}
    print(f"[probe] sending req1 at t={time.time()-t_spawn:.2f}s")
    proc.stdin.write(json.dumps(req1) + "\n")
    proc.stdin.flush()

    # Wait a beat, then send second
    time.sleep(2.0)
    print(f"[probe] alive after 2s? poll={proc.poll()} (None means still running)")

    req2 = {"text": "Second request, still here.", "output_file": str(OUT / "probe_2.wav")}
    print(f"[probe] sending req2 at t={time.time()-t_spawn:.2f}s")
    proc.stdin.write(json.dumps(req2) + "\n")
    proc.stdin.flush()

    time.sleep(2.0)
    print(f"[probe] alive after second 2s? poll={proc.poll()}")

    # Close stdin to let it drain and exit cleanly
    proc.stdin.close()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        print("[probe] terminating (didn't exit on stdin close)")
        proc.terminate()
        proc.wait(timeout=3)

    print()
    print(f"[probe] exit code: {proc.returncode}")
    print(f"[probe] wav1 exists: {(OUT / 'probe_1.wav').exists()} size={(OUT / 'probe_1.wav').stat().st_size if (OUT / 'probe_1.wav').exists() else 0}")
    print(f"[probe] wav2 exists: {(OUT / 'probe_2.wav').exists()} size={(OUT / 'probe_2.wav').stat().st_size if (OUT / 'probe_2.wav').exists() else 0}")
    print()
    print("--- STDOUT lines ---")
    for label, ts, line in out_lines:
        print(f"  [+{ts-t_spawn:.2f}s] {label}: {line}")
    print()
    print("--- STDERR lines ---")
    for label, ts, line in err_lines:
        print(f"  [+{ts-t_spawn:.2f}s] {label}: {line}")

    # Cleanup
    for f in [OUT / "probe_1.wav", OUT / "probe_2.wav"]:
        f.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
