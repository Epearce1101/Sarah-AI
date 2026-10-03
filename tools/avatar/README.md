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
| `stage2_face.py` | Her face is driven by bones (eyelids, brows, jaw, lips) and has no shape keys, so each VRM expression is posed with those bones and baked into a shape key: `blink`, `blinkLeft`, `blinkRight`, `happy`, `sad`, `angry`, `surprised`, `relaxed`, the mouth shapes `aa`, `ih`, `ee`, `oh`, `ou`, and her own `grin` (open smile with teeth) and `pout`. Tweak a face in the `EXPR` table (offsets in metres). |
| `stage2b_extras.py` | Adds two soft blush ovals on her cheeks (invisible until she blushes) and seven short hair bone chains (front locks, sides, back) with the hair below her temples weighted to them, so the hair can sway. |
| `stage3_export.py` | Her shaders output colour directly. The eye and hair shaders are baked to textures, colour textures get their UV islands padded (no grey seams in the browser), and the model is exported as glTF. |
| `export_vrm.py` | Adds the VRM extensions: humanoid bone map, expressions (plus custom `grin`, `pout` and `blush`), bone-based eye look-at, spring-bone hair physics with head/neck/chest/shoulder colliders (`VRMC_springBone`, tune with `HAIR`), and toon materials with thin outlines (`VRMC_materials_mtoon`, tune with `OUTLINE_WIDTH`). |

## Poses

`make_pose_vrma.py` writes held poses as VRM animation files her body plays
like any other clip. `poses/lying_poses.json` holds her rest skeleton and each
pose's bone rotations (VRM normalized space); `poses/lying_poses_spec.json` is
how they were shaped (limb directions relative to her body). Rebuild with:

```
python tools/avatar/make_pose_vrma.py tools/avatar/poses/lying_poses.json frontend/renderer/assets/vrm/animations
```

| File | Gesture | Pose |
|---|---|---|
| `pose_lie_front.vrma` | `lie_down` | On her stomach, chin in her hand, feet up and swaying |
| `pose_lie_side.vrma` | `lie_side` | On her side, propped on an elbow, other hand on her hip |

Each holds for 12 s with slow breathing; the camera switches to a low, wide
"floor" framing while she's down and back when she gets up.
