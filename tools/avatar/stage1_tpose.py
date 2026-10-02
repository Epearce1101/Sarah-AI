"""Stage 1: clean the rig and make her rest pose a T-pose (VRM requirement)."""
import bpy, sys, os
from mathutils import Matrix, Vector
src, texdir, out = sys.argv[-3:]
bpy.ops.wm.open_mainfile(filepath=src)

for im in bpy.data.images:
    im.filepath = os.path.join(texdir, os.path.basename(im.filepath)); im.reload()
for o in list(bpy.data.objects):
    if o.name.startswith("WGT_"): bpy.data.objects.remove(o)

arm = bpy.data.objects["Armature"]
arm.animation_data_clear()
for pb in arm.pose.bones:
    for c in list(pb.constraints): pb.constraints.remove(c)
    pb.matrix_basis = Matrix.Identity(4)
bpy.context.view_layer.update()

meshes = [o for o in bpy.data.objects if o.type == "MESH"]
# Bake every modifier except the armature into the mesh data.
for o in meshes:
    bpy.context.view_layer.objects.active = o
    for m in list(o.modifiers):
        if m.type != "ARMATURE":
            bpy.ops.object.modifier_apply(modifier=m.name)

# --- T-pose: aim arm chains straight out along +-X (world), palms down.
Mw = arm.matrix_world; Mi = Mw.inverted()
def aim(name, world_dir):
    pb = arm.pose.bones[name]
    bpy.context.view_layer.update()
    head = pb.head.copy(); cur = (pb.tail - pb.head).normalized()
    want = (Mi.to_3x3() @ Vector(world_dir)).normalized()
    R = cur.rotation_difference(want).to_matrix().to_4x4()
    pb.matrix = Matrix.Translation(head) @ R @ Matrix.Translation(-head) @ pb.matrix
    bpy.context.view_layer.update()

for side, sx in (("L", 1), ("R", -1)):
    for b in ("UpperArm", "LowerArm", "Hand"):
        aim(f"{b}_{side}", (sx, 0, 0))
    for f in ("Index", "Middle", "Ring", "Pinky"):
        for i in ("01", "02", "03"):
            aim(f"F_{f}_{i}_{side}", (sx, 0, 0))
bpy.context.view_layer.update()

# Apply the pose to the meshes, then make it the new rest pose.
for o in meshes:
    mod = next(m for m in o.modifiers if m.type == "ARMATURE")
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.modifier_apply(modifier=mod.name)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode="POSE")
bpy.ops.pose.select_all(action="SELECT")
bpy.ops.pose.armature_apply(selected=False)
bpy.ops.object.mode_set(mode="OBJECT")
for o in meshes:
    m = o.modifiers.new("Armature", "ARMATURE"); m.object = arm
bpy.ops.wm.save_as_mainfile(filepath=out)
for b in ("UpperArm_L", "LowerArm_L", "Hand_L", "F_Index_01_L"):
    bb = arm.data.bones[b]; print(b, tuple(round(x, 3) for x in Mw @ bb.head_local), tuple(round(x, 3) for x in Mw @ bb.tail_local))
