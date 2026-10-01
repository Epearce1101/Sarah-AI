"""Build Sarah's 3D model in Blender, from scratch, with one script.

She's modelled after the three turnaround pictures (front, side, back): long
black hair with bangs, amber anime eyes, white T-shirt, navy leggings, black
sneakers and a little devil tail with a spade tip.

Run it either way:

    blender --background --python blender/sarah_model.py
    blender --background --python blender/sarah_model.py -- --out some/folder --no-render

or open Blender > Scripting tab > open this file > Run Script (it builds the
scene in the open file and saves the outputs next to the script).

Outputs (in blender/out/ unless --out is given):
    sarah.blend          the scene, ready to keep editing / sculpting
    sarah.glb            glTF export (mesh + rig + skin weights + textures)
    sarah_face.png       the painted face texture
    preview_*.png        front / side / back / three-quarter renders

Everything is built in real-world metres, Z up, facing -Y (Blender's front
view), standing at the origin, 1.65 m tall. Her left side is +X. The rig uses
humanoid bone names (Hips, Spine, Chest, UpperChest, Neck, Head, UpperArm.L,
...), so the VRM add-on for Blender can map it and export a .vrm for the app.
"""

import math
import os
import random
import sys

import bpy  # noqa: I001  (bpy first: the pip 'bpy' module only exposes bmesh after it)
import bmesh
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

random.seed(7)

# --------------------------------------------------------------------------
# Options
# --------------------------------------------------------------------------
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
OUT_DIR = os.path.join(HERE, "out")
RENDER = True
SAMPLES = 48
for i, a in enumerate(argv):
    if a == "--out":
        OUT_DIR = os.path.abspath(argv[i + 1])
    elif a == "--no-render":
        RENDER = False
    elif a == "--samples":
        SAMPLES = int(argv[i + 1])
os.makedirs(OUT_DIR, exist_ok=True)

# --------------------------------------------------------------------------
# Proportions (metres). Tweak here to reshape her.
# --------------------------------------------------------------------------
HEAD_C = Vector((0.0, 0.006, 1.540))   # head centre
HEAD_W, HEAD_D, HEAD_H = 0.085, 0.095, 0.106   # half width / depth / height
ARM_OUT = math.radians(11)              # arms hang this far out from vertical
SLEEVE_LEN = 0.12                       # T-shirt sleeve length down the upper arm
SHIRT_HEM = 1.0                         # bottom edge of the T-shirt
WAISTBAND = 1.035                       # top of the leggings

# Colours (sRGB, as you'd pick them in a colour picker)
SKIN = (0.98, 0.85, 0.77)
HAIR = (0.045, 0.04, 0.055)
SHIRT = (0.93, 0.93, 0.92)
LEGGINGS = (0.17, 0.24, 0.47)
SHOES = (0.035, 0.035, 0.04)
SOLE = (0.07, 0.07, 0.075)
TAIL = (0.07, 0.065, 0.07)
IRIS_DARK = (0.36, 0.17, 0.05)
IRIS_LIGHT = (0.96, 0.68, 0.24)


def lin(c):
    """sRGB -> linear, for material colours."""
    out = [((x / 12.92) if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4) for x in c[:3]]
    return (*out, 1.0)


# --------------------------------------------------------------------------
# Scene
# --------------------------------------------------------------------------
def reset_scene():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.metaballs, bpy.data.materials,
                 bpy.data.images, bpy.data.armatures, bpy.data.cameras, bpy.data.lights):
        for item in list(coll):
            coll.remove(item)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    return scene


SCENE = reset_scene()


def link(obj):
    SCENE.collection.objects.link(obj)
    return obj


def depsgraph():
    return bpy.context.evaluated_depsgraph_get()


def bake(obj):
    """Apply every modifier / convert to a plain mesh in place."""
    me = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph()))
    if obj.type == "MESH":
        obj.modifiers.clear()
        old = obj.data
        obj.data = me
        bpy.data.meshes.remove(old)
        return obj
    new = link(bpy.data.objects.new(obj.name, me))
    data = obj.data
    bpy.data.objects.remove(obj)
    if isinstance(data, bpy.types.MetaBall):
        bpy.data.metaballs.remove(data)
    new.name = new.name.split(".")[0]
    return new


def shade_smooth(obj):
    obj.data.polygons.foreach_set("use_smooth", [True] * len(obj.data.polygons))


def decimate(obj, faces):
    n = len(obj.data.polygons)
    if n > faces:
        mod = obj.modifiers.new("Decimate", "DECIMATE")
        mod.ratio = faces / n
        bake(obj)


# --------------------------------------------------------------------------
# Materials
# --------------------------------------------------------------------------
def material(name, color, rough=0.55, sheen=0.0, spec=0.25):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = lin(color)
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Specular IOR Level"].default_value = spec
    if sheen:
        bsdf.inputs["Sheen Weight"].default_value = sheen
    mat.diffuse_color = lin(color)
    return mat


def outline_material():
    """Inverted-hull outline: black where the flipped shell faces the camera.
    Only camera rays see it, so it never shadows the model."""
    mat = bpy.data.materials.new("Outline")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    path = nt.nodes.new("ShaderNodeLightPath")
    not_cam = nt.nodes.new("ShaderNodeMath")
    not_cam.operation = "SUBTRACT"
    not_cam.inputs[0].default_value = 1.0
    nt.links.new(path.outputs["Is Camera Ray"], not_cam.inputs[1])
    hide = nt.nodes.new("ShaderNodeMath")
    hide.operation = "MAXIMUM"
    nt.links.new(geo.outputs["Backfacing"], hide.inputs[0])
    nt.links.new(not_cam.outputs[0], hide.inputs[1])
    mix = nt.nodes.new("ShaderNodeMixShader")
    emit = nt.nodes.new("ShaderNodeEmission")
    emit.inputs["Color"].default_value = (0.012, 0.01, 0.012, 1)
    clear = nt.nodes.new("ShaderNodeBsdfTransparent")
    nt.links.new(hide.outputs[0], mix.inputs["Fac"])
    nt.links.new(emit.outputs[0], mix.inputs[1])
    nt.links.new(clear.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    mat.use_backface_culling = True
    mat.diffuse_color = (0, 0, 0, 1)
    return mat


M_SKIN = material("Skin", SKIN, rough=0.5, sheen=0.15)
M_HAIR = material("Hair", HAIR, rough=0.48, spec=0.3, sheen=0.3)
M_SHIRT = material("Shirt", SHIRT, rough=0.8, sheen=0.4, spec=0.15)
M_LEGGINGS = material("Leggings", LEGGINGS, rough=0.6, sheen=0.3)
M_SHOES = material("Shoes", SHOES, rough=0.45)
M_SOLE = material("Sole", SOLE, rough=0.8)
M_TAIL = material("Tail", TAIL, rough=0.35, spec=0.4)
M_OUTLINE = outline_material()


# --------------------------------------------------------------------------
# Metaball helpers: smooth organic shapes that blend at the joints
# --------------------------------------------------------------------------
K_CHAIN = 0.70   # visible size / element size when elements overlap in a chain
K_BLOB = 0.60    # same for a lone element


def metaball(name, res=0.004):
    mb = bpy.data.metaballs.new(name)
    mb.resolution = res
    mb.render_resolution = res
    mb.threshold = 0.6
    return mb, link(bpy.data.objects.new(name, mb))


def blob(mb, co, a, b, c, rot=None, k=K_BLOB, negative=False):
    """Ellipsoid with *visible* half-extents a, b, c (local x, y, z)."""
    m = max(a, b, c)
    e = mb.elements.new(type="ELLIPSOID")
    e.co = co
    e.radius = m / k
    e.size_x, e.size_y, e.size_z = a / m, b / m, c / m
    e.stiffness = 2.0
    e.use_negative = negative
    if rot is not None:
        e.rotation = rot
    return e


def loft(mb, sections, step=0.32):
    """Chain of ellipsoids through sections [(centre, half_x, half_y), ...].

    The chain follows the centres; half_x / half_y are the cross-section sizes
    (x stays roughly world-x, y roughly world-y)."""
    for (p0, a0, b0), (p1, a1, b1) in zip(sections, sections[1:]):
        p0, p1 = Vector(p0), Vector(p1)
        d = p1 - p0
        rot = Vector((0, 0, 1)).rotation_difference(d.normalized())
        n = max(1, int(d.length / (step * min(a0, b0, a1, b1))) + 1)
        for i in range(n + (1 if (p1, a1, b1) == tuple(sections[-1]) else 0)):
            t = i / n
            a, b = a0 + (a1 - a0) * t, b0 + (b1 - b0) * t
            blob(mb, p0 + d * t, a, b, min(a, b), rot=rot, k=K_CHAIN)


# --------------------------------------------------------------------------
# Skeleton landmarks (shared by body, clothes and rig)
# --------------------------------------------------------------------------
def arm_points(s):
    shoulder = Vector((s * 0.158, 0.018, 1.300))
    down = Vector((s * math.sin(ARM_OUT), 0.015, -math.cos(ARM_OUT))).normalized()
    elbow = shoulder + down * 0.265
    down2 = Vector((s * math.sin(ARM_OUT * 0.8), -0.07, -1.0)).normalized()
    wrist = elbow + down2 * 0.235
    hand_tip = wrist + down2 * 0.175
    return shoulder, elbow, wrist, hand_tip, down2


def leg_points(s):
    return (Vector((s * 0.088, 0.008, 0.835)),   # hip joint
            Vector((s * 0.076, -0.004, 0.470)),  # knee
            Vector((s * 0.080, 0.012, 0.090)),   # ankle
            Vector((s * 0.082, -0.085, 0.022)),  # ball of the foot
            Vector((s * 0.082, -0.165, 0.016)))  # toe tip


# --------------------------------------------------------------------------
# Body
# --------------------------------------------------------------------------
def build_body():
    mb, obj = metaball("BodyMB")

    # Torso: (z, centre y, half width, half depth)
    torso = [
        (0.775, 0.012, 0.135, 0.085),
        (0.840, 0.008, 0.158, 0.094),
        (0.920, 0.002, 0.146, 0.086),
        (1.010, 0.000, 0.112, 0.074),
        (1.090, 0.000, 0.118, 0.080),
        (1.170, 0.004, 0.128, 0.086),
        (1.250, 0.010, 0.136, 0.082),
        (1.300, 0.020, 0.118, 0.064),
    ]
    loft(mb, [((0, y, z), a, b) for z, y, a, b in torso])
    # Upper chest / bust, seat, shoulder caps, trapezius
    for s in (-1, 1):
        blob(mb, (s * 0.056, -0.052, 1.190), 0.060, 0.050, 0.058)
        blob(mb, (s * 0.068, 0.050, 0.825), 0.074, 0.058, 0.074)
        blob(mb, (s * 0.142, 0.018, 1.296), 0.045, 0.044, 0.040)
        blob(mb, (s * 0.075, 0.030, 1.325), 0.060, 0.040, 0.030)
    # Neck
    loft(mb, [((0, 0.022, 1.30), 0.046, 0.044), ((0, 0.018, 1.39), 0.038, 0.040),
              ((0, 0.012, 1.48), 0.036, 0.038)])

    for s in (-1, 1):
        hip, knee, ankle, ball, _ = leg_points(s)
        loft(mb, [
            (hip + Vector((0.000, 0.004, 0.0)), 0.086, 0.086),
            (Vector((s * 0.090, 0.000, 0.720)), 0.080, 0.082),
            (Vector((s * 0.084, -0.006, 0.600)), 0.066, 0.070),
            (knee, 0.044, 0.048),
            (Vector((s * 0.076, 0.010, 0.400)), 0.046, 0.050),
            (Vector((s * 0.077, 0.016, 0.320)), 0.047, 0.052),
            (Vector((s * 0.078, 0.012, 0.200)), 0.033, 0.036),
            (ankle, 0.026, 0.029),
        ])
        blob(mb, ball + Vector((0, 0.035, 0.015)), 0.033, 0.085, 0.030)

        shoulder, elbow, wrist, hand_tip, down2 = arm_points(s)
        loft(mb, [
            (shoulder, 0.046, 0.046),
            (shoulder.lerp(elbow, 0.5), 0.040, 0.041),
            (elbow, 0.031, 0.033),
            (elbow.lerp(wrist, 0.35), 0.033, 0.032),
            (wrist, 0.019, 0.025),
        ])
        # Palm (flat, facing the thigh)
        rot = Vector((0, 0, 1)).rotation_difference(down2)
        blob(mb, wrist + down2 * 0.045, 0.014, 0.036, 0.048, rot=rot)

    body = bake(obj)
    body.name = "Body"
    decimate(body, 70000)
    return body


def build_fingers(s):
    """Fingers as separate tubes (metaballs would melt them together)."""
    shoulder, elbow, wrist, hand_tip, down2 = arm_points(s)
    side = Vector((0, 1, 0)).cross(down2).normalized() * s   # towards palm side
    across = down2.cross(side).normalized()                  # front/back of hand
    palm_end = wrist + down2 * 0.085
    tubes = []
    for i, (off, length) in enumerate(((-0.025, 0.070), (-0.0085, 0.078),
                                       (0.0085, 0.074), (0.024, 0.060))):
        base = palm_end + across * off + side * 0.002
        curl = (down2 + side * 0.45).normalized()
        pts = [base - down2 * 0.02, base, base + down2 * length * 0.5,
               base + down2 * length * 0.5 + curl * length * 0.5]
        tubes.append(sweep(f"Finger{i}", catmull(pts, 4),
                           lambda t: 0.0085 * (1 - 0.25 * t), lambda t: 0.0078 * (1 - 0.2 * t),
                           lambda p, T: side, seg=8, round_tip=True))
    tb = wrist + down2 * 0.03 - across * 0.03 + side * 0.006
    pts = [tb, tb + (down2 * 0.6 - across * 0.6 + side * 0.4).normalized() * 0.035,
           tb + down2 * 0.06 - across * 0.035 + side * 0.022]
    tubes.append(sweep("Thumb", catmull(pts, 4), lambda t: 0.0095 * (1 - 0.2 * t),
                       lambda t: 0.0088, lambda p, T: side, seg=8, round_tip=True))
    return tubes


# --------------------------------------------------------------------------
# Sweeps: hair strands, tail, fingers
# --------------------------------------------------------------------------
def catmull(pts, n=6):
    pts = [Vector(p) for p in pts]
    P = [pts[0] + (pts[0] - pts[1])] + pts + [pts[-1] + (pts[-1] - pts[-2])]
    out = []
    for i in range(len(pts) - 1):
        p0, p1, p2, p3 = P[i], P[i + 1], P[i + 2], P[i + 3]
        for k in range(n):
            t = k / n
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t
                              + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    out.append(pts[-1].copy())
    return out


def sweep(name, pts, width, thick, outward, seg=8, round_tip=False, cap_root=True, bm=None):
    """Elliptical tube along pts. width/thick(t) are half-sizes, t = 0..1 by length.
    outward(p, T) gives the direction of the thin side (e.g. away from the head).
    Ends in a point unless round_tip. Appends into bm if given."""
    own = bm is None
    if own:
        bm = bmesh.new()
    lengths = [0.0]
    for a, b in zip(pts, pts[1:]):
        lengths.append(lengths[-1] + (b - a).length)
    total = lengths[-1] or 1.0
    rings = []
    n = len(pts)
    for i, p in enumerate(pts):
        t = lengths[i] / total
        T = (pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)]).normalized()
        O = outward(p, T)
        O = (O - T * O.dot(T))
        if O.length < 1e-6:
            O = T.orthogonal()
        O.normalize()
        S = T.cross(O).normalized()
        if i == n - 1 and not round_tip:
            rings.append([bm.verts.new(p)])
            break
        w, h = max(width(t), 1e-4), max(thick(t), 1e-4)
        rings.append([bm.verts.new(p + S * (math.cos(a) * w) + O * (math.sin(a) * h))
                      for a in (2 * math.pi * k / seg for k in range(seg))])
    for r0, r1 in zip(rings, rings[1:]):
        for k in range(seg):
            k2 = (k + 1) % seg
            if len(r1) == 1:
                bm.faces.new((r0[k], r0[k2], r1[0]))
            else:
                bm.faces.new((r0[k], r0[k2], r1[k2], r1[k]))
    if cap_root:
        bm.faces.new(list(reversed(rings[0])))
    if round_tip:
        tip = pts[-1] + (pts[-1] - pts[-2]).normalized() * thick(1.0) * 0.8
        tv = bm.verts.new(tip)
        last = rings[-1]
        for k in range(seg):
            bm.faces.new((last[k], last[(k + 1) % seg], tv))
    if not own:
        return None
    me = bpy.data.meshes.new(name)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(me)
    bm.free()
    return link(bpy.data.objects.new(name, me))


def finish_bmesh(name, bm, mat):
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    obj = link(bpy.data.objects.new(name, me))
    obj.data.materials.append(mat)
    shade_smooth(obj)
    return obj


# --------------------------------------------------------------------------
# Head
# --------------------------------------------------------------------------
def head_shape(x, y, z):
    """Unit sphere point -> anime head point (relative to HEAD_C, metres)."""
    if z < 0.15:
        t = (0.15 - z) / 1.15
        x *= 1 - 0.40 * t ** 1.3
        if y < 0:
            y *= 1 - 0.12 * t
            z *= 1 + 0.10 * t
        else:
            y *= 1 - 0.42 * t
    if y > 0 and z > -0.3:
        y *= 1.06                     # rounder back of the skull
    if y < -0.35:
        y = -0.35 + (y + 0.35) * 0.82  # flatter face plane
    p = Vector((x * HEAD_W, y * HEAD_D, z * HEAD_H))
    # tiny nose
    nose = math.exp(-((p.x / 0.005) ** 2 + ((p.z + 0.046) / 0.011) ** 2))
    p.y -= 0.0045 * nose * (y < 0)
    return p


def build_head():
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=64, v_segments=40, radius=1.0)
    unit = {}
    for v in bm.verts:
        unit[v.index] = v.co.copy()
        v.co = HEAD_C + head_shape(*v.co)
    head = finish_bmesh("Head", bm, M_SKIN)
    return head, unit


def build_hair_cap(head, unit):
    """Hair shell over the scalp, slightly larger than the head."""
    me = head.data.copy()
    me.name = "HairCap"
    cap = link(bpy.data.objects.new("HairCap", me))
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.verts.ensure_lookup_table()

    def keep(u):
        return (u.z > 0.42 or (u.y > -0.40 and u.z > -0.05)
                or (u.y > 0.15 and u.z > -0.72))

    dead = [f for f in bm.faces if not all(keep(unit[v.index]) for v in f.verts)]
    bmesh.ops.delete(bm, geom=dead, context="FACES")
    for v in bm.verts:
        v.co = HEAD_C + (v.co - HEAD_C) * 1.065
    bm.to_mesh(me)
    bm.free()
    me.materials.clear()
    me.materials.append(M_HAIR)
    sol = cap.modifiers.new("Thickness", "SOLIDIFY")
    sol.thickness = 0.004
    bake(cap)
    shade_smooth(cap)
    return cap


# --------------------------------------------------------------------------
# Face texture (painted with numpy, projected onto the front of the head)
# --------------------------------------------------------------------------
TEX_N = 1024
TEX_X0, TEX_Z0, TEX_SIZE = -0.09, HEAD_C.z - 0.126, 0.18
PX = TEX_SIZE / TEX_N


def paint_face():
    c = (np.arange(TEX_N) + 0.5) * PX
    # X across the face, Z up from the head centre; rows are z, columns x
    X, Z = np.meshgrid(TEX_X0 + c, TEX_Z0 - HEAD_C.z + c)
    img = np.ones((TEX_N, TEX_N, 3)) * np.array(SKIN)

    def cover(d):          # signed distance (m, negative inside) -> coverage
        return np.clip(0.5 - d / PX, 0.0, 1.0)

    def put(col, alpha):
        nonlocal img
        a = alpha[..., None]
        img = img * (1 - a) + np.asarray(col)[None, None, :3] * a

    def ellipse(cx, cz, rx, rz):
        r = np.sqrt(((X - cx) / rx) ** 2 + ((Z - cz) / rz) ** 2)
        return (r - 1) * min(rx, rz)

    # cheeks
    for s in (-1, 1):
        r = np.sqrt(((X - s * 0.042) / 0.015) ** 2 + ((Z + 0.046) / 0.007) ** 2)
        put((0.98, 0.66, 0.64), 0.28 * np.clip(1 - r, 0, 1) ** 1.5)

    for s in (-1, 1):
        cx, cz, hw = s * 0.035, -0.013, 0.0208
        u = (X - cx) * s / hw                                # -1 inner .. +1 outer corner
        uc = np.clip(u, -1, 1)
        upper = cz + 0.0125 * (1 - uc ** 2) ** 0.55 + 0.0035 * uc
        lower = cz - 0.0082 * (1 - uc ** 2) ** 0.85 + 0.0028 * uc
        inside_d = np.maximum.reduce([lower - Z, Z - upper, (np.abs(u) - 1) * hw])
        eye = cover(inside_d)
        put((0.97, 0.96, 0.98), eye)
        # shadow of the upper lid on the eyeball
        put((0.70, 0.66, 0.74), eye * np.clip(1 - (upper - Z) / 0.0035, 0, 1) * 0.6)
        # iris: dark at the top, glowing amber at the bottom
        icx, icz = cx + s * 0.0006, cz - 0.0006
        iris = cover(ellipse(icx, icz, 0.0102, 0.0138)) * eye
        g = np.clip((Z - (icz - 0.0138)) / 0.0276, 0, 1)[..., None]
        iris_col = np.array(IRIS_LIGHT) * (1 - g) + np.array(IRIS_DARK) * g
        a = iris[..., None]
        img = img * (1 - a) + iris_col * a
        ring = cover(np.abs(ellipse(icx, icz, 0.0102, 0.0138)) - 0.0007) * eye
        put((0.28, 0.12, 0.04), ring * 0.9)
        put((0.12, 0.05, 0.02), cover(ellipse(icx, icz + 0.0008, 0.0040, 0.0066)) * eye)
        # catch-lights
        put((1, 1, 1), cover(ellipse(icx - s * 0.0038, icz + 0.0052, 0.0030, 0.0028)) * eye)
        put((1, 1, 1), 0.9 * cover(ellipse(icx + s * 0.0040, icz - 0.0062, 0.0014, 0.0013)) * eye)
        # upper lash line, thicker towards the outer corner, with a wing
        th = 0.0018 + 0.0015 * np.clip(u, 0, 1.2)
        lid = np.maximum.reduce([upper - Z - 0.0002, Z - upper - th, (np.abs(u + 0.05) - 1.05) * hw])
        put((0.07, 0.04, 0.05), cover(lid))
        wing_c = cz + 0.0036 + (u - 1) * 0.005
        wing = np.maximum.reduce([np.abs(Z - wing_c) - 0.0016 * np.clip((1.32 - u) / 0.32, 0, 1),
                                  (0.95 - u) * hw, (u - 1.32) * hw])
        put((0.07, 0.04, 0.05), cover(wing))
        # lower lid hint and crease
        low = np.maximum.reduce([np.abs(Z - lower) - 0.00045, (0.15 - u) * hw, (u - 1.0) * hw])
        put((0.35, 0.20, 0.18), 0.8 * cover(low))
        crease = np.maximum(np.abs(Z - upper - 0.0042) - 0.00035, (np.abs(u - 0.1) - 0.75) * hw)
        put((0.62, 0.40, 0.36), 0.6 * cover(crease))
        # eyebrow
        bu = (X - s * 0.036) * s / 0.017
        buc = np.clip(bu, -1, 1)
        brow_c = 0.0135 + 0.0022 * (1 - buc ** 2) - 0.0010 * buc
        brow = np.maximum(np.abs(Z - brow_c) - (0.0011 - 0.0005 * (buc + 1) / 2),
                          (np.abs(bu) - 1) * 0.017)
        put((0.16, 0.10, 0.10), 0.95 * cover(brow))

    # nose and mouth
    put((0.80, 0.58, 0.52), 0.55 * cover(ellipse(0.0018, -0.0445, 0.0016, 0.0008)))
    mu = np.clip(X / 0.0085, -1, 1)
    mouth_c = -0.0765 + 0.0016 * mu ** 2
    mouth = np.maximum(np.abs(Z - mouth_c) - 0.00055 * (1 - 0.6 * mu ** 2), (np.abs(X) - 0.0085))
    put((0.58, 0.30, 0.30), 0.9 * cover(mouth))
    put((0.92, 0.58, 0.58), 0.30 * cover(ellipse(0.0, -0.0800, 0.0060, 0.0020)))

    image = bpy.data.images.new("SarahFace", TEX_N, TEX_N, alpha=False)
    rgba = np.concatenate([np.clip(img, 0, 1), np.ones((TEX_N, TEX_N, 1))], axis=2)
    image.pixels.foreach_set(rgba.astype(np.float32).ravel())
    image.filepath_raw = os.path.join(OUT_DIR, "sarah_face.png")
    image.file_format = "PNG"
    image.save()
    image.pack()
    return image


def face_material(image):
    mat = M_SKIN.copy()
    mat.name = "Face"
    nt = mat.node_tree
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.extension = "EXTEND"
    nt.links.new(tex.outputs["Color"], nt.nodes["Principled BSDF"].inputs["Base Color"])
    return mat


def project_face_uvs(head):
    me = head.data
    uv = me.uv_layers.new(name="UVMap")
    for loop in me.loops:
        p = me.vertices[loop.vertex_index].co
        u = (p.x - TEX_X0) / TEX_SIZE
        v = (p.z - TEX_Z0) / TEX_SIZE
        if p.y > HEAD_C.y - 0.02:          # back of the head: park on the skin border
            u = 0.0 if p.x < 0 else 1.0
            v = 0.99
        uv.data[loop.index].uv = (min(max(u, 0.0), 1.0), min(max(v, 0.003), 0.997))


# --------------------------------------------------------------------------
# Clothes
# --------------------------------------------------------------------------
def seg_dist(p, a, b):
    ab = b - a
    t = max(0.0, min(1.0, (p - a).dot(ab) / ab.length_squared))
    return (p - (a + ab * t)).length, t


def arm_info(p):
    """(is_arm, distance down the arm from the shoulder joint)"""
    s = 1 if p.x >= 0 else -1
    if abs(p.x) < 0.145:
        return False, 0.0
    shoulder, elbow, wrist, hand_tip, _ = arm_points(s)
    best, along, acc = 9.0, 0.0, 0.0
    for a, b in ((shoulder, elbow), (elbow, wrist), (wrist, hand_tip)):
        d, t = seg_dist(p, a, b)
        if d < best:
            best, along = d, acc + t * (b - a).length
        acc += (b - a).length
    # tighter below the waist, where the hands hang close to the hips
    return best < (0.062 if p.z < 0.98 else 0.068), along


def in_shirt(p):
    is_arm, along = arm_info(p)
    if is_arm:
        return along < SLEEVE_LEN
    if p.z < SHIRT_HEM or p.z > 1.40:
        return False
    # scoop neck at the front, higher at the back
    if p.y < 0.01 and p.z > 1.255 + 7.0 * p.x ** 2:
        return False
    if p.z > 1.315 and abs(p.x) < 0.085:
        return False
    return True


def arm_vertices(me, seed_along=0.15):
    """Per vertex: (is part of an arm, distance down the arm). Grown along the
    mesh from the forearms and hands, so hips the hands hang next to (but
    don't touch) never count as arm."""
    n = len(me.vertices)
    co = np.empty(n * 3)
    me.vertices.foreach_get("co", co)
    P = co.reshape(-1, 3)
    dist = np.full(n, 9.0)
    along = np.zeros(n)
    for s in (-1, 1):
        side = (P[:, 0] * s) >= 0
        acc = 0.0
        pts = arm_points(s)
        for a, b in zip(pts[:3], pts[1:4]):
            a, b = np.array(a), np.array(b)
            ab = b - a
            t = np.clip(((P - a) @ ab) / (ab @ ab), 0, 1)
            d = np.linalg.norm(P - (a + t[:, None] * ab), axis=1)
            better = side & (d < dist)
            dist[better] = d[better]
            along[better] = acc + t[better] * np.linalg.norm(ab)
            acc += np.linalg.norm(ab)
    cand = (np.abs(P[:, 0]) > 0.145) & (dist < 0.095)
    seeds = cand & (dist < 0.06) & (along > seed_along)
    edges = np.empty(len(me.edges) * 2, dtype=np.int64)
    me.edges.foreach_get("vertices", edges)
    edges = edges.reshape(-1, 2)
    edges = edges[cand[edges[:, 0]] & cand[edges[:, 1]]]
    nbrs = [[] for _ in range(n)]
    for a, b in edges.tolist():
        nbrs[a].append(b)
        nbrs[b].append(a)
    arm = seeds.copy()
    stack = np.nonzero(seeds)[0].tolist()
    while stack:
        for b in nbrs[stack.pop()]:
            if not arm[b]:
                arm[b] = True
                stack.append(b)
    return arm, along


def assign_body_materials(body):
    me = body.data
    me.materials.clear()
    for m in (M_SKIN, M_LEGGINGS):
        me.materials.append(m)
    arm, _ = arm_vertices(me)
    idx = []
    for f in me.polygons:
        is_arm = sum(arm[v] for v in f.vertices) * 2 > len(f.vertices)
        idx.append(1 if (not is_arm and 0.075 < f.center.z < WAISTBAND) else 0)
    me.polygons.foreach_set("material_index", idx)


def build_shirt(body):
    bm = bmesh.new()
    bm.from_mesh(body.data)
    # clean straight cuts for the hem and sleeve ends
    cuts = [(Vector((0, 0, SHIRT_HEM)), Vector((0, 0, 1)))]
    for s in (-1, 1):
        shoulder, elbow = arm_points(s)[:2]
        down = (elbow - shoulder).normalized()
        cuts.append((shoulder + down * SLEEVE_LEN, down))
    for co, no in cuts:
        geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
        bmesh.ops.bisect_plane(bm, geom=geom, plane_co=co, plane_no=no)
    dead = [f for f in bm.faces if not in_shirt(f.calc_center_median())]
    bmesh.ops.delete(bm, geom=dead, context="FACES")
    # shave off the saw-teeth along the cut edges, then even them out
    for _ in range(4):
        teeth = [f for f in bm.faces if sum(e.is_boundary for e in f.edges) >= 2
                 or sum(len(v.link_faces) <= 2 for v in f.verts) >= 2]
        bmesh.ops.delete(bm, geom=teeth, context="FACES")
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    for _ in range(12):
        moves = {}
        for v in bm.verts:
            if v.is_boundary:
                nb = [e.other_vert(v) for e in v.link_edges if e.is_boundary]
                if len(nb) == 2:
                    moves[v] = (nb[0].co + nb[1].co) / 2
        for v, co in moves.items():
            v.co = v.co.lerp(co, 0.5)
    bm.normal_update()
    for v in bm.verts:
        is_arm, along = arm_info(v.co)
        push = 0.005 + (0.005 * along / SLEEVE_LEN if is_arm else 0.0)
        if not is_arm and v.co.z < 1.16:   # hangs a little loose under the bust
            push += 0.006 * min(1.0, (1.16 - v.co.z) / 0.1) * max(0.0, -v.normal.y)
        v.co += v.normal * push
    for f in bm.faces:
        f.material_index = 0
    me = bpy.data.meshes.new("Shirt")
    bm.to_mesh(me)
    bm.free()
    shirt = link(bpy.data.objects.new("Shirt", me))
    me.materials.append(M_SHIRT)
    sm = shirt.modifiers.new("Smooth", "SMOOTH")
    sm.iterations = 6
    sm.factor = 0.5
    sol = shirt.modifiers.new("Cloth", "SOLIDIFY")
    sol.thickness = 0.003
    sol.offset = 1.0
    bake(shirt)
    shade_smooth(shirt)
    return shirt


def build_shoes():
    shoes = []
    for s in (-1, 1):
        mb, obj = metaball("Shoe" + ("L" if s > 0 else "R"), res=0.005)
        x = s * 0.081
        blob(mb, (x, 0.036, 0.050), 0.045, 0.050, 0.050)       # heel
        blob(mb, (x, -0.035, 0.046), 0.049, 0.070, 0.046)      # arch
        blob(mb, (x, -0.118, 0.038), 0.047, 0.060, 0.037)      # toe box
        blob(mb, (x, 0.004, 0.100), 0.040, 0.050, 0.040)       # collar
        blob(mb, (x, -0.050, 0.080), 0.037, 0.050, 0.034)      # tongue / laces
        shoe = bake(obj)
        me = shoe.data
        for v in me.vertices:       # flat, slightly wider rubber sole
            if v.co.z < 0.012:
                v.co.z *= 0.15
            if v.co.z < 0.02:
                v.co.x = x + (v.co.x - x) * 1.05
                v.co.y = -0.04 + (v.co.y + 0.04) * 1.03
        me.materials.append(M_SHOES)
        me.materials.append(M_SOLE)
        me.polygons.foreach_set("material_index",
                                [1 if f.center.z < 0.019 else 0 for f in me.polygons])
        shade_smooth(shoe)
        shoe.name = "Shoe." + ("L" if s > 0 else "R")
        shoes.append(shoe)
    return shoes


# --------------------------------------------------------------------------
# Hair
# --------------------------------------------------------------------------
class Surface:
    def __init__(self, obj):
        self.tree = BVHTree.FromObject(obj, depsgraph())

    def toward(self, centre, direction, offset=0.0):
        d = Vector(direction).normalized()
        hit = self.tree.ray_cast(centre + d * 0.6, -d)
        if hit[0] is None:
            return centre + d * 0.1
        return hit[0] + d * offset

    def behind(self, x, z, offset):
        hit = self.tree.ray_cast(Vector((x, 0.6, z)), Vector((0, -1, 0)))
        return (hit[0].y if hit[0] is not None else 0.03) + offset


def d_back(a, e):
    """Direction from the head centre: a = degrees from straight back, e = elevation."""
    a, e = math.radians(a), math.radians(e)
    return Vector((math.sin(a) * math.cos(e), math.cos(a) * math.cos(e), math.sin(e)))


def d_front(a, e):
    a, e = math.radians(a), math.radians(e)
    return Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))


def hair_outward(p, T):
    c = Vector((0.0, 0.012, min(max(p.z, 0.9), HEAD_C.z)))
    return p - c


def strand_width(wmax, tip_from=0.62, root=0.55):
    def w(t):
        base = root + (1 - root) * min(t / 0.3, 1.0)
        tip = ((1 - t) / (1 - tip_from)) ** 0.75 if t > tip_from else 1.0
        return wmax * base * tip
    return w


def build_hair(head_surf, back_surf):
    bm = bmesh.new()
    # interpolated half-width of the back hair curtain by height
    curtain = [(1.37, 0.115), (1.24, 0.170), (1.10, 0.165), (0.95, 0.140), (0.80, 0.12)]

    def curtain_w(z):
        for (z0, w0), (z1, w1) in zip(curtain, curtain[1:]):
            if z1 <= z <= z0:
                return w0 + (w1 - w0) * (z0 - z) / (z0 - z1)
        return curtain[0][1] if z > curtain[0][0] else curtain[-1][1]

    def back_strand(u, layer, wmax):
        a = u * 118
        e0 = 58 - 12 * min(abs(u), 1)
        lift = 0.018 + 0.007 * layer
        pts = [head_surf.toward(HEAD_C, d_back(a * 0.6, e0), -0.002),
               head_surf.toward(HEAD_C, d_back(a, 14), lift),
               head_surf.toward(HEAD_C, d_back(a * 0.97, -32), lift + 0.006)]
        z_end = 0.90 + 0.05 * abs(u) ** 2 + 0.035 * layer + random.uniform(-0.03, 0.03)
        clear = 0.020 + 0.008 * layer + 0.004 * (1 - abs(u))
        for z in (1.37, 1.25, 1.12, 1.0, 0.9):
            if z <= z_end + 0.03:
                break
            x = u * curtain_w(z) + random.uniform(-0.004, 0.004)
            pts.append(Vector((x, back_surf.behind(x, z, clear), z)))
        x = u * curtain_w(z_end) * 0.97
        pts.append(Vector((x, back_surf.behind(x, z_end, clear) - 0.004, z_end)))
        sweep(None, catmull(pts, 6), strand_width(wmax), lambda t, w=strand_width(wmax): 0.3 * w(t) + 0.0018,
              hair_outward, seg=8, bm=bm)

    for layer, n, wmax in ((0, 24, 0.024), (1, 20, 0.022)):
        for i in range(n):
            u = -1 + 2 * (i + 0.5 * layer) / (n - 1) + random.uniform(-0.02, 0.02)
            back_strand(max(-1.0, min(1.0, u)), layer, wmax)
    # extra strands that spill over the shoulders behind the arms
    for s in (-1, 1):
        for u in (1.08, 1.16, 1.24):
            back_strand(s * u, 1, 0.020)

    # face-framing locks down to the collarbone
    for s in (-1, 1):
        for a, z_end, w in ((80, 1.32, 0.014), (94, 1.29, 0.016), (108, 1.33, 0.016)):
            pts = [head_surf.toward(HEAD_C, d_front(s * a * 0.5, 62), 0.004),
                   head_surf.toward(HEAD_C, d_front(s * a, 22), 0.014),
                   head_surf.toward(HEAD_C, d_front(s * (a + 4), -18), 0.012),
                   head_surf.toward(HEAD_C, d_front(s * (a + 10), -55), 0.014)]
            low = pts[-1]
            pts.append(Vector((low.x + s * 0.012, low.y + 0.008, (low.z + z_end) / 2)))
            pts.append(Vector((low.x + s * 0.020, low.y + 0.014, z_end)))
            sweep(None, catmull(pts, 6), strand_width(w, 0.55),
                  lambda t, w=strand_width(w, 0.55): 0.35 * w(t) + 0.0018,
                  hair_outward, seg=8, bm=bm)

    # bangs: parted a little off-centre, pointed tips over the brows
    for i in range(13):
        u = -1 + 2 * i / 12
        u += 0.08 if u > -0.05 else -0.02
        side = abs(u)
        pts = [head_surf.toward(HEAD_C, d_front(u * 25, 72), -0.002),
               head_surf.toward(HEAD_C, d_front(u * 42, 42), 0.013),
               head_surf.toward(HEAD_C, d_front(u * 52, 16), 0.010)]
        if side < 0.62:
            pts.append(head_surf.toward(HEAD_C, d_front(u * 55, 11 + 4 * side), 0.007))
        else:
            pts.append(head_surf.toward(HEAD_C, d_front(u * 66, -6), 0.009))
            pts.append(head_surf.toward(HEAD_C, d_front(u * 74, -32), 0.008))
        w = 0.0125 if side < 0.62 else 0.011
        sweep(None, catmull(pts, 6), strand_width(w, 0.5, 0.75),
              lambda t, w=strand_width(w, 0.5, 0.75): 0.4 * w(t) + 0.0015,
              lambda p, T: p - HEAD_C, seg=8, bm=bm)

    return finish_bmesh("Hair", bm, M_HAIR)


# --------------------------------------------------------------------------
# Tail
# --------------------------------------------------------------------------
TAIL_PTS = [(0.0, None, 0.935), (0.035, 0.17, 0.87), (0.10, 0.215, 0.70),
            (0.17, 0.205, 0.52), (0.225, 0.17, 0.385), (0.250, 0.15, 0.315)]


def build_tail(back_surf):
    pts = []
    for x, y, z in TAIL_PTS:
        pts.append(Vector((x, back_surf.behind(x, z, -0.01) if y is None else y, z)))
    path = catmull(pts, 8)
    bm = bmesh.new()
    sweep(None, path, lambda t: 0.0115 - 0.0045 * t, lambda t: 0.0115 - 0.0045 * t,
          lambda p, T: Vector((0, 1, 0)), seg=10, round_tip=True, bm=bm)
    # spade tip: flat arrowhead in the plane facing sideways-forward
    end, T = path[-1], (path[-1] - path[-3]).normalized()
    S = T.cross(Vector((0.4, -1, 0)).normalized()).normalized()
    N = S.cross(T).normalized()
    half = [(0.0, -0.006), (0.012, 0.004), (0.032, -0.012), (0.036, 0.010),
            (0.028, 0.034), (0.0, 0.074)]
    outline = [(-x, t) for x, t in reversed(half[1:-1])]
    poly = half + outline
    sides = []
    for side in (-1, 1):
        vs = [bm.verts.new(end + S * x + T * t + N * 0.0035 * side) for x, t in poly]
        f = bm.faces.new(vs if side > 0 else list(reversed(vs)))
        bmesh.ops.triangulate(bm, faces=[f])
        sides.append(vs)
    # close the edges of the spade into a slab
    n = len(poly)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((sides[0][i], sides[0][j], sides[1][j], sides[1][i]))
    return finish_bmesh("Tail", bm, M_TAIL)


# --------------------------------------------------------------------------
# Rig + skin weights
# --------------------------------------------------------------------------
def build_rig():
    arm = bpy.data.armatures.new("SarahRig")
    rig = link(bpy.data.objects.new("SarahRig", arm))
    arm.display_type = "STICK"
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm.edit_bones

    def bone(name, head, tail, parent=None, connect=False):
        b = eb.new(name)
        b.head, b.tail = Vector(head), Vector(tail)
        if parent:
            b.parent = eb[parent]
            b.use_connect = connect
        return b

    bone("Hips", (0, 0.01, 0.86), (0, 0.01, 0.97))
    bone("Spine", (0, 0.01, 0.97), (0, 0.006, 1.09), "Hips", True)
    bone("Chest", (0, 0.006, 1.09), (0, 0.012, 1.21), "Spine", True)
    bone("UpperChest", (0, 0.012, 1.21), (0, 0.02, 1.33), "Chest", True)
    bone("Neck", (0, 0.02, 1.33), (0, 0.014, 1.43), "UpperChest", True)
    bone("Head", (0, 0.014, 1.43), (0, 0.014, 1.65), "Neck", True)
    for s, sfx in ((1, "L"), (-1, "R")):
        shoulder, elbow, wrist, hand_tip, down2 = arm_points(s)
        bone(f"Shoulder.{sfx}", (s * 0.03, 0.02, 1.31), shoulder, "UpperChest")
        bone(f"UpperArm.{sfx}", shoulder, elbow, f"Shoulder.{sfx}", True)
        bone(f"LowerArm.{sfx}", elbow, wrist, f"UpperArm.{sfx}", True)
        bone(f"Hand.{sfx}", wrist, wrist + down2 * 0.09, f"LowerArm.{sfx}", True)
        hip, knee, ankle, ball, toe = leg_points(s)
        bone(f"UpperLeg.{sfx}", hip, knee, "Hips")
        bone(f"LowerLeg.{sfx}", knee, ankle, f"UpperLeg.{sfx}", True)
        bone(f"Foot.{sfx}", ankle, ball, f"LowerLeg.{sfx}", True)
        bone(f"Toes.{sfx}", ball, toe, f"Foot.{sfx}", True)
    return rig


def add_tail_bones(rig, tail_path):
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    eb = rig.data.edit_bones
    idx = np.linspace(0, len(tail_path) - 1, 7).astype(int)
    parent = "Hips"
    for i in range(6):
        b = eb.new(f"Tail.{i + 1:03d}")
        b.head, b.tail = tail_path[idx[i]], tail_path[idx[i + 1]]
        b.parent = eb[parent]
        b.use_connect = i > 0
        parent = b.name
    bpy.ops.object.mode_set(mode="OBJECT")


def bone_table(rig, names):
    return [(n, np.array(rig.data.bones[n].head_local), np.array(rig.data.bones[n].tail_local))
            for n in names]


ARM_BONES = ("UpperArm", "LowerArm", "Hand")


def body_mask(obj, seed_along=0.15):
    """Keep arm bones on the arms and everything else off them, so a bent
    elbow can't drag the hip it hangs next to."""
    arm, along_all = arm_vertices(obj.data, seed_along)
    return lambda P, W, names: _mask(P, W, names, arm, along_all)


def _mask(P, W, names, arm, along_all):
    W = W.copy()
    arm_j = [j for j, n in enumerate(names) if n.split(".")[0] in ARM_BONES]
    upper_j = [j for j, n in enumerate(names) if n.startswith("UpperArm")]
    keep_j = [j for j, n in enumerate(names) if n.split(".")[0] in ("UpperArm", "Shoulder", "UpperChest")]
    for i, co in enumerate(P):
        p = Vector(co)
        is_arm, along = arm[i], along_all[i]
        if is_arm and along > 0.05:
            mask = np.zeros(len(names), bool)
            mask[arm_j] = True
            if along < 0.10:
                mask[keep_j] = True
        elif is_arm:
            mask = np.zeros(len(names), bool)
            mask[keep_j] = True
        else:
            mask = np.ones(len(names), bool)
            mask[arm_j] = False
            s = 1 if p.x >= 0 else -1
            if (p - arm_points(s)[0]).length < 0.075:
                mask[upper_j] = True
        W[i, ~mask] = 0.0
    return W


def skin(obj, rig, names, side_split=True, power=7.0, max_bones=3, fixed=None):
    """Smooth distance-based weights to the given bones."""
    me = obj.data
    P = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", P)
    P = P.reshape(-1, 3)
    bones = bone_table(rig, names)
    D = np.empty((len(P), len(bones)))
    for j, (n, a, b) in enumerate(bones):
        ab = b - a
        t = np.clip(((P - a) @ ab) / (ab @ ab), 0, 1)
        D[:, j] = np.linalg.norm(P - (a + t[:, None] * ab), axis=1)
        if side_split and n.endswith((".L", ".R")):
            wrong = (P[:, 0] < -0.015) if n.endswith(".L") else (P[:, 0] > 0.015)
            D[wrong, j] = 1e3
    W = 1.0 / np.maximum(D, 0.004) ** power
    if fixed is not None:
        W = fixed(P, W, [n for n, _, _ in bones])
    max_bones = min(max_bones, len(bones))
    order = np.argsort(-W, axis=1)[:, :max_bones]
    groups = {n: obj.vertex_groups.new(name=n) for n, _, _ in bones}
    top = np.take_along_axis(W, order, axis=1)
    top /= np.maximum(top.sum(axis=1, keepdims=True), 1e-12)
    for j, (n, _, _) in enumerate(bones):
        for k in range(max_bones):
            sel = np.nonzero((order[:, k] == j) & (top[:, k] > 0.01))[0]
            for w in np.unique(np.round(top[sel, k], 2)):
                ids = sel[np.round(top[sel, k], 2) == w].tolist()
                groups[n].add(ids, float(w), "REPLACE")
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = rig
    obj.parent = rig


def rigid(obj, rig, weights_by_z):
    """Weights blended between bones by height: [(z, bone), ...] top to bottom."""
    me = obj.data
    groups = {}
    for v in me.vertices:
        z = v.co.z
        pairs = weights_by_z
        if z >= pairs[0][0]:
            ws = {pairs[0][1]: 1.0}
        elif z <= pairs[-1][0]:
            ws = {pairs[-1][1]: 1.0}
        else:
            for (z0, b0), (z1, b1) in zip(pairs, pairs[1:]):
                if z1 <= z <= z0:
                    t = (z0 - z) / (z0 - z1)
                    ws = {b0: 1 - t}
                    ws[b1] = ws.get(b1, 0) + t
                    break
        for b, w in ws.items():
            if w > 0.005:
                if b not in groups:
                    groups[b] = obj.vertex_groups.new(name=b)
                groups[b].add([v.index], w, "REPLACE")
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = rig
    obj.parent = rig


def add_outline(obj, thickness=0.0016):
    mats = obj.data.materials
    if M_OUTLINE.name not in mats:
        mats.append(M_OUTLINE)
    mod = obj.modifiers.new("Outline", "SOLIDIFY")
    mod.thickness = thickness
    mod.offset = 1.0
    mod.use_flip_normals = True
    mod.use_rim = False
    mod.material_offset = len(mats) - 1
    mod.show_in_editmode = False


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------
def build():
    print("[sarah] body")
    body = build_body()
    shade_smooth(body)
    fingers = build_fingers(1) + build_fingers(-1)
    for f in fingers:
        f.data.materials.append(M_SKIN)
    print("[sarah] head + face")
    head, unit = build_head()
    face_img = paint_face()
    project_face_uvs(head)
    head.data.materials[0] = face_material(face_img)
    cap = build_hair_cap(head, unit)

    print("[sarah] clothes")
    assign_body_materials(body)
    shirt = build_shirt(body)
    shoes = build_shoes()

    # join fingers into the body
    with bpy.context.temp_override(active_object=body, selected_editable_objects=[body] + fingers):
        bpy.ops.object.join()
    shade_smooth(body)

    print("[sarah] hair + tail")
    head_surf = Surface(head)
    back_surf = Surface(body)
    shirt_surf = Surface(shirt)

    class Back:
        def behind(self, x, z, off):
            return max(back_surf.behind(x, z, off), shirt_surf.behind(x, z, off - 0.002))
    hair = build_hair(head_surf, Back())
    hair_all = [hair, cap]
    tail = build_tail(back_surf)

    print("[sarah] rig")
    rig = build_rig()
    bpy.ops.object.mode_set(mode="OBJECT")
    tail_path = catmull([Vector((x, back_surf.behind(x, z, -0.01) if y is None else y, z))
                         for x, y, z in TAIL_PTS], 2)
    add_tail_bones(rig, tail_path)

    body_bones = [b.name for b in rig.data.bones if not b.name.startswith("Tail")]
    skin(body, rig, [b for b in body_bones if b != "Head"], fixed=body_mask(body))
    skin(shirt, rig, [b for b in body_bones if b != "Head"], fixed=body_mask(shirt, seed_along=0.04))
    rigid(head, rig, [(0.0, "Head")])
    for h in hair_all:
        rigid(h, rig, [(1.45, "Head"), (1.36, "Neck"), (1.24, "UpperChest"), (1.05, "Chest")])
    for s in shoes:
        sfx = s.name[-1]
        skin(s, rig, [f"Foot.{sfx}", f"Toes.{sfx}"], side_split=False)
    skin(tail, rig, ["Hips"] + [b.name for b in rig.data.bones if b.name.startswith("Tail")],
         side_split=False, power=4.0, max_bones=2)

    for obj, th in ((body, 0.0016), (head, 0.0013), (shirt, 0.0018), (hair, 0.0012),
                    (cap, 0.0014), (tail, 0.0012), *((s, 0.0016) for s in shoes)):
        add_outline(obj, th)
    return rig, [body, head, shirt, hair, cap, tail, *shoes]


# --------------------------------------------------------------------------
# Previews
# --------------------------------------------------------------------------
def setup_render():
    SCENE.render.engine = "CYCLES"
    SCENE.cycles.device = "CPU"
    SCENE.cycles.samples = SAMPLES
    SCENE.cycles.use_denoising = True
    SCENE.render.film_transparent = False
    SCENE.view_settings.view_transform = "Standard"
    SCENE.view_settings.look = "None"
    SCENE.render.resolution_x = 480
    SCENE.render.resolution_y = 1000
    world = bpy.data.worlds.new("Studio") if not SCENE.world else SCENE.world
    SCENE.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = lin((0.52, 0.52, 0.53))
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.6

    for name, energy, loc in (("Key", 3.2, (-1.4, -2.6, 2.8)), ("Fill", 1.2, (2.2, -1.5, 1.2)),
                              ("Rim", 2.0, (1.6, 2.4, 2.4))):
        sun = bpy.data.lights.new(name, "SUN")
        sun.energy, sun.angle = energy, math.radians(8)
        obj = link(bpy.data.objects.new(name, sun))
        obj.location = loc
        obj.rotation_euler = (Vector((0, 0, 1.0)) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    # Floor
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=4)
    floor = finish_bmesh("Floor", bm, material("Floor", (0.5, 0.5, 0.51), rough=0.9))

    cam_data = bpy.data.cameras.new("Cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = 1.82
    cam = link(bpy.data.objects.new("Cam", cam_data))
    SCENE.camera = cam
    return cam, floor


def render_views(cam):
    views = {
        "front": (0, -4, 0.0),
        "side": (4, 0, 90.0),
        "back": (0, 4, 180.0),
    }
    files = []
    for name, (x, y, _) in views.items():
        cam.data.type = "ORTHO"
        cam.location = (x, y, 0.86)
        cam.rotation_euler = (Vector((0, 0, 0.86)) - cam.location).to_track_quat("-Z", "Y").to_euler()
        SCENE.render.filepath = os.path.join(OUT_DIR, f"preview_{name}.png")
        bpy.ops.render.render(write_still=True)
        files.append(SCENE.render.filepath)
    # three-quarter close-up of the face
    cam.data.type = "PERSP"
    cam.data.lens = 85
    cam.location = (-0.45, -1.35, 1.56)
    cam.rotation_euler = (Vector((0, 0, 1.51)) - cam.location).to_track_quat("-Z", "Y").to_euler()
    SCENE.render.resolution_x = SCENE.render.resolution_y = 800
    SCENE.render.filepath = os.path.join(OUT_DIR, "preview_face.png")
    bpy.ops.render.render(write_still=True)
    files.append(SCENE.render.filepath)
    SCENE.render.resolution_x, SCENE.render.resolution_y = 480, 1000
    return files


def export(rig, meshes, floor):
    # glTF/VRM viewers don't do the inverted-hull trick, so leave outlines out
    for o in meshes:
        o.modifiers["Outline"].show_viewport = False
        o.modifiers["Outline"].show_render = False
    floor.hide_set(True)
    bpy.ops.object.select_all(action="DESELECT")
    for o in [rig, *meshes]:
        o.select_set(True)
    bpy.ops.export_scene.gltf(filepath=os.path.join(OUT_DIR, "sarah.glb"), export_format="GLB",
                              use_selection=True, export_apply=True, export_skins=True,
                              export_animations=False)
    for o in meshes:
        o.modifiers["Outline"].show_viewport = True
        o.modifiers["Outline"].show_render = True
    floor.hide_set(False)


def main():
    rig, meshes = build()
    cam, floor = setup_render()
    for o in SCENE.objects:
        o.select_set(False)
    if RENDER:
        print("[sarah] rendering previews")
        render_views(cam)
    print("[sarah] exporting")
    export(rig, meshes, floor)
    cam.data.type = "ORTHO"
    cam.location = (0, -4, 0.86)
    cam.rotation_euler = (math.radians(90), 0, 0)
    bpy.context.preferences.filepaths.save_version = 0   # no sarah.blend1 backups
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT_DIR, "sarah.blend"), compress=True)
    print("[sarah] done ->", OUT_DIR)


main()
