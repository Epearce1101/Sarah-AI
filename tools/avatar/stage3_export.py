"""Stage 3: flatten the custom toon shaders into plain textures and export glTF.

The original materials plug colour straight into the surface (unlit look),
so the eye and hair shaders are baked to images and every material becomes
a simple base-colour texture; export_vrm.py then marks them unlit.
"""
import bpy, sys, os
import numpy as np
src, outdir = sys.argv[-2:]
bpy.ops.wm.open_mainfile(filepath=src)
sc = bpy.context.scene
sc.render.engine = "CYCLES"; sc.cycles.samples = 4; sc.cycles.device = "CPU"

def bake(mat_names, obj_name, size, img_name):
    obj = bpy.data.objects[obj_name]
    img = bpy.data.images.new(img_name, size, size)
    for mn in mat_names:
        nt = bpy.data.materials[mn].node_tree
        n = nt.nodes.new("ShaderNodeTexImage"); n.image = img
        nt.nodes.active = n
    for o in bpy.context.view_layer.objects: o.select_set(False)
    obj.select_set(True); bpy.context.view_layer.objects.active = obj
    bpy.ops.object.bake(type="EMIT", margin=8)
    img.filepath_raw = os.path.join(outdir, img_name + ".png"); img.file_format = "PNG"; img.save()
    return img

# Each eye material reads a different channel of the mask, so bake them
# to separate images (their UVs overlap).
eyeL = bake(["MAT_Polly_EyeL"], "GEO_Polly_Eyes", 512, "TEX_Sarah_eyeL")
for mn in ("MAT_Polly_EyeL",):
    nt = bpy.data.materials[mn].node_tree; [nt.nodes.remove(n) for n in list(nt.nodes) if n.bl_idname == "ShaderNodeTexImage" and n.image == eyeL]
eyeR = bake(["MAT_Polly_EyeR"], "GEO_Polly_Eyes", 512, "TEX_Sarah_eyeR")
hair = bake(["MAT_Polly_Hair"], "GEO_Polly_Hair", 1024, "TEX_Sarah_hair")
# Soften the baked shine band: pull bright pixels most of the way back
# toward the base hair brown so it reads as a sheen, not a white stripe.
px = np.array(hair.pixels[:], dtype=np.float32).reshape(-1, 4)
lum = px[:, :3].mean(axis=1)
brown = np.array([0.05, 0.035, 0.03], dtype=np.float32)  # linear
bright = lum > 0.12
px[bright, :3] = brown + (px[bright, :3] - brown) * 0.35
hair.pixels[:] = px.ravel(); hair.update(); hair.save()

def flat(mat_name, image=None, color=(1, 1, 1, 1), alpha=None, image_alpha=False):
    m = bpy.data.materials[mat_name]; nt = m.node_tree
    for n in list(nt.nodes): nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial"); bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs[0], out.inputs[0])
    bsdf.inputs["Base Color"].default_value = color
    bsdf.inputs["Roughness"].default_value = 1.0
    if image:
        t = nt.nodes.new("ShaderNodeTexImage"); t.image = image
        nt.links.new(t.outputs["Color"], bsdf.inputs["Base Color"])
        if image_alpha:
            nt.links.new(t.outputs["Alpha"], bsdf.inputs["Alpha"])
    if alpha is not None or image_alpha:
        if alpha is not None: bsdf.inputs["Alpha"].default_value = alpha
        m.surface_render_method = "BLENDED"
    else:
        m.surface_render_method = "DITHERED"

def padded(image, mat_name, px=24):
    """Copy of `image` with colour bled outward from the UV islands that use
    it, so texture filtering at seams never picks up the background."""
    w, h = image.size
    m = np.zeros((h, w), bool)
    def fill_tri(p0, p1, p2):
        xs, ys = (p0[0], p1[0], p2[0]), (p0[1], p1[1], p2[1])
        x0, x1 = max(int(min(xs)), 0), min(int(max(xs)) + 1, w - 1)
        y0, y1 = max(int(min(ys)), 0), min(int(max(ys)) + 1, h - 1)
        if x1 < x0 or y1 < y0: return
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        def edge(a, b): return (b[0] - a[0]) * (gy - a[1]) - (b[1] - a[1]) * (gx - a[0])
        e0, e1, e2 = edge(p0, p1), edge(p1, p2), edge(p2, p0)
        inside = ((e0 >= 0) & (e1 >= 0) & (e2 >= 0)) | ((e0 <= 0) & (e1 <= 0) & (e2 <= 0))
        m[y0:y1 + 1, x0:x1 + 1] |= inside
    for o in bpy.data.objects:
        if o.type != "MESH": continue
        idx = [i for i, mt in enumerate(o.data.materials) if mt and mt.name == mat_name]
        if not idx: continue
        uv = o.data.uv_layers.active.data
        for poly in o.data.polygons:
            if poly.material_index not in idx: continue
            pts = [(uv[li].uv.x * w, (1 - uv[li].uv.y) * h) for li in poly.loop_indices]
            for k in range(1, len(pts) - 1): fill_tri(pts[0], pts[k], pts[k + 1])
    a = np.array(image.pixels[:], dtype=np.float32).reshape(h, w, 4)[::-1].copy()
    for _ in range(px):
        acc = np.zeros_like(a); cnt = np.zeros((h, w), np.float32)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            sm = np.roll(m, (dy, dx), (0, 1)); sa = np.roll(a, (dy, dx), (0, 1))
            acc[sm] += sa[sm]; cnt[sm] += 1
        grow = (~m) & (cnt > 0)
        a[grow] = acc[grow] / cnt[grow][:, None]; m = m | grow
    out = bpy.data.images.new(image.name.replace(".png", "") + "_padded", w, h)
    out.pixels[:] = a[::-1].ravel(); out.update()
    out.filepath_raw = os.path.join(outdir, out.name + ".png"); out.file_format = "PNG"; out.save()
    return out

imgs = {i.name: i for i in bpy.data.images}
imgs["TEX_Polly_cloth_color.png"] = padded(imgs["TEX_Polly_cloth_color.png"], "MAT_Polly_Cloth")
imgs["TEX_Polly_head_color.png"] = padded(imgs["TEX_Polly_head_color.png"], "MAT_Polly_Head")
flat("MAT_Polly_Cloth", imgs["TEX_Polly_cloth_color.png"])
flat("MAT_Polly_Head", imgs["TEX_Polly_head_color.png"])
flat("MAT_Polly_EyeL", eyeL)
flat("MAT_Polly_EyeR", eyeR)
flat("MAT_Polly_Hair", hair)
flat("MAT_Polly_Glass", None, (0.85, 0.92, 1.0, 1.0), alpha=0.12)
if "MAT_Polly_Blush" in bpy.data.materials:  # from stage2b; export_vrm.py hides it until she blushes
    flat("MAT_Polly_Blush", bpy.data.images["TEX_Sarah_blush"], image_alpha=True)

# Rename to Sarah's own names for the VRM.
for m in bpy.data.materials: m.name = m.name.replace("MAT_Polly_", "Sarah_")
for o in bpy.data.objects:
    if o.type == "MESH": o.name = o.name.replace("GEO_Polly_", "Sarah_")
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(outdir, "stage3.blend"))

bpy.ops.export_scene.gltf(
    filepath=os.path.join(outdir, "sarah_raw.glb"), export_format="GLB",
    export_apply=False, export_skins=True, export_morph=True, export_morph_normal=False,
    export_animations=False, export_def_bones=False, export_yup=True,
    export_image_format="AUTO", export_extras=False, export_cameras=False, export_lights=False,
)
print("EXPORTED")
