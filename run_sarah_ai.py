import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    backend_dir = root / "backend"
    cmd = [sys.executable, str(backend_dir / "server.py")]
    subprocess.run(cmd)


if __name__ == "__main__":
    main()
