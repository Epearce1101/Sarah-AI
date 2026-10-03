"""Turn held poses into VRM animation files (.vrma) Sarah's body can play.

    python tools/avatar/make_pose_vrma.py tools/avatar/poses/lying_poses.json frontend/renderer/assets/vrm/animations

The input (made with a pose preview on her model, see poses/lying_poses_spec.json)
holds her rest skeleton (`__rest`: each humanoid bone's parent and position in
the T-pose) and, per pose, every bone's rotation in VRM's normalized space plus
the hips position. Each pose is written as `pose_<name>.vrma`: the rest skeleton
with zero rotations (a T-pose, so the rotations apply to any VRM unchanged),
and a 12 s clip that holds the pose while she breathes; lying on her stomach,
her raised feet also sway.
"""
from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path

DURATION = 12.0
STEP = 0.5


def q_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz]


def q_axis(axis, degrees):
    h = math.radians(degrees) / 2
    x, y, z = axis
    return [x * math.sin(h), y * math.sin(h), z * math.sin(h), math.cos(h)]


def finger_curl(bone):
    """A relaxed hand: fingers slightly curled toward the palm."""
    if not any(f in bone for f in ("Index", "Middle", "Ring", "Little")):
        return None
    side = -1 if bone.startswith("left") else 1
    return q_axis((0, 0, 1), side * (18 if "Proximal" in bone else 22 if "Intermediate" in bone else 15))


def motion(name, bone, t):
    """Small living motion on top of the held pose (degrees about local axes)."""
    breath = math.sin(2 * math.pi * t / 4.0)          # one breath every 4 s
    extra = None
    if bone in ("chest", "upperChest"):
        extra = q_axis((1, 0, 0), 0.9 * breath)
    elif bone == "head":
        extra = q_axis((0, 0, 1), 1.5 * math.sin(2 * math.pi * t / 6.0))
    elif name == "lie_front" and bone in ("leftLowerLeg", "rightLowerLeg"):
        sway = math.sin(2 * math.pi * t / 3.0) * (1 if bone.startswith("left") else -1)
        extra = q_axis((1, 0, 0), 9 * sway)
    return extra


def build(name, pose, rest):
    bones = list(rest["bones"].keys())
    index = {b: i for i, b in enumerate(bones)}
    nodes = []
    for b in bones:
        node = {"name": b, "translation": [float(v) for v in rest["bones"][b]["position"]]}
        kids = [index[c] for c in bones if rest["parents"].get(c) == b]
        if kids:
            node["children"] = kids
        nodes.append(node)
    roots = [index[b] for b in bones if not rest["parents"].get(b)]

    times = [round(i * STEP, 3) for i in range(int(DURATION / STEP) + 1)]
    blob = bytearray()
    accessors, views = [], []

    def add(values, comps, minmax=False):
        data = struct.pack(f"<{len(values)}f", *values)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)})
        blob.extend(data)
        acc = {"bufferView": len(views) - 1, "componentType": 5126, "count": len(values) // comps,
               "type": {1: "SCALAR", 3: "VEC3", 4: "VEC4"}[comps]}
        if minmax:
            acc["min"], acc["max"] = [min(values)], [max(values)]
        accessors.append(acc)
        return len(accessors) - 1

    t_acc = add(times, 1, minmax=True)
    samplers, channels = [], []
    for b in bones:
        base = pose["rotations"].get(b) or [0, 0, 0, 1]
        if abs(base[3]) > 0.99999 and (curl := finger_curl(b)):
            base = curl
        values = []
        for t in times:
            q = base
            extra = motion(name, b, t)
            if extra:
                q = q_mul(q, extra)
            values += q
        samplers.append({"input": t_acc, "output": add(values, 4), "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1, "target": {"node": index[b], "path": "rotation"}})
    hx, hy, hz = pose["hips"]
    hips_values = []
    for t in times:
        hips_values += [hx, hy + 0.004 * math.sin(2 * math.pi * t / 4.0), hz]
    samplers.append({"input": t_acc, "output": add(hips_values, 3), "interpolation": "LINEAR"})
    channels.append({"sampler": len(samplers) - 1, "target": {"node": index["hips"], "path": "translation"}})

    gltf = {
        "asset": {"version": "2.0", "generator": "Sarah make_pose_vrma.py"},
        "extensionsUsed": ["VRMC_vrm_animation"],
        "extensions": {"VRMC_vrm_animation": {"specVersion": "1.0", "humanoid": {
            "humanBones": {b: {"node": index[b]} for b in bones}}}},
        "scene": 0, "scenes": [{"nodes": roots}], "nodes": nodes,
        "animations": [{"name": name, "channels": channels, "samplers": samplers}],
        "buffers": [{"byteLength": len(blob)}], "bufferViews": views, "accessors": accessors,
    }
    j = json.dumps(gltf, separators=(",", ":")).encode()
    j += b" " * (-len(j) % 4)
    blob += b"\0" * (-len(blob) % 4)
    total = 12 + 8 + len(j) + 8 + len(blob)
    return (struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(j), b"JSON") + j
            + struct.pack("<I4s", len(blob), b"BIN\0") + bytes(blob))


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    data = json.loads(src.read_text(encoding="utf-8"))
    rest = data["__rest"]
    for key, name in (("prone", "lie_front"), ("side", "lie_side")):
        path = out / f"pose_{name}.vrma"
        path.write_bytes(build(name, data[key], rest))
        print(f"wrote {path} ({path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
