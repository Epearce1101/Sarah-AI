"""
Download a free (CC0) anime VRM model to try Sarah's 3D avatar with.

    python renderer/avatar3d/download_sample_model.py

It's "AvatarSample_F" from early VRoid Studio, which pixiv released under CC0
(free for anything, no credit needed):
https://opengameart.org/content/vroid-studio-cc0-models

Once you make your own Sarah in VRoid Studio, save her as models/sarah.vrm and
she's used instead.
"""
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

URL = "https://opengameart.org/sites/default/files/avatarsample_f.zip"
MODELS_DIR = Path(__file__).resolve().parent / "models"


def main() -> int:
    target = MODELS_DIR / "AvatarSample_F.vrm"
    if target.exists():
        print(f"Already downloaded: {target}")
        return 0
    print(f"Downloading {URL} (about 11 MB)...")
    with urllib.request.urlopen(URL, timeout=120) as resp:
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        vrm_names = [n for n in zf.namelist() if n.lower().endswith(".vrm")]
        if not vrm_names:
            print("The download didn't contain a .vrm file.")
            return 1
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zf.read(vrm_names[0]))
    print(f"Saved {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
