"""Stage 2b: blush decals and hair bones.

- Blush: two soft pink ovals just in front of her cheeks (found by casting
  rays at the face), on the head bone, with a radial-gradient texture. They
  start invisible; export_vrm.py binds a "blush" expression that fades them in.
- Hair: seven short bone chains around the head (front locks, sides, back),
  each root -> mid -> tip under the head bone. Hair below the temples is
  weighted to the two nearest chains, blending from the head at the roots to
  the chain at the ends, so VRM spring bones (export_vrm.py) can sway it.
"""
import bpy, bmesh, sys, os, math
import numpy as np
from mathutils import Vector, Matrix

src, out, texdir = sys.argv[-3:]
bpy.ops.wm.open_mainfile(filepath=src)
arm = bpy.data.objects["Armature"]
Mi = arm.matrix_world.inverted()

# --- Blush -------------------------------------------------------------------
def blush_texture(size=128):
    img = bpy.data.images.new("TEX_Sarah_blush", size, size, alpha=True)
    y, x = np.mgrid[0:size, 0:size]
    r = np.hypot((x - size / 2 + 0.5) / (size / 2), (y - size / 2 + 0.5) / (size / 2))
    a = np.clip(1 - r, 0, 1) ** 1.6 * 0.75
    px = np.zeros((size, size, 4), np.float32)
    px[..., 0], px[..., 1], px[..., 2], px[..., 3] = 0.98, 0.45, 0.52, a
    img.pixels[:] = px.ravel()
    img.filepath_raw = os.path.join(texdir, "TEX_Sarah_blush.png")
    img.file_format = "PNG"
    img.save()
    img.pack()
    return img

dg = bpy.context.evaluated_depsgraph_get()
bm = bmesh.new()
uv_layer = bm.loops.layers.uv.new("UVMap")
for side in (1, -1):
    hit, loc, nor, *_ = bpy.context.scene.ray_cast(dg, Vector((side * 0.041, -0.5, 1.404)), Vector((0, 1, 0)))
    assert hit, "no cheek found"
    n = nor.normalized()
    up = Vector((0, 0, 1))
    right = up.cross(n).normalized()
    up = n.cross(right).normalized()
    center = loc + n * 0.0015
    ring = []
    for i in range(16):
        a = 2 * math.pi * i / 16
        p = center + right * math.cos(a) * 0.017 + up * math.sin(a) * 0.010
        ring.append((bm.verts.new(p), (0.5 + 0.5 * math.cos(a), 0.5 + 0.5 * math.sin(a))))
    c = bm.verts.new(center)
    for i in range(16):
        (v1, uv1), (v2, uv2) = ring[i], ring[(i + 1) % 16]
        f = bm.faces.new((c, v1, v2) if side < 0 else (c, v2, v1))
        for loop, uv in zip(f.loops, ((0.5, 0.5), uv1, uv2) if side < 0 else ((0.5, 0.5), uv2, uv1)):
            loop[uv_layer].uv = uv
mesh = bpy.data.meshes.new("Sarah_Blush")
bm.to_mesh(mesh)
bm.free()
blush = bpy.data.objects.new("GEO_Polly_Blush", mesh)
bpy.context.scene.collection.objects.link(blush)
mat = bpy.data.materials.new("MAT_Polly_Blush")
mat.use_nodes = True
nt = mat.node_tree
bsdf = nt.nodes.get("Principled BSDF")
tex = nt.nodes.new("ShaderNodeTexImage")
tex.image = blush_texture()
nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
nt.links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
mat.surface_render_method = "BLENDED"
mesh.materials.append(mat)
blush.parent = arm
blush.matrix_parent_inverse = arm.matrix_world.inverted()
vg = blush.vertex_groups.new(name="Head")
vg.add(list(range(len(mesh.vertices))), 1.0, "REPLACE")
blush.modifiers.new("Armature", "ARMATURE").object = arm

# --- Hair bones ----------------------------------------------------------------
hair = bpy.data.objects["GEO_Polly_Hair"]
Hm = hair.matrix_world
verts = [Hm @ v.co for v in hair.data.vertices]
CENTER = Vector((0.0, 0.027, 0.0))
Z_ROOT, Z_MID, Z_TIP = 1.45, 1.37, 1.29
CHAINS = {"FrontL": 35, "FrontR": -35, "SideL": 80, "SideR": -80, "BackL": 130, "BackR": -130, "Back": 180}

def azimuth(v):
    """0 = front (-Y), +90 = her left (+X)."""
    return math.degrees(math.atan2(v.x - CENTER.x, -(v.y - CENTER.y)))

def ang_diff(a, b):
    return abs((a - b + 180) % 360 - 180)

def surface_point(az, z):
    """Average hair position near this azimuth and height."""
    near = [v for v in verts if ang_diff(azimuth(v), az) < 22 and abs(v.z - z) < 0.03]
    if not near:
        near = [v for v in verts if ang_diff(azimuth(v), az) < 40]
    p = sum(near, Vector()) / len(near)
    return Vector((p.x, p.y, z))

bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode="EDIT")
eb = arm.data.edit_bones
for name, az in CHAINS.items():
    pts = [surface_point(az, z) for z in (Z_ROOT, Z_MID, Z_TIP)]
    tip_end = pts[2] + (pts[2] - pts[1]).normalized() * 0.03
    parent = eb["Head"]
    for i, (a, b) in enumerate(((pts[0], pts[1]), (pts[1], pts[2]), (pts[2], tip_end))):
        bone = eb.new(f"Hair_{name}_{i + 1}")
        bone.head, bone.tail = Mi @ a, Mi @ b
        bone.parent = parent
        bone.use_connect = i > 0
        bone.use_deform = i < 2
        parent = bone
bpy.ops.object.mode_set(mode="OBJECT")

groups = {g.name: g for g in hair.vertex_groups}
for name in CHAINS:
    for i in (1, 2):
        groups[f"Hair_{name}_{i}"] = hair.vertex_groups.new(name=f"Hair_{name}_{i}")
head_g = groups["Head"]
order = sorted(CHAINS.items(), key=lambda kv: kv[1])
for idx, v in enumerate(verts):
    if v.z >= Z_ROOT:
        continue
    s = min(1.0, (Z_ROOT - v.z) / (Z_ROOT - Z_TIP))
    w_head = max(0.0, 1 - s * 2.5)
    w2 = min(1.0, max(0.0, (s - 0.45) / 0.4))
    w1 = max(0.0, 1 - w_head - w2)
    # The two chains either side of this vertex, by angle.
    az = azimuth(v)
    dists = sorted(((ang_diff(az, a), n) for n, a in CHAINS.items()))
    (d1, n1), (d2, n2) = dists[0], dists[1]
    k1 = d2 / (d1 + d2) if d1 + d2 > 0 else 1.0
    head_g.add([idx], w_head, "REPLACE")
    for n, k in ((n1, k1), (n2, 1 - k1)):
        if w1 * k > 0.001:
            groups[f"Hair_{n}_1"].add([idx], w1 * k, "ADD")
        if w2 * k > 0.001:
            groups[f"Hair_{n}_2"].add([idx], w2 * k, "ADD")

bpy.ops.wm.save_as_mainfile(filepath=out)
print("EXTRAS DONE", len([b for b in arm.data.bones if b.name.startswith("Hair_")]), "hair bones")
