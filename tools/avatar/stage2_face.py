"""Stage 2: bake face-bone poses into VRM expression shape keys.

Offsets are (out, forward, up) in metres in world space; "out" points away
from the face centre on each side. Bone names with {s} apply to both sides
unless the expression is one-sided.
"""
import bpy, sys, math
from mathutils import Matrix, Vector
src, out = sys.argv[-2:]
bpy.ops.wm.open_mainfile(filepath=src)
arm = bpy.data.objects["Armature"]
Mi3 = arm.matrix_world.inverted().to_3x3()

LIDS_UP = "Eyelid_Upper_Mid_{s}"; LIDS_LO = "Eyelid_Lower_Mid_{s}"
CORNERS = ["Mouth_Upper_3_{s}", "Mouth_Lower_3_{s}"]
def corners(o=0.0, f=0.0, u=0.0, inner=0.5):
    d = {c: (o, f, u) for c in CORNERS}
    for c in ("Mouth_Upper_2_{s}", "Mouth_Lower_2_{s}"): d[c] = (o * inner, f * inner, u * inner)
    return d
def brows(b1, b2, b3, b4, in1=0.0):
    return {"Eyebrow_1_{s}": (in1, 0, b1), "Eyebrow_2_{s}": (0, 0, b2), "Eyebrow_3_{s}": (0, 0, b3), "Eyebrow_4_{s}": (0, 0, b4)}
JAW = lambda deg: {"Jaw": deg}

EXPR = {
    "blink":      {LIDS_UP: (0, -0.002, -0.020), LIDS_LO: (0, 0, 0.003)},
    "blinkLeft":  ("L", {LIDS_UP: (0, -0.002, -0.020), LIDS_LO: (0, 0, 0.003)}),
    "blinkRight": ("R", {LIDS_UP: (0, -0.002, -0.020), LIDS_LO: (0, 0, 0.003)}),
    "happy":      {**corners(0.003, 0, 0.009, inner=0.6), LIDS_LO: (0, 0, 0.006), **brows(0.003, 0.003, 0.002, 0.001)},
    "sad":        {**corners(0, 0, -0.004), LIDS_UP: (0, 0, -0.004), **brows(0.006, 0.003, 0.0, -0.003)},
    "angry":      {**corners(-0.001, 0, -0.003), LIDS_UP: (0, 0, -0.004), **brows(-0.006, -0.003, 0.0, 0.002, in1=-0.002)},
    "surprised":  {**JAW(8), **corners(-0.003, 0, 0), LIDS_UP: (0, 0, 0.003), LIDS_LO: (0, 0, -0.002), **brows(0.008, 0.008, 0.007, 0.006)},
    "relaxed":    {**corners(0.001, 0, 0.003), LIDS_UP: (0, 0, -0.008), **brows(-0.001, -0.001, -0.001, -0.001)},
    "aa":         {**JAW(14), "Mouth_Upper": (0, 0, 0.002)},
    "ih":         {**JAW(5), **corners(0.004, 0, 0.001)},
    "ee":         {**JAW(4), **corners(0.007, 0, 0.002), "Mouth_Upper": (0, 0, 0.001)},
    "oh":         {**JAW(11), **corners(-0.005, 0, 0)},
    "ou":         {**JAW(6), **corners(-0.010, -0.003, 0), "Mouth_Upper": (0, -0.004, 0), "Mouth_Lower": (0, -0.004, 0)},
    # Custom expressions (not VRM presets): a big open smile with teeth, and a pout.
    "grin":       {**JAW(7), **corners(0.005, 0, 0.010, inner=0.6), "Mouth_Upper": (0, 0, 0.003),
                   LIDS_LO: (0, 0, 0.006), **brows(0.004, 0.004, 0.003, 0.002)},
    "pout":       {**corners(-0.008, -0.004, -0.002), "Mouth_Upper": (0, -0.005, -0.001),
                   "Mouth_Lower": (0, -0.006, 0.003), **brows(-0.002, 0.0, 0.001, 0.0, in1=-0.001)},
}

def expand(spec):
    sides = ("L", "R")
    if isinstance(spec, tuple): sides, spec = (spec[0],), spec[1]
    out = {}
    for name, v in spec.items():
        for s in (sides if "{s}" in name else (None,)):
            n = name.format(s=s) if s else name
            if isinstance(v, (int, float)): out[n] = v
            else:
                sx = 1 if s == "L" else -1 if s == "R" else 0
                o, f, u = v
                out[n] = Vector((o * sx, -f, u))  # world: +X her left, -Y forward, +Z up
    return out

def pose(spec):
    for pb in arm.pose.bones: pb.matrix_basis = Matrix.Identity(4)
    bpy.context.view_layer.update()
    # Rotations (jaw) first so its children move with it, then translations.
    for name, v in spec.items():
        if not isinstance(v, (int, float)): continue
        pb = arm.pose.bones[name]; h = pb.head.copy()
        R = Matrix.Rotation(math.radians(v), 4, Mi3 @ Vector((1, 0, 0)))
        pb.matrix = Matrix.Translation(h) @ R @ Matrix.Translation(-h) @ pb.matrix
        bpy.context.view_layer.update()
    for name, v in spec.items():
        if isinstance(v, (int, float)): continue
        pb = arm.pose.bones[name]
        pb.matrix = Matrix.Translation(Mi3 @ v) @ pb.matrix
        bpy.context.view_layer.update()

meshes = [o for o in bpy.data.objects if o.type == "MESH" and any(m.type == "ARMATURE" for m in o.modifiers)]
def coords(o):
    if o.data.shape_keys:
        for k in o.data.shape_keys.key_blocks: k.value = 0.0
        o.active_shape_key_index = 0
        o.data.update()
    dg = bpy.context.evaluated_depsgraph_get()
    return [v.co.copy() for v in o.evaluated_get(dg).data.vertices]

pose({}); rest = {o.name: coords(o) for o in meshes}
basis = {o.name: [v.co.copy() for v in o.data.vertices] for o in meshes}
for ename, spec in EXPR.items():
    pose(expand(spec))
    for o in meshes:
        posed = coords(o); base = rest[o.name]
        if max((a - b).length for a, b in zip(posed, base)) < 1e-5: continue
        if not o.data.shape_keys: o.shape_key_add(name="Basis", from_mix=False)
        k = o.shape_key_add(name=ename, from_mix=False)
        k.value = 0.0
        for i, (a, b) in enumerate(zip(posed, base)):
            k.data[i].co = basis[o.name][i] + (a - b)
    print("expr", ename, [o.name for o in meshes if o.data.shape_keys and ename in o.data.shape_keys.key_blocks])
pose({})
bpy.ops.wm.save_as_mainfile(filepath=out)
