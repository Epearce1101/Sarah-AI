# Avatar converter

Turns the Blender model `sarah_refined.blend` (+ its `textures/` folder) into
the VRM 1.0 file Sarah's 3D body loads (`frontend/renderer/assets/vrm/sarah.vrm`).
Needs Blender 4.2 or newer.

```
python tools/avatar/build.py --blender "C:/Program Files/Blender Foundation/Blender 4.2/blender.exe" ^
    --source C:/Users/Zero/Desktop/sarah_refined.blend --textures C:/Users/Zero/Desktop/textures
```

Output: `tools/avatar/build/sarah.vrm` (git-ignored). Back up the old
`frontend/renderer/assets/vrm/sarah.vrm`, copy the new one over it, restart Sarah.

What each step does:

| Script | Step |
|---|---|
| `stage1_tpose.py` | Drops the rig's constraints and animation, bakes mirror/solidify modifiers, and re-rests the arms and fingers in a T-pose (VRM's rest pose; her source rest pose had the arms 24° down, which would push every animation's arms into her body). |
| `stage2_face.py` | Her face is driven by bones (eyelids, brows, jaw, lips) and has no shape keys, so each VRM expression is posed with those bones and baked into a shape key: `blink`, `blinkLeft`, `blinkRight`, `happy`, `sad`, `angry`, `surprised`, `relaxed`, and the mouth shapes `aa`, `ih`, `ee`, `oh`, `ou`. Tweak a face in the `EXPR` table (offsets in metres). |
| `stage3_export.py` | Her shaders output colour directly (unlit look). The eye and hair shaders are baked to textures, colour textures get their UV islands padded (no grey seams in the browser), and the model is exported as glTF. |
| `export_vrm.py` | Adds the `VRMC_vrm` extension: humanoid bone map, expressions, bone-based eye look-at, and unlit materials. |
