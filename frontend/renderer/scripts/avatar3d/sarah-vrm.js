// Sarah's 3D body: a VRM model rendered with three.js, driven by layers
// that run every frame in this order:
//   1. Animator  - motion-capture clips (idle / talking loops, gestures)
//   2. Body      - procedural offsets on top: gaze (spine/neck/head), arm
//                  pointing (IK), speech beats, leaning
//   3. Face      - expressions, blinking, audio-driven lip sync
//   4. vrm.update - applies expressions, eye look-at, spring-bone hair/cloth
// Procedural bone offsets are undone after each render so bones a clip
// doesn't animate never accumulate rotation.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";
import {
  VRMAnimationLoaderPlugin,
  VRMLookAtQuaternionProxy,
  createVRMAnimationClip,
} from "@pixiv/three-vrm-animation";

const { damp, clamp, degToRad } = THREE.MathUtils;
const tmpV1 = new THREE.Vector3();
const tmpV2 = new THREE.Vector3();
const tmpV3 = new THREE.Vector3();
const tmpQ1 = new THREE.Quaternion();
const tmpQ2 = new THREE.Quaternion();
const tmpQ3 = new THREE.Quaternion();
const tmpE = new THREE.Euler(0, 0, 0, "YXZ");
const IDENTITY = new THREE.Quaternion();

function makeLoader() {
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));
  loader.register((parser) => new VRMAnimationLoaderPlugin(parser));
  return loader;
}

// ---------------------------------------------------------------------------
// Animator: VRMA clip library with crossfades between a looping "base"
// (idle / talking) and one-shot gestures layered over it.
// ---------------------------------------------------------------------------
class Animator {
  constructor(vrm, loader, baseUrl) {
    this.vrm = vrm;
    this.loader = loader;
    this.baseUrl = baseUrl;
    this.mixer = new THREE.AnimationMixer(vrm.scene);
    this.hipsName = vrm.humanoid.getNormalizedBoneNode("hips")?.name || "hips";
    this.catalog = new Map();
    this.byTag = new Map();
    this.vrmaCache = new Map();
    this.clipCache = new Map();
    this.base = null;       // { id, action }
    this.pendingBase = null;
    this.oneShot = null;    // { id, action, resolve }
    this.mixer.addEventListener("finished", (ev) => this._onFinished(ev));
  }

  async init() {
    try {
      const res = await fetch(`${this.baseUrl}catalog.json`);
      const data = await res.json();
      for (const a of data.animations || []) {
        this.catalog.set(a.id, a);
        for (const tag of [a.category, a.subcategory, ...(a.tags || [])].filter(Boolean)) {
          if (!this.byTag.has(tag)) this.byTag.set(tag, []);
          this.byTag.get(tag).push(a.id);
        }
      }
    } catch (err) {
      console.warn("[SarahVRM] animation catalog unavailable:", err);
    }
  }

  has(id) {
    return this.catalog.has(id);
  }

  withTag(tag) {
    return this.byTag.get(tag) || [];
  }

  async _vrma(id) {
    if (!this.vrmaCache.has(id)) {
      const entry = this.catalog.get(id);
      const file = entry?.file || `${id}.vrma`;
      const promise = this.loader
        .loadAsync(`${this.baseUrl}${encodeURIComponent(file)}`)
        .then((gltf) => gltf.userData.vrmAnimations?.[0] || null);
      this.vrmaCache.set(id, promise);
      promise.catch(() => this.vrmaCache.delete(id));
    }
    return this.vrmaCache.get(id);
  }

  async clip(id) {
    if (this.clipCache.has(id)) return this.clipCache.get(id);
    const vrma = await this._vrma(id);
    if (!vrma) throw new Error(`animation ${id} has no VRM animation`);
    const clip = createVRMAnimationClip(vrma, this.vrm);
    clip.name = id;
    // Keep her on her spot: clips are authored at arbitrary floor positions,
    // so re-centre hips X/Z on the first frame (relative motion survives).
    for (const track of clip.tracks) {
      if (!track.name.endsWith(".position") || !track.name.includes(this.hipsName)) continue;
      const v = track.values;
      const x0 = v[0];
      const z0 = v[2];
      for (let i = 0; i < v.length; i += 3) { v[i] -= x0; v[i + 2] -= z0; }
    }
    this.clipCache.set(id, clip);
    return clip;
  }

  preload(ids) {
    // Low-priority warmup, two at a time, so the first gesture isn't a stall.
    const queue = [...new Set(ids)].filter((id) => this.has(id));
    const worker = async () => {
      while (queue.length) {
        const id = queue.shift();
        try { await this.clip(id); } catch { /* reported when played */ }
      }
    };
    return Promise.all([worker(), worker()]);
  }

  _action(clip, loop) {
    const action = this.mixer.clipAction(clip);
    action.setLoop(loop ? THREE.LoopRepeat : THREE.LoopOnce, loop ? Infinity : 1);
    action.clampWhenFinished = !loop;
    return action;
  }

  async setBase(id, fade = 0.6) {
    if (!this.has(id) || this.base?.id === id) return;
    let clip;
    try { clip = await this.clip(id); } catch (err) { console.warn("[SarahVRM] base clip failed:", id, err); return; }
    const action = this._action(clip, true);
    if (this.oneShot) {
      this.pendingBase = { id, action };
      return;
    }
    this._fadeTo(action, this.base?.action, fade);
    this.base = { id, action };
  }

  _fadeTo(next, prev, fade) {
    next.enabled = true;
    next.reset();
    next.setEffectiveTimeScale(1);
    next.setEffectiveWeight(1);
    next.play();
    if (prev && prev !== next) {
      next.crossFadeFrom(prev, fade, false);
      setTimeout(() => {
        if (prev !== this.base?.action && prev !== this.oneShot?.action) prev.stop();
      }, (fade + 0.1) * 1000);
    } else {
      next.fadeIn(fade);
    }
  }

  async playOnce(id, { fade = 0.35, speed = 1 } = {}) {
    let clip;
    try { clip = await this.clip(id); } catch (err) { console.warn("[SarahVRM] gesture clip failed:", id, err); return false; }
    const action = this._action(clip, false);
    const prev = this.oneShot?.action || this.base?.action;
    this.oneShot?.resolve?.(false);
    action.timeScale = speed;
    this._fadeTo(action, prev, fade);
    return new Promise((resolve) => {
      this.oneShot = { id, action, resolve };
    });
  }

  _onFinished(ev) {
    if (!this.oneShot || ev.action !== this.oneShot.action) return;
    const done = this.oneShot;
    this.oneShot = null;
    const next = this.pendingBase || this.base;
    this.pendingBase = null;
    if (next) {
      this._fadeTo(next.action, done.action, 0.5);
      this.base = next;
    }
    done.resolve(true);
  }

  get busy() {
    return Boolean(this.oneShot);
  }

  update(dt) {
    this.mixer.update(dt);
  }
}

// ---------------------------------------------------------------------------
// Body: procedural layers on normalized humanoid bones (after the mixer).
// ---------------------------------------------------------------------------
class Body {
  constructor(vrm) {
    this.vrm = vrm;
    this.h = vrm.humanoid;
    this.gaze = { yaw: 0, pitch: 0 };
    this.gazeTarget = new THREE.Vector3(0, 1.4, 2);
    this.lean = 0;
    this.leanTarget = 0;
    this.tilt = 0;
    this.tiltTarget = 0;
    this.beat = 0;          // head nod (radians, positive = down)
    this.point = null;      // { side, target:Vector3, weight, until }
    this.touched = new Map(); // node -> quaternion before offsets
  }

  bone(name) {
    return this.h.getNormalizedBoneNode(name);
  }

  _save(node) {
    if (node && !this.touched.has(node)) this.touched.set(node, node.quaternion.clone());
  }

  restore() {
    for (const [node, q] of this.touched) node.quaternion.copy(q);
    this.touched.clear();
  }

  pointAt(target, { side = null, hold = 1.8 } = {}) {
    const chosen = side || (target.x < 0 ? "right" : "left"); // avatar faces +Z: its right is -X
    this.point = { side: chosen, target: target.clone(), weight: this.point?.weight || 0, until: performance.now() + hold * 1000 };
  }

  update(dt, now) {
    this._applyGaze(dt);
    this._applyLeanAndTilt(dt);
    this._applyPoint(dt, now);
  }

  _applyGaze(dt) {
    const head = this.bone("head");
    if (!head) return;
    head.getWorldPosition(tmpV1);
    const dir = tmpV2.copy(this.gazeTarget).sub(tmpV1);
    const yaw = clamp(Math.atan2(dir.x, Math.max(0.05, dir.z)), degToRad(-55), degToRad(55));
    const pitch = clamp(Math.atan2(dir.y, Math.hypot(dir.x, dir.z)), degToRad(-30), degToRad(22));
    this.gaze.yaw = damp(this.gaze.yaw, yaw, 5.5, dt);
    this.gaze.pitch = damp(this.gaze.pitch, pitch, 5.5, dt);
    // Spread the turn down the spine so it reads as a natural look, not a
    // swivelling head; the eyes (vrm.lookAt) close the remaining gap.
    const chain = [["upperChest", 0.12, 0.1], ["neck", 0.33, 0.35], ["head", 0.45, 0.55]];
    for (const [name, yawShare, pitchShare] of chain) {
      const node = this.bone(name);
      if (!node) continue;
      this._save(node);
      const nod = name === "head" ? this.beat : 0;
      tmpE.set(-(this.gaze.pitch * pitchShare) + nod, this.gaze.yaw * yawShare, 0, "YXZ");
      node.quaternion.premultiply(tmpQ1.setFromEuler(tmpE));
    }
  }

  _applyLeanAndTilt(dt) {
    this.lean = damp(this.lean, this.leanTarget, 3, dt);
    this.tilt = damp(this.tilt, this.tiltTarget, 4, dt);
    const spine = this.bone("spine");
    if (spine && Math.abs(this.lean) > 1e-3) {
      this._save(spine);
      spine.quaternion.premultiply(tmpQ1.setFromEuler(tmpE.set(this.lean, 0, 0, "YXZ")));
    }
    const head = this.bone("head");
    if (head && Math.abs(this.tilt) > 1e-3) {
      this._save(head);
      head.quaternion.premultiply(tmpQ1.setFromEuler(tmpE.set(0, 0, this.tilt, "YXZ")));
    }
  }

  _applyPoint(dt, now) {
    const p = this.point;
    if (!p) return;
    const active = now < p.until;
    p.weight = damp(p.weight, active ? 1 : 0, active ? 7 : 5, dt);
    if (!active && p.weight < 0.01) {
      this.point = null;
      return;
    }
    const s = p.side;
    const upper = this.bone(`${s}UpperArm`);
    const lower = this.bone(`${s}LowerArm`);
    const hand = this.bone(`${s}Hand`);
    if (!upper || !lower || !hand) return;
    const w = p.weight;
    [upper, lower, hand].forEach((n) => this._save(n));

    // 1) Nearly straighten elbow and wrist.
    lower.quaternion.slerp(IDENTITY, w * 0.95);
    hand.quaternion.slerp(IDENTITY, w * 0.7);
    upper.updateWorldMatrix(true, true);

    // 2) Aim shoulder->hand at the target (kept in front of the body).
    const shoulder = upper.getWorldPosition(tmpV1);
    const handPos = hand.getWorldPosition(tmpV2);
    const current = handPos.sub(shoulder).normalize();
    const desired = tmpV3.copy(p.target).sub(shoulder);
    desired.z = Math.max(desired.z, 0.25 * desired.length());
    desired.normalize();
    const aim = tmpQ1.setFromUnitVectors(current, desired);
    const upperWorld = upper.getWorldQuaternion(tmpQ2);
    const parentWorld = upper.parent.getWorldQuaternion(tmpQ3);
    const local = parentWorld.invert().multiply(aim.multiply(upperWorld));
    upper.quaternion.slerp(local, w);

    // 3) Index finger out, the rest curled.
    const curlSign = s === "right" ? 1 : -1;
    for (const finger of ["Middle", "Ring", "Little"]) {
      for (const [seg, angle] of [["Proximal", 70], ["Intermediate", 85], ["Distal", 50]]) {
        const node = this.bone(`${s}${finger}${seg}`);
        if (!node) continue;
        this._save(node);
        tmpQ2.setFromEuler(tmpE.set(0, 0, degToRad(angle) * curlSign, "YXZ"));
        node.quaternion.slerp(tmpQ2, w);
      }
    }
    for (const seg of ["Proximal", "Intermediate", "Distal"]) {
      const node = this.bone(`${s}Index${seg}`);
      if (!node) continue;
      this._save(node);
      node.quaternion.slerp(IDENTITY, w);
    }
    const thumb = this.bone(`${s}ThumbDistal`);
    if (thumb) {
      this._save(thumb);
      thumb.quaternion.slerp(tmpQ2.setFromEuler(tmpE.set(0, degToRad(-25) * curlSign, 0, "YXZ")), w);
    }
  }
}

// ---------------------------------------------------------------------------
// Face: expressions with smoothing, natural blinking, audio lip sync.
// ---------------------------------------------------------------------------
export const FACE_RECIPES = {
  neutral: {},
  happy: { happy: 0.75 },
  smile: { happy: 0.4 },
  laugh: { happy: 1.0, aa: 0.35 },
  excited: { happy: 0.9, surprised: 0.25 },
  playful: { happy: 0.6, raw: { Fcl_HA_Fung1: 0.7 } },
  sad: { sad: 0.8 },
  cry: { sad: 1.0, raw: { Fcl_EYE_Close: 0.25 } },
  angry: { angry: 0.8 },
  annoyed: { angry: 0.4 },
  surprised: { surprised: 0.9 },
  shocked: { surprised: 1.0, raw: { Fcl_EYE_Spread: 0.5 } },
  shy: { happy: 0.35, relaxed: 0.3 },
  smug: { relaxed: 0.55, happy: 0.2 },
  relaxed: { relaxed: 0.8 },
  thinking: { raw: { Fcl_BRW_Sorrow: 0.35, Fcl_MTH_Close: 0.3 } },
  worried: { sad: 0.45, raw: { Fcl_BRW_Surprised: 0.3 } },
  sleepy: { relaxed: 0.5, raw: { Fcl_EYE_Close: 0.45 } },
  pout: { angry: 0.25, raw: { Fcl_MTH_Up: 0.5 } },
};

export const FACE_ALIASES = {
  joy: "happy", joyful: "happy", glad: "happy", grin: "happy", smiling: "smile",
  laughing: "laugh", giggle: "laugh", affectionate: "shy", loving: "shy", love: "shy",
  embarrassed: "shy", blush: "shy", flustered: "shy", confused: "thinking", curious: "thinking",
  pensive: "thinking", frustrated: "annoyed", irritated: "annoyed", mad: "angry",
  scared: "worried", nervous: "worried", anxious: "worried", concerned: "worried",
  tired: "sleepy", bored: "sleepy", calm: "relaxed", content: "relaxed", teasing: "playful",
  mischievous: "playful", proud: "smug", shock: "shocked", amazed: "surprised", sorrow: "sad",
  upset: "sad", crying: "cry", tearful: "cry", neutral: "neutral",
  hurt: "sad", lonely: "sad", guilty: "worried", amused: "playful", grateful: "smile",
  relieved: "relaxed", warm: "smile", delighted: "excited", thrilled: "excited", unsure: "thinking",
};

const MOUTH = ["aa", "ih", "ou", "ee", "oh"];
const PRESETS = ["happy", "angry", "sad", "relaxed", "surprised", ...MOUTH, "blink", "blinkLeft", "blinkRight"];

class Face {
  constructor(vrm) {
    this.vrm = vrm;
    this.em = vrm.expressionManager;
    this.weights = Object.fromEntries(PRESETS.map((n) => [n, 0]));
    this.raw = {};           // current raw morph weights
    this.baseline = { recipe: "neutral", amount: 0.4 };
    this.override = null;    // { recipe, amount, until }
    this.nextBlink = performance.now() + 1500;
    this.blinkT = -1;
    this.wink = 0;           // seconds remaining
    this.analyser = null;
    this.freq = null;
    this.speakingFallback = 0;
    this.mouth = Object.fromEntries(MOUTH.map((n) => [n, 0]));
    this.energy = 0;
    this.morphMeshes = [];
    vrm.scene.traverse((o) => {
      if (o.isMesh && o.morphTargetDictionary) this.morphMeshes.push(o);
    });
  }

  resolve(name) {
    const key = String(name || "neutral").toLowerCase();
    return FACE_RECIPES[key] ? key : FACE_ALIASES[key] || null;
  }

  express(name, amount = 1, hold = 4) {
    if (String(name).toLowerCase() === "wink") {
      this.wink = 0.65;
      this.override = { recipe: "smile", amount: 1, until: performance.now() + 900 };
      return true;
    }
    const recipe = this.resolve(name);
    if (!recipe) return false;
    this.override = { recipe, amount: amount ?? 1, until: performance.now() + hold * 1000 };
    return true;
  }

  setBaseline(name, amount = 0.4) {
    const recipe = this.resolve(name) || "neutral";
    this.baseline = { recipe, amount };
  }

  blink() {
    if (this.blinkT < 0) this.blinkT = 0;
  }

  attachAnalyser(analyser) {
    this.analyser = analyser;
    this.freq = analyser ? new Uint8Array(analyser.frequencyBinCount) : null;
  }

  update(dt, now) {
    const active = this.override && now < this.override.until ? this.override : null;
    if (this.override && !active) this.override = null;
    const { recipe, amount } = active || this.baseline;
    const target = FACE_RECIPES[recipe] || {};
    const speaking = this._updateMouth(dt, now);

    for (const name of ["happy", "angry", "sad", "relaxed", "surprised"]) {
      let goal = (target[name] || 0) * amount;
      if (speaking) goal *= 0.7; // leave room for visible lip shapes
      this.weights[name] = damp(this.weights[name], goal, 6, dt);
    }
    const rawGoal = target.raw || {};
    for (const key of new Set([...Object.keys(this.raw), ...Object.keys(rawGoal)])) {
      this.raw[key] = damp(this.raw[key] || 0, (rawGoal[key] || 0) * amount, 6, dt);
      if (this.raw[key] < 1e-3 && !rawGoal[key]) delete this.raw[key];
    }
    for (const m of MOUTH) {
      this.weights[m] = Math.max(this.mouth[m], speaking ? 0 : (target[m] || 0) * amount);
    }
    this._updateBlink(dt, now);

    if (this.em) {
      for (const [name, value] of Object.entries(this.weights)) {
        if (this.em.getExpression(name)) this.em.setValue(name, clamp(value, 0, 1));
      }
    }
  }

  _updateMouth(dt, now) {
    const targets = Object.fromEntries(MOUTH.map((n) => [n, 0]));
    let speaking = false;
    if (this.analyser && this.freq) {
      this.analyser.getByteFrequencyData(this.freq);
      const f = this.freq;
      const len = f.length;
      const band = (a, b) => {
        let s = 0;
        const i0 = Math.floor(len * a);
        const i1 = Math.max(i0 + 1, Math.floor(len * b));
        for (let i = i0; i < i1; i++) s += f[i];
        return s / (i1 - i0) / 255;
      };
      const low = band(0, 0.08);
      const midLow = band(0.08, 0.18);
      const mid = band(0.18, 0.35);
      const high = band(0.35, 0.6);
      const volume = clamp((low + midLow + mid) / 1.6, 0, 1);
      this.energy = damp(this.energy, volume, 18, dt);
      if (volume > 0.04) {
        speaking = true;
        targets.aa = clamp(volume * 1.8 * (0.4 + low), 0, 1);
        targets.oh = clamp(midLow * volume * 1.4, 0, 0.8);
        targets.ih = clamp(mid * volume * 1.3, 0, 0.7);
        targets.ee = clamp(high * volume * 1.6, 0, 0.6);
        targets.ou = clamp(midLow * volume * 0.8, 0, 0.5);
      }
    } else if (this.speakingFallback > now) {
      speaking = true;
      const t = now / 1000;
      targets.aa = Math.max(0, Math.sin(t * 13) * 0.45 + Math.sin(t * 7.3) * 0.25);
      targets.oh = Math.max(0, Math.cos(t * 5.1) * 0.25);
      this.energy = damp(this.energy, targets.aa, 12, dt);
    } else {
      this.energy = damp(this.energy, 0, 8, dt);
    }
    for (const m of MOUTH) {
      const rate = targets[m] > this.mouth[m] ? 30 : 16; // fast attack, softer release
      this.mouth[m] = damp(this.mouth[m], targets[m], rate, dt);
    }
    return speaking || Object.values(this.mouth).some((v) => v > 0.05);
  }

  _updateBlink(dt, now) {
    let blink = 0;
    if (this.blinkT >= 0) {
      this.blinkT += dt;
      const closing = 0.07;
      const opening = 0.11;
      blink = this.blinkT < closing ? this.blinkT / closing : Math.max(0, 1 - (this.blinkT - closing) / opening);
      if (this.blinkT > closing + opening) {
        this.blinkT = -1;
        // Natural rhythm: 2.5-6 s, sometimes a quick double blink.
        this.nextBlink = now + (Math.random() < 0.12 ? 180 : 2500 + Math.random() * 3500);
      }
    } else if (now >= this.nextBlink) {
      this.blinkT = 0;
    }
    this.weights.blink = blink;
    if (this.wink > 0) {
      this.wink -= dt;
      this.weights.blinkRight = damp(this.weights.blinkRight, 1, 25, dt);
    } else {
      this.weights.blinkRight = damp(this.weights.blinkRight, 0, 12, dt);
    }
  }

  // Raw morphs (e.g. Fcl_EYE_Spread) layered on top of the VRM expressions.
  // Written after vrm.update. Morphs an expression binds are reset by the
  // expression manager every frame; unbound ones keep whatever we last
  // wrote, so an influence still equal to our last write is ours to replace.
  applyRaw() {
    this.rawWritten ||= new Map(); // "meshIdx:morphIdx" -> value we wrote
    const next = new Map();
    this.morphMeshes.forEach((mesh, mi) => {
      const dict = mesh.morphTargetDictionary;
      const inf = mesh.morphTargetInfluences;
      const names = new Set(Object.keys(this.raw));
      for (const key of this.rawWritten.keys()) {
        const [m, name] = key.split("|");
        if (Number(m) === mi) names.add(name);
      }
      for (const name of names) {
        const idx = dict[name];
        if (idx === undefined) continue;
        const key = `${mi}|${name}`;
        const prev = this.rawWritten.get(key);
        const base = prev !== undefined && inf[idx] === prev ? 0 : inf[idx];
        const value = Math.max(base, this.raw[name] || 0);
        inf[idx] = value;
        if (this.raw[name]) next.set(key, value);
      }
    });
    this.rawWritten = next;
  }
}

// ---------------------------------------------------------------------------
// Stage: renderer, camera framing, render loop.
// ---------------------------------------------------------------------------
// height: metres of her (from the top of the head down) that must be in
// view; width: metres that must fit across; headroom above the head.
const FRAMES = {
  full: { height: null, width: 0.9, headroom: 0.1 },
  upper: { height: 0.95, width: 0.62, headroom: 0.07 },
  face: { height: 0.36, width: 0.34, headroom: 0.05 },
};

export class SarahVRM {
  constructor({ container, canvas, modelUrl, animationsUrl }) {
    this.container = container;
    this.canvas = canvas;
    this.modelUrl = modelUrl;
    this.animationsUrl = animationsUrl;
    this.loader = makeLoader();
    this.clock = new THREE.Clock();
    this.frame = "upper";
    this.cameraGoal = { pos: new THREE.Vector3(0, 1.3, 3), look: new THREE.Vector3(0, 1.2, 0) };
    this.cameraLook = new THREE.Vector3(0, 1.2, 0);
    this.onFrame = null; // director hook: (dt, nowMs) => void
    this.running = false;
  }

  async init() {
    const renderer = new THREE.WebGLRenderer({ canvas: this.canvas, alpha: true, antialias: true, powerPreference: "high-performance" });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer = renderer;

    const scene = new THREE.Scene();
    const key = new THREE.DirectionalLight(0xffffff, 1.4);
    key.position.set(0.6, 1.6, 2.2);
    const rim = new THREE.DirectionalLight(0xc9b3ff, 0.9);
    rim.position.set(-1.2, 1.8, -1.5);
    scene.add(key, rim, new THREE.AmbientLight(0xffffff, 0.85));
    this.scene = scene;
    this.camera = new THREE.PerspectiveCamera(26, 1, 0.05, 50);

    const gltf = await this.loader.loadAsync(this.modelUrl);
    const vrm = gltf.userData.vrm;
    if (!vrm) throw new Error("file is not a VRM model");
    VRMUtils.removeUnnecessaryVertices(gltf.scene);
    VRMUtils.combineSkeletons?.(gltf.scene);
    if (vrm.meta?.metaVersion === "0") VRMUtils.rotateVRM0(vrm);
    vrm.scene.traverse((o) => { o.frustumCulled = false; });
    if (vrm.lookAt) {
      vrm.scene.add(Object.assign(new VRMLookAtQuaternionProxy(vrm.lookAt), { name: "VRMLookAtQuaternionProxy" }));
      this.eyeTarget = new THREE.Object3D();
      scene.add(this.eyeTarget);
      vrm.lookAt.target = this.eyeTarget;
      vrm.lookAt.autoUpdate = true;
    }
    vrm.scene.visible = false; // until the idle pose is applied (no T-pose flash)
    scene.add(vrm.scene);
    this.vrm = vrm;

    this.animator = new Animator(vrm, this.loader, this.animationsUrl);
    this.body = new Body(vrm);
    this.face = new Face(vrm);
    await this.animator.init();

    this._measure();
    this.resize();
    this.setFrame(this.frame, true);
    this._resizeObserver = new ResizeObserver(() => this.resize());
    this._resizeObserver.observe(this.container);
    return this;
  }

  _measure() {
    const pos = (name) => {
      const node = this.vrm.humanoid.getNormalizedBoneNode(name);
      return node ? node.getWorldPosition(new THREE.Vector3()) : null;
    };
    this.vrm.scene.updateMatrixWorld(true);
    const head = pos("head") || new THREE.Vector3(0, 1.45, 0);
    const hips = pos("hips") || new THREE.Vector3(0, 0.9, 0);
    const foot = pos("leftFoot") || new THREE.Vector3(0, 0.08, 0);
    this.points = {
      head,
      hips,
      chest: pos("upperChest") || pos("chest") || head.clone().lerp(hips, 0.45),
      top: head.y + 0.16,
      bottom: Math.min(foot.y, 0) - 0.02,
    };
  }

  headPosition(out = new THREE.Vector3()) {
    const node = this.vrm.humanoid.getNormalizedBoneNode("head");
    return node ? node.getWorldPosition(out) : out.copy(this.points.head);
  }

  setFrame(name, instant = false) {
    const spec = FRAMES[name] || FRAMES.upper;
    this.frame = name in FRAMES ? name : "upper";
    const p = this.points;
    const height = (spec.height ?? p.top - p.bottom) + spec.headroom;
    const tanHalf = Math.tan(degToRad(this.camera.fov / 2));
    const aspect = Math.max(0.2, this.camera.aspect);
    const dist = Math.max(height / 2 / tanHalf, spec.width / 2 / (tanHalf * aspect));
    // Anchor the head near the top of the frame; in a tall panel the extra
    // room goes below her instead of above.
    const visible = 2 * dist * tanHalf;
    const centerY = p.top + spec.headroom - visible / 2;
    this.cameraGoal.pos.set(0, centerY + 0.02, dist);
    this.cameraGoal.look.set(0, centerY, 0);
    if (instant) {
      this.camera.position.copy(this.cameraGoal.pos);
      this.cameraLook.copy(this.cameraGoal.look);
      this.camera.lookAt(this.cameraLook);
    }
  }

  resize() {
    const w = Math.max(1, this.container.clientWidth);
    const h = Math.max(1, this.container.clientHeight);
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    if (this.points) this.setFrame(this.frame);
  }

  // Screen (client px) -> world point on a plane in front of the avatar.
  // Works for points outside the canvas too (e.g. the chat panel), so she
  // can look and point across the whole window.
  screenToWorld(clientX, clientY, planeZ = 0.35) {
    const rect = this.canvas.getBoundingClientRect();
    const ndc = new THREE.Vector3(
      ((clientX - rect.left) / Math.max(1, rect.width)) * 2 - 1,
      -(((clientY - rect.top) / Math.max(1, rect.height)) * 2 - 1),
      0.5,
    );
    ndc.unproject(this.camera);
    const dir = ndc.sub(this.camera.position).normalize();
    const t = (planeZ - this.camera.position.z) / (dir.z || -1e-6);
    return this.camera.position.clone().add(dir.multiplyScalar(t));
  }

  start() {
    if (this.running) return;
    this.running = true;
    this.clock.getDelta();
    const tick = () => {
      if (!this.running) return;
      this._raf = requestAnimationFrame(tick);
      if (document.hidden) return;
      const dt = Math.min(this.clock.getDelta(), 1 / 20);
      const now = performance.now();
      this.onFrame?.(dt, now);
      this.animator.update(dt);
      this.body.update(dt, now);
      this.face.update(dt, now);
      if (this.eyeTarget) this.eyeTarget.position.copy(this.body.gazeTarget);
      this.vrm.update(dt);
      this.face.applyRaw();
      this.camera.position.lerp(this.cameraGoal.pos, 1 - Math.exp(-4 * dt));
      this.cameraLook.lerp(this.cameraGoal.look, 1 - Math.exp(-4 * dt));
      this.camera.lookAt(this.cameraLook);
      this.renderer.render(this.scene, this.camera);
      this.body.restore();
      if (!this.vrm.scene.visible && this.animator.base) this.vrm.scene.visible = true;
    };
    this._raf = requestAnimationFrame(tick);
  }

  stop() {
    this.running = false;
    cancelAnimationFrame(this._raf);
  }
}
