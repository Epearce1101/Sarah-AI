// Mixamo -> VRM animation retargeting.
//
// Based on pixiv/three-vrm's loadMixamoAnimation example
// (MIT License, Copyright (c) 2019-2026 pixiv Inc.), extended to:
//   - read .glb/.gltf as well as .fbx
//   - handle armatures that are rotated or scaled (common in .glb exports)
//   - optionally keep animations "in place" so Sarah stays in frame
//
// How it works: a Mixamo animation only stores how each Mixamo bone rotates
// over time. Every VRM model has the same standard humanoid bones, so each
// track is renamed to the matching VRM bone and converted so it's relative
// to the rest pose. Hip movement is scaled to the VRM's size.
import * as THREE from 'three';
import { FBXLoader } from 'three/addons/loaders/FBXLoader.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { mixamoVRMRigMap } from './mixamoVRMRigMap.js';

const _restRotationInverse = new THREE.Quaternion();
const _parentRestWorldRotation = new THREE.Quaternion();
const _quat = new THREE.Quaternion();
const _vec = new THREE.Vector3();

// "mixamorig:Hips" (raw FBX) and "mixamorigHips" (three.js sanitised) are the same bone
function normalizeBoneName(name) {
  return name.replace(/[:\s]/g, '');
}

function findMixamoNode(root, mixamoName) {
  let found = root.getObjectByName(mixamoName);
  if (found) return found;
  root.traverse((obj) => {
    if (!found && normalizeBoneName(obj.name) === mixamoName) found = obj;
  });
  return found;
}

/**
 * Load a Mixamo-rigged animation file (.fbx, .glb or .gltf).
 * @returns {Promise<{root: THREE.Object3D, clips: THREE.AnimationClip[]}>}
 */
export async function loadMixamoFile(url) {
  const path = url.split(/[?#]/)[0].toLowerCase();
  let root;
  let clips;
  if (path.endsWith('.fbx')) {
    root = await new FBXLoader().loadAsync(url);
    clips = root.animations;
  } else if (path.endsWith('.glb') || path.endsWith('.gltf')) {
    const gltf = await new GLTFLoader().loadAsync(url);
    root = gltf.scene;
    clips = gltf.animations;
  } else {
    throw new Error(`Unsupported animation file (use .fbx, .glb or .gltf): ${url}`);
  }
  if (!clips || clips.length === 0) throw new Error(`No animation found in ${url}`);
  root.updateMatrixWorld(true);
  return { root, clips };
}

/**
 * Convert one Mixamo clip so it plays on `vrm`.
 * @param {THREE.AnimationClip} clip
 * @param {THREE.Object3D} sourceRoot  the scene the clip came from (rest pose)
 * @param {import('@pixiv/three-vrm').VRM} vrm
 * @param {{name?: string, inPlace?: boolean}} options
 */
export function retargetClip(clip, sourceRoot, vrm, { name = clip.name, inPlace = true } = {}) {
  const hipsNode = findMixamoNode(sourceRoot, 'mixamorigHips');
  if (!hipsNode) {
    throw new Error('Not a Mixamo-rigged animation (no mixamorigHips bone found)');
  }

  // Hip height in world units, measured from the model's feet (its root)
  const rootY = sourceRoot.getWorldPosition(_vec).y;
  const motionHipsHeight = hipsNode.getWorldPosition(_vec).y - rootY;
  const vrmHipsHeight = vrm.humanoid.normalizedRestPose.hips.position[1];
  const hipsScale = motionHipsHeight > 1e-6 ? vrmHipsHeight / motionHipsHeight : 1;
  const isVRM0 = vrm.meta?.metaVersion === '0';

  const tracks = [];
  for (const track of clip.tracks) {
    const { nodeName, propertyName } = THREE.PropertyBinding.parseTrackName(track.name);
    const mixamoName = normalizeBoneName(nodeName);
    const vrmBoneName = mixamoVRMRigMap[mixamoName];
    const vrmNode = vrmBoneName ? vrm.humanoid.getNormalizedBoneNode(vrmBoneName) : null;
    const sourceNode = findMixamoNode(sourceRoot, mixamoName);
    if (!vrmNode || !sourceNode) continue;

    if (propertyName === 'quaternion') {
      sourceNode.getWorldQuaternion(_restRotationInverse).invert();
      sourceNode.parent.getWorldQuaternion(_parentRestWorldRotation);

      const values = new Float32Array(track.values.length);
      for (let i = 0; i < track.values.length; i += 4) {
        // parent rest world rotation * track rotation * inverse(rest world rotation)
        _quat.fromArray(track.values, i)
          .premultiply(_parentRestWorldRotation)
          .multiply(_restRotationInverse);
        _quat.toArray(values, i);
        if (isVRM0) { // VRM 0.x models face the other way
          values[i] = -values[i];
          values[i + 2] = -values[i + 2];
        }
      }
      tracks.push(new THREE.QuaternionKeyframeTrack(`${vrmNode.name}.quaternion`, track.times, values));
    } else if (propertyName === 'position' && vrmBoneName === 'hips') {
      // Only the hips move; other bones keep the VRM's own proportions
      const parentMatrix = sourceNode.parent.matrixWorld;
      const values = new Float32Array(track.values.length);
      for (let i = 0; i < track.values.length; i += 3) {
        _vec.fromArray(track.values, i).applyMatrix4(parentMatrix);
        _vec.y -= rootY;
        _vec.multiplyScalar(hipsScale);
        if (inPlace) { _vec.x = 0; _vec.z = 0; }
        if (isVRM0) { _vec.x = -_vec.x; _vec.z = -_vec.z; }
        _vec.toArray(values, i);
      }
      tracks.push(new THREE.VectorKeyframeTrack(`${vrmNode.name}.position`, track.times, values));
    }
  }

  if (tracks.length === 0) {
    throw new Error('None of the animation\'s bones matched the VRM skeleton');
  }
  return new THREE.AnimationClip(name, clip.duration, tracks);
}
