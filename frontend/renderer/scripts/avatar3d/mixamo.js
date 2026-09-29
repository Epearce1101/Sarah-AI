// Mixamo animations on Sarah's VRM body: an FBX from mixamo.com (skeleton
// "mixamorig...") is retargeted onto her humanoid bones and scaled to her
// height. Adapted from the three-vrm example "loadMixamoAnimation" (MIT).
import * as THREE from "three";
import { FBXLoader } from "three/addons/loaders/FBXLoader.js";

export const MIXAMO_TO_VRM = {
  mixamorigHips: "hips", mixamorigSpine: "spine", mixamorigSpine1: "chest", mixamorigSpine2: "upperChest",
  mixamorigNeck: "neck", mixamorigHead: "head",
  mixamorigLeftShoulder: "leftShoulder", mixamorigLeftArm: "leftUpperArm", mixamorigLeftForeArm: "leftLowerArm",
  mixamorigLeftHand: "leftHand",
  mixamorigLeftHandThumb1: "leftThumbMetacarpal", mixamorigLeftHandThumb2: "leftThumbProximal", mixamorigLeftHandThumb3: "leftThumbDistal",
  mixamorigLeftHandIndex1: "leftIndexProximal", mixamorigLeftHandIndex2: "leftIndexIntermediate", mixamorigLeftHandIndex3: "leftIndexDistal",
  mixamorigLeftHandMiddle1: "leftMiddleProximal", mixamorigLeftHandMiddle2: "leftMiddleIntermediate", mixamorigLeftHandMiddle3: "leftMiddleDistal",
  mixamorigLeftHandRing1: "leftRingProximal", mixamorigLeftHandRing2: "leftRingIntermediate", mixamorigLeftHandRing3: "leftRingDistal",
  mixamorigLeftHandPinky1: "leftLittleProximal", mixamorigLeftHandPinky2: "leftLittleIntermediate", mixamorigLeftHandPinky3: "leftLittleDistal",
  mixamorigRightShoulder: "rightShoulder", mixamorigRightArm: "rightUpperArm", mixamorigRightForeArm: "rightLowerArm",
  mixamorigRightHand: "rightHand",
  mixamorigRightHandThumb1: "rightThumbMetacarpal", mixamorigRightHandThumb2: "rightThumbProximal", mixamorigRightHandThumb3: "rightThumbDistal",
  mixamorigRightHandIndex1: "rightIndexProximal", mixamorigRightHandIndex2: "rightIndexIntermediate", mixamorigRightHandIndex3: "rightIndexDistal",
  mixamorigRightHandMiddle1: "rightMiddleProximal", mixamorigRightHandMiddle2: "rightMiddleIntermediate", mixamorigRightHandMiddle3: "rightMiddleDistal",
  mixamorigRightHandRing1: "rightRingProximal", mixamorigRightHandRing2: "rightRingIntermediate", mixamorigRightHandRing3: "rightRingDistal",
  mixamorigRightHandPinky1: "rightLittleProximal", mixamorigRightHandPinky2: "rightLittleIntermediate", mixamorigRightHandPinky3: "rightLittleDistal",
  mixamorigLeftUpLeg: "leftUpperLeg", mixamorigLeftLeg: "leftLowerLeg", mixamorigLeftFoot: "leftFoot", mixamorigLeftToeBase: "leftToes",
  mixamorigRightUpLeg: "rightUpperLeg", mixamorigRightLeg: "rightLowerLeg", mixamorigRightFoot: "rightFoot", mixamorigRightToeBase: "rightToes",
};

const loader = new FBXLoader();

// Some exports prefix bone names ("mixamorig1:Hips", "mixamorig:Hips").
const canonical = (name) => name.replace(/^mixamorig\d*:?/, "mixamorig");

export async function loadMixamoClip(url, vrm) {
  return retargetMixamo(await loader.loadAsync(url), vrm);
}

// asset: the loaded FBX group (bones named mixamorig..., .animations).
export function retargetMixamo(asset, vrm) {
  const source = THREE.AnimationClip.findByName(asset.animations, "mixamo.com") || asset.animations[0];
  if (!source) throw new Error("no animation in that FBX");
  const nodes = {};
  asset.traverse((o) => { if (o.name) nodes[canonical(o.name)] = o; });
  const hips = nodes.mixamorigHips;
  if (!hips) throw new Error("not a Mixamo skeleton (no mixamorig Hips)");

  const restInverse = new THREE.Quaternion();
  const parentRest = new THREE.Quaternion();
  const q = new THREE.Quaternion();
  const v = new THREE.Vector3();
  const vrmHipsY = vrm.humanoid.getNormalizedBoneNode("hips").getWorldPosition(v).y;
  const vrmRootY = vrm.scene.getWorldPosition(v).y;
  const scale = Math.abs(vrmHipsY - vrmRootY) / Math.max(1e-6, hips.position.y);
  const vrm0 = vrm.meta?.metaVersion === "0";

  const tracks = [];
  for (const track of source.tracks) {
    const [rawBone, property] = track.name.split(".");
    const rigName = canonical(rawBone);
    const vrmNode = vrm.humanoid.getNormalizedBoneNode(MIXAMO_TO_VRM[rigName])?.name;
    const rigNode = nodes[rigName];
    if (!vrmNode || !rigNode) continue;
    rigNode.getWorldQuaternion(restInverse).invert();
    rigNode.parent.getWorldQuaternion(parentRest);
    if (track instanceof THREE.QuaternionKeyframeTrack) {
      const values = track.values.slice();
      for (let i = 0; i < values.length; i += 4) {
        q.fromArray(values, i).premultiply(parentRest).multiply(restInverse).toArray(values, i);
      }
      tracks.push(new THREE.QuaternionKeyframeTrack(`${vrmNode}.${property}`, track.times,
        values.map((x, i) => (vrm0 && i % 2 === 0 ? -x : x))));
    } else if (track instanceof THREE.VectorKeyframeTrack && property === "position" && rigName === "mixamorigHips") {
      tracks.push(new THREE.VectorKeyframeTrack(`${vrmNode}.${property}`, track.times,
        track.values.map((x, i) => (vrm0 && i % 3 !== 1 ? -x : x) * scale)));
    }
  }
  return new THREE.AnimationClip("mixamo", source.duration, tracks);
}

// "Hip Hop Dancing (1).fbx" -> { id: "mx_hip_hop_dancing_1", words: ["hip", "hop", "dancing"] }
export function describeFile(file) {
  const base = file.replace(/\.(fbx|vrma)$/i, "");
  const slug = base.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  const words = slug.split("_").filter((w) => w.length >= 3 && !/^\d+$/.test(w));
  return { id: `mx_${slug}`, slug, words, loop: /idle|danc|walk|run|loop|sway|breath/.test(slug) };
}
