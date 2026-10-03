"""Turn the exported glTF into a VRM 1.0 (adds the VRMC_vrm extension).

Maps the rig's bones to VRM humanoid bones, binds the baked shape keys to
VRM expressions (plus Sarah's own grin, pout and blush), sets up bone-based
eye look-at, gives the hair bones from stage2b spring physics
(VRMC_springBone) and turns every material into a soft toon (MToon) one
with thin outlines.
"""
import json, struct, sys
import numpy as np

src, dst = sys.argv[1], sys.argv[2]
OUTLINE_WIDTH = 0.006  # screen-space, fraction of the view height
HAIR = {"stiffness": 1.2, "gravity": 0.12, "drag": 0.45}
blob = open(src, "rb").read()
jlen = struct.unpack("<I", blob[12:16])[0]
gltf = json.loads(blob[20:20 + jlen])
rest = blob[20 + jlen:]  # BIN chunk (header + data), untouched

HUMANOID = {
    "hips": "Hips", "spine": "Spine1", "chest": "Spine2", "upperChest": "Chest",
    "neck": "Neck", "head": "Head", "leftEye": "Eye_L", "rightEye": "Eye_R",
}
for side, s in (("left", "L"), ("right", "R")):
    HUMANOID.update({
        f"{side}Shoulder": f"Clavicle_{s}", f"{side}UpperArm": f"UpperArm_{s}",
        f"{side}LowerArm": f"LowerArm_{s}", f"{side}Hand": f"Hand_{s}",
        f"{side}UpperLeg": f"Thigh_{s}", f"{side}LowerLeg": f"Calf_{s}",
        f"{side}Foot": f"Foot_{s}", f"{side}Toes": f"Toe_{s}",
        f"{side}ThumbMetacarpal": f"F_Thumb_01_{s}", f"{side}ThumbProximal": f"F_Thumb_02_{s}",
        f"{side}ThumbDistal": f"F_Thumb_03_{s}",
    })
    for vrm_f, rig_f in (("Index", "Index"), ("Middle", "Middle"), ("Ring", "Ring"), ("Little", "Pinky")):
        for seg, i in (("Proximal", "01"), ("Intermediate", "02"), ("Distal", "03")):
            HUMANOID[f"{side}{vrm_f}{seg}"] = f"F_{rig_f}_{i}_{s}"

node_index = {n.get("name"): i for i, n in enumerate(gltf["nodes"])}
human_bones = {}
for vrm_name, bone in HUMANOID.items():
    if bone not in node_index:
        raise SystemExit(f"missing bone {bone} for {vrm_name}")
    human_bones[vrm_name] = {"node": node_index[bone]}

PRESETS = ["happy", "angry", "sad", "relaxed", "surprised", "aa", "ih", "ou", "ee", "oh",
           "blink", "blinkLeft", "blinkRight"]
binds = {p: [] for p in PRESETS}
for ni, node in enumerate(gltf["nodes"]):
    if "mesh" not in node: continue
    names = (gltf["meshes"][node["mesh"]].get("extras") or {}).get("targetNames", [])
    for ti, name in enumerate(names):
        if name in binds: binds[name].append({"node": ni, "index": ti, "weight": 1.0})
missing = [p for p, b in binds.items() if not b]
if missing: raise SystemExit(f"expressions without shape keys: {missing}")
# Emotions keep the eyes free to blink; blinking hides the look-at drift.
expressions = {}
mat_index = {m["name"]: i for i, m in enumerate(gltf["materials"])}
custom = {}
for name in ("grin", "pout"):
    b = [{"node": ni, "index": ti, "weight": 1.0}
         for ni, node in enumerate(gltf["nodes"]) if "mesh" in node
         for ti, t in enumerate((gltf["meshes"][node["mesh"]].get("extras") or {}).get("targetNames", [])) if t == name]
    if b:
        custom[name] = {"morphTargetBinds": b, "isBinary": False,
                        "overrideBlink": "none", "overrideLookAt": "none", "overrideMouth": "none"}
# The blush decal starts fully transparent; the "blush" expression fades it in.
if "Sarah_Blush" in mat_index:
    gltf["materials"][mat_index["Sarah_Blush"]]["pbrMetallicRoughness"]["baseColorFactor"] = [1, 1, 1, 0]
    custom["blush"] = {"materialColorBinds": [{"material": mat_index["Sarah_Blush"], "type": "color",
                                               "targetValue": [1, 1, 1, 1]}],
                       "isBinary": False, "overrideBlink": "none", "overrideLookAt": "none", "overrideMouth": "none"}
for p in PRESETS:
    e = {"morphTargetBinds": binds[p], "isBinary": False,
         "overrideBlink": "none", "overrideLookAt": "none", "overrideMouth": "none"}
    if p in ("blink", "blinkLeft", "blinkRight"): e["overrideLookAt"] = "block"
    if p == "surprised": e["overrideBlink"] = "blend"
    expressions[p] = e

range_map = lambda out: {"inputMaxValue": 90.0, "outputScale": out}
vrm = {
    "specVersion": "1.0",
    "meta": {
        "name": "Sarah",
        "version": "1.0",
        "authors": ["Polly model by its original author; converted for Sarah"],
        "licenseUrl": "https://vrm.dev/licenses/1.0/",
        "avatarPermission": "onlyAuthor",
        "allowExcessivelyViolentUsage": False,
        "allowExcessivelySexualUsage": False,
        "commercialUsage": "personalNonProfit",
        "allowPoliticalOrReligiousUsage": False,
        "allowAntisocialOrHateUsage": False,
        "creditNotation": "required",
        "allowRedistribution": False,
        "modification": "prohibited",
    },
    "humanoid": {"humanBones": human_bones},
    "expressions": {"preset": expressions, "custom": custom},
    "lookAt": {
        "offsetFromHeadBone": [0.0, 0.047, 0.056],
        "type": "bone",
        "rangeMapHorizontalInner": range_map(12.0),
        "rangeMapHorizontalOuter": range_map(12.0),
        "rangeMapVerticalDown": range_map(8.0),
        "rangeMapVerticalUp": range_map(8.0),
    },
    "firstPerson": {"meshAnnotations": []},
}

# --- Toon materials ------------------------------------------------------------
# The textures already carry painted shading, so the toon shade is gentle: a
# warm 20% darker band on the side away from the light, plus a thin outline
# on her skin, clothes and hair (not on eyes, glasses or the blush).
OUTLINED = {"Sarah_Cloth", "Sarah_Head", "Sarah_Hair"}
for m in gltf["materials"]:
    if m["name"] == "Sarah_Hair": m["doubleSided"] = True
    pbr = m.setdefault("pbrMetallicRoughness", {})
    tex = pbr.get("baseColorTexture")
    flat = m["name"] in ("Sarah_EyeL", "Sarah_EyeR", "Sarah_Blush", "Sarah_Glass")
    mtoon = {
        "specVersion": "1.0",
        "transparentWithZWrite": False,
        "renderQueueOffsetNumber": 0,
        "shadeColorFactor": [1.0, 1.0, 1.0] if flat else [0.80, 0.72, 0.76],
        "shadingShiftFactor": -0.05,
        "shadingToonyFactor": 0.9,
        "giEqualizationFactor": 0.9,
        "parametricRimColorFactor": [0.0, 0.0, 0.0] if flat else [0.10, 0.08, 0.12],
        "parametricRimFresnelPowerFactor": 4.0,
        "parametricRimLiftFactor": 0.0,
        "rimLightingMixFactor": 1.0,
        "outlineWidthMode": "screenCoordinates" if m["name"] in OUTLINED else "none",
        "outlineWidthFactor": OUTLINE_WIDTH if m["name"] in OUTLINED else 0.0,
        "outlineColorFactor": [0.22, 0.13, 0.13],
        "outlineLightingMixFactor": 1.0,
    }
    if tex: mtoon["shadeMultiplyTexture"] = {"index": tex["index"]}
    m["extensions"] = {"VRMC_materials_mtoon": mtoon}

# --- Hair physics -----------------------------------------------------------------
def world_matrices():
    """Each node's rest-pose world matrix (glTF space)."""
    parent = {c: i for i, n in enumerate(gltf["nodes"]) for c in n.get("children", [])}
    def local(n):
        if "matrix" in n: return np.array(n["matrix"], float).reshape(4, 4).T
        x, y, z, w = n.get("rotation", [0, 0, 0, 1])
        R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        M = np.eye(4); M[:3, :3] = R * np.array(n.get("scale", [1, 1, 1])); M[:3, 3] = n.get("translation", [0, 0, 0])
        return M
    cache = {}
    def world(i):
        if i not in cache:
            cache[i] = (world(parent[i]) if i in parent else np.eye(4)) @ local(gltf["nodes"][i])
        return cache[i]
    return world

def blender_to_gltf(p):
    x, y, z = p
    return np.array([x, z, -y, 1.0])

def collider(bone, shape, center, radius, tail=None):
    """A collider on `bone`; positions are Blender world coordinates (metres)."""
    inv = np.linalg.inv(world(node_index[bone]))
    to_local = lambda p: [round(float(v), 5) for v in (inv @ blender_to_gltf(p))[:3]]
    s = {"offset": to_local(center), "radius": radius}
    if tail is not None: s["tail"] = to_local(tail)
    return {"node": node_index[bone], "shape": {shape: s}}

hair_chains = sorted({n.rsplit("_", 1)[0] for n in node_index if n and n.startswith("Hair_")})
if hair_chains:
    world = world_matrices()
    # Measured on the model: the joints sit on the hair surface 12-14 cm from
    # this head centre, so the head sphere keeps the hair out of her face
    # without pushing it at rest.
    colliders = [
        collider("Head", "sphere", (0.0, 0.02, 1.47), 0.10),
        collider("Neck", "capsule", (0.0, 0.037, 1.30), 0.045, tail=(0.0, 0.025, 1.40)),
        collider("Chest", "sphere", (0.0, 0.03, 1.20), 0.08),
        collider("Clavicle_L", "sphere", (0.11, 0.04, 1.25), 0.035),
        collider("Clavicle_R", "sphere", (-0.11, 0.04, 1.25), 0.035),
    ]
    springs = []
    for chain in hair_chains:
        joints = []
        for i in (1, 2, 3):
            joints.append({"node": node_index[f"{chain}_{i}"], "hitRadius": 0.02, "stiffness": HAIR["stiffness"],
                           "gravityPower": HAIR["gravity"], "gravityDir": [0.0, -1.0, 0.0], "dragForce": HAIR["drag"]})
        springs.append({"name": chain, "joints": joints, "colliderGroups": [0]})
    gltf["extensions"] = gltf.get("extensions", {})
    gltf["extensions"]["VRMC_springBone"] = {
        "specVersion": "1.0",
        "colliders": colliders,
        "colliderGroups": [{"name": "Body", "colliders": list(range(len(colliders)))}],
        "springs": springs,
    }

gltf.setdefault("extensions", {})["VRMC_vrm"] = vrm
used = set(gltf.get("extensionsUsed", [])) | {"VRMC_vrm", "VRMC_materials_mtoon"}
if hair_chains: used.add("VRMC_springBone")
used.discard("KHR_materials_unlit")
gltf["extensionsUsed"] = sorted(used)
gltf["asset"]["generator"] = "Sarah VRM converter (Blender glTF + export_vrm.py)"

j = json.dumps(gltf, separators=(",", ":")).encode()
j += b" " * (-len(j) % 4)
total = 12 + 8 + len(j) + len(rest)
out = struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(j), b"JSON") + j + rest
open(dst, "wb").write(out)
print(f"wrote {dst}: {total / 1e6:.1f} MB, {len(human_bones)} humanoid bones, "
      f"{sum(len(b) for b in binds.values())} expression binds, custom {sorted(custom)}, "
      f"{len(hair_chains)} hair springs")
