"""Build Sarah's VRM from the Blender source model.

    python tools/avatar/build.py --blender "C:/Program Files/Blender Foundation/Blender 4.2/blender.exe" ^
        --source C:/Users/Zero/Desktop/sarah_refined.blend --textures C:/Users/Zero/Desktop/textures

Runs the three Blender stages headless, then adds the VRM extension. The
result lands in tools/avatar/build/sarah.vrm; copy it to
frontend/renderer/assets/vrm/sarah.vrm (keep a backup of the old one).
"""
import argparse, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))

def run_stage(blender, script, *args):
    cmd = [blender, "-b", "--factory-startup", "--python", os.path.join(HERE, script), "--", *args]
    print(">", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--blender", default="blender", help="Blender 4.2+ executable")
    ap.add_argument("--source", required=True, help="the .blend model (sarah_refined.blend)")
    ap.add_argument("--textures", required=True, help="folder with the TEX_*.png files")
    ap.add_argument("--out", default=os.path.join(HERE, "build"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    s1, s2 = os.path.join(a.out, "stage1.blend"), os.path.join(a.out, "stage2.blend")
    run_stage(a.blender, "stage1_tpose.py", os.path.abspath(a.source), os.path.abspath(a.textures), s1)
    run_stage(a.blender, "stage2_face.py", s1, s2)
    run_stage(a.blender, "stage3_export.py", s2, a.out)
    subprocess.run([sys.executable, os.path.join(HERE, "export_vrm.py"),
                    os.path.join(a.out, "sarah_raw.glb"), os.path.join(a.out, "sarah.vrm")], check=True)

if __name__ == "__main__":
    main()
