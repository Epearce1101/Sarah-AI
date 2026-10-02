"""Turn the exported glTF into a VRM 1.0 (adds the VRMC_vrm extension).

Maps the rig's bones to VRM humanoid bones, binds the baked shape keys to
VRM expressions, sets up bone-based eye look-at, and marks every material
unlit (the original shaders are unlit; shading is painted in the textures).
"""
import json, struct, sys

src, dst = sys.argv[1], sys.argv[2]
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
    "expressions": {"preset": expressions},
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

for m in gltf["materials"]:
    m.setdefault("extensions", {})["KHR_materials_unlit"] = {}
    if m["name"] == "Sarah_Hair": m["doubleSided"] = True

gltf.setdefault("extensions", {})["VRMC_vrm"] = vrm
used = set(gltf.get("extensionsUsed", [])) | {"VRMC_vrm", "KHR_materials_unlit"}
gltf["extensionsUsed"] = sorted(used)
gltf["asset"]["generator"] = "Sarah VRM converter (Blender glTF + export_vrm.py)"

j = json.dumps(gltf, separators=(",", ":")).encode()
j += b" " * (-len(j) % 4)
total = 12 + 8 + len(j) + len(rest)
out = struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(j), b"JSON") + j + rest
open(dst, "wb").write(out)
print(f"wrote {dst}: {total / 1e6:.1f} MB, {len(human_bones)} humanoid bones, "
      f"{sum(len(b) for b in binds.values())} expression binds")
