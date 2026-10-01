"""Turn the generated sarah.blend into a .vrm the app can load.

Needs the "VRM format" add-on (https://vrm-addon-for-blender.info) installed
and enabled in Blender, or its source folder passed with --addon.

    blender --background --python blender/export_vrm.py
    blender --background --python blender/export_vrm.py -- --blend blender/out/sarah.blend --out blender/out/sarah.vrm

What it does: opens the scene, drops the render-only outline shells, swings
the arms up into the T-pose VRM expects and makes that the rest pose, maps
the humanoid bones, makes the tail springy, fills in the meta and exports.
The .blend on disk is left unchanged.

To use it in the app, copy the result over
frontend/renderer/assets/vrm/sarah.vrm (back up your current one first).
"""

import os
import sys

import bpy  # noqa: I001  (bpy first: the pip 'bpy' module only exposes the rest after it)
import addon_utils
from mathutils import Matrix, Vector

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
HERE = os.path.dirname(os.path.abspath(__file__))
BLEND = os.path.join(HERE, "out", "sarah.blend")
OUT = os.path.join(HERE, "out", "sarah.vrm")
for i, a in enumerate(argv):
    if a == "--blend":
        BLEND = os.path.abspath(argv[i + 1])
    elif a == "--out":
        OUT = os.path.abspath(argv[i + 1])
    elif a == "--addon":
        sys.path.insert(0, os.path.abspath(argv[i + 1]))

bpy.ops.wm.open_mainfile(filepath=BLEND)



def vrm_ready():
    return "vrm_addon_extension" in bpy.types.Armature.bl_rna.properties


if not vrm_ready():
    for name in ("io_scene_vrm", "bl_ext.blender_org.vrm", "bl_ext.user_default.vrm"):
        try:
            if addon_utils.enable(name, default_set=True):
                break
        except Exception:  # noqa: BLE001 - try the next install location
            pass
if not vrm_ready():
    sys.exit("VRM add-on not found: install it in Blender, or pass --addon <path to src folder>")

rig = bpy.data.objects["SarahRig"]
meshes = [o for o in bpy.data.objects if o.type == "MESH" and o.parent == rig]
for o in [o for o in bpy.data.objects if o.type in {"LIGHT", "CAMERA"} or o.name == "Floor"]:
    bpy.data.objects.remove(o)

# Outline shells are for Blender renders only (VRM viewers draw their own)
for o in meshes:
    if "Outline" in o.modifiers:
        o.modifiers.remove(o.modifiers["Outline"])
    slot = o.data.materials.find("Outline")
    if slot >= 0:
        o.data.materials.pop(index=slot)


# --- T-pose: arms straight out along X, then make that the rest pose -------
def aim(pose_bone, direction):
    """Rotate a pose bone (armature space) so it points along direction."""
    bpy.context.view_layer.update()
    m = pose_bone.matrix.copy()
    head = m.translation.copy()
    cur = (m.to_3x3() @ Vector((0, 1, 0))).normalized()
    rot = cur.rotation_difference(direction.normalized()).to_matrix().to_4x4()
    m = Matrix.Translation(head) @ rot @ Matrix.Translation(-head) @ m
    pose_bone.matrix = m


bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="POSE")
for sfx, s in (("L", 1), ("R", -1)):
    for name in ("UpperArm", "LowerArm", "Hand"):
        aim(rig.pose.bones[f"{name}.{sfx}"], Vector((s, 0, 0)))
bpy.ops.object.mode_set(mode="OBJECT")
bpy.context.view_layer.update()

for o in meshes:
    with bpy.context.temp_override(object=o, active_object=o):
        bpy.ops.object.modifier_apply(modifier="Armature")
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="POSE")
bpy.ops.pose.armature_apply(selected=False)
bpy.ops.object.mode_set(mode="OBJECT")
for o in meshes:
    mod = o.modifiers.new("Armature", "ARMATURE")
    mod.object = rig

# --- VRM data --------------------------------------------------------------
ext = rig.data.vrm_addon_extension
ext.spec_version = "1.0"
human = ext.vrm1.humanoid.human_bones
mapping = {
    "hips": "Hips", "spine": "Spine", "chest": "Chest", "upper_chest": "UpperChest",
    "neck": "Neck", "head": "Head",
}
for side, sfx in (("left", "L"), ("right", "R")):
    mapping.update({
        f"{side}_shoulder": f"Shoulder.{sfx}", f"{side}_upper_arm": f"UpperArm.{sfx}",
        f"{side}_lower_arm": f"LowerArm.{sfx}", f"{side}_hand": f"Hand.{sfx}",
        f"{side}_upper_leg": f"UpperLeg.{sfx}", f"{side}_lower_leg": f"LowerLeg.{sfx}",
        f"{side}_foot": f"Foot.{sfx}", f"{side}_toes": f"Toes.{sfx}",
    })
for vrm_name, bone in mapping.items():
    getattr(human, vrm_name).node.bone_name = bone

meta = ext.vrm1.meta
meta.vrm_name = "Sarah"
meta.version = "1.0"
if not len(meta.authors):
    meta.authors.add()
meta.authors[0].value = "Sarah-AI"

# Springy tail
springs = ext.spring_bone1.springs
spring = springs.add()
spring.vrm_name = "Tail"
for i in range(1, 7):
    joint = spring.joints.add()
    joint.node.bone_name = f"Tail.{i:03d}"
    joint.stiffness = 1.6 - 0.18 * i
    joint.gravity_power = 0.25
    joint.drag_force = 0.45
    joint.hit_radius = 0.012

os.makedirs(os.path.dirname(OUT), exist_ok=True)
result = bpy.ops.export_scene.vrm(filepath=OUT, armature_object_name=rig.name, ignore_warning=True)
print("[sarah] VRM export:", result, "->", OUT)
