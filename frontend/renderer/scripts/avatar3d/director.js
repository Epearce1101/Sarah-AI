// Sarah's body: the part of her that isn't words. Mind and body are one
// self, so this runs both directions:
//   mind -> body  Her feeling (<feel>) becomes her mood, face and posture;
//                 her movements (<face>/<look>/<point>/<gesture>) happen as
//                 their words are spoken; every sentence's tone shows on her
//                 face even untagged (affect.js), plus natural nods/waves.
//   body -> mind  What she senses (a touch, the user coming back) is sent to
//                 her mind via `onSensation` (presence.js), and `describe()`
//                 tells her mind what her body is doing, so she knows it.
//   alive         Between turns she breathes, looks around, listens while
//                 being typed to, thinks while waiting, idles by mood.
import * as THREE from "three";
import { affectCues } from "./affect.js";

// Friendly gesture names -> animation ids (catalog) or procedural actions.
export const GESTURES = {
  wave: "161_Waving", hello: "79_Standing Greeting", greet: "79_Standing Greeting", hi: "dm_4",
  nod: "118_Head Nod Yes", yes: "118_Head Nod Yes", agree: "dm_12",
  shake_head: "144_Shaking Head No", shake: "144_Shaking Head No", head_shake: "144_Shaking Head No",
  shaking_head: "144_Shaking Head No", shake_no: "144_Shaking Head No", no: "144_Shaking Head No", refuse: "dm_27", tease: "56_No", annoyed: "95_Annoyed Head Shake",
  sit: "75_Sitting",
  shrug: "145_Shrugging", whatever: "93_Whatever Gesture",
  think: "88_Thinking", thinking: "88_Thinking", thinking_pose: "88_Thinking", idea: "dm_108", point_up: "dm_108",
  clap: "19_Clapping", cheer: "dm_28", encourage: "dm_2", yay: "dm_45", excited: "dm_53",
  thanks: "156_Thankful", thankful: "156_Thankful", hand_to_chest: "156_Thankful", grateful: "156_Thankful",
  bow: "138_Quick Informal Bow", formal_bow: "137_Quick Formal Bow", cute_bow: "138_Quick Informal Bow",
  happy_hands: "116_Happy Hand Gesture", present: "dm_0", explain: "dm_90", show: "dm_0",
  peace: "dm_26", victory: "dm_30", heart: "dm_29", blow_kiss: "dm_20", kiss: "dm_41",
  shy: "dm_51", blush: "dm_51", sorry: "dm_40", apologize: "dm_40",
  shush: "dm_42", salute: "dm_10", tsundere: "dm_8", pout: "dm_8", tantrum: "dm_9",
  angry: "94_Angry Gesture", frustrated: "0_Angry",
  sigh: "65_Relieved Sigh", relieved: "65_Relieved Sigh", stretch: "131_Neck Stretching", yawn: "dm_22",
  jump: "49_Joyful Jump", joy: "49_Joyful Jump", arms_up: "dm_19", cute_jump: "dm_32", laugh: "dm_2",
  cat: "dm_43", nya: "dm_48", dog: "dm_47", tiger: "dm_57",
  cry: "22_Crying", sob: "23_Crying_2", defeat: "26_Defeat", facepalm: "26_Defeat",
  raise_hand: "dm_108", question: "dm_108", sing: "71_Singing",
  phone: "155_Talking On Phone", distant: "142_Sad Idle",
};
// Held poses: she leans toward you (spine bends, eyes stay on you), tilts
// her head and rocks a little, with a face and optionally a hand clip.
// tilt: null = pick a side at random.
// turn: she turns this far (either side, at random) so the lean shows.
// approach: metres her whole body comes toward you (the camera).
export const POSES = {
  lean_toward: { lean: 0.42, approach: 0.14, tilt: null, tiltSize: 0.12, sway: 0.02, face: "happy", hold: 3.6, words: "leaning in close toward them" },
  cute_pose: { lean: 0.44, turn: 0.35, approach: 0.08, tilt: null, sway: 0.05, face: "happy", clips: ["dm_24", "dm_56"], hold: 4, words: "leaning toward them, striking a cute pose" },
  lean_forward: { lean: 0.4, turn: 0.12, approach: 0.12, tilt: null, face: "happy", hold: 3.4, words: "leaning forward toward them" },
  peek: { lean: 0.26, roll: 0.22, approach: 0.05, tilt: 0.3, sway: 0.03, face: "playful", hold: 3.4, words: "leaning over to peek at them" },
  curious_lean: { lean: 0.34, turn: 0.1, approach: 0.1, tilt: null, headPitch: 0.04, face: "thinking", hold: 3.4, words: "leaning in, curious" },
  heart_lean: { lean: 0.4, turn: 0.2, approach: 0.1, tilt: null, sway: 0.03, face: "shy", clips: ["dm_29"], hold: 4, words: "leaning in, making a heart" },
  peace_lean: { lean: 0.38, turn: 0.25, approach: 0.08, tilt: null, bob: 0.02, face: "wink", clips: ["dm_26"], hold: 4, words: "leaning in with a peace sign and a wink" },
};
const POSE_ALIASES = { cute: "cute_pose", pose: "cute_pose", cute_lean: "cute_pose", lean: "lean_forward",
  peek_a_boo: "peek", lean_in: "lean_toward", lean_close: "lean_toward", come_closer: "lean_toward", lean_to_user: "lean_toward" };
const IDLE_GAP = [60000, 150000];   // ms between idle actions
const POSE_SHARE = 0.25;            // of those, how often a pose (rather than something small)
export const PROCEDURAL = ["lean_in", "step_back", "tilt", "nod_small", "look_around", "surprise", "dance", "spin",
  ...Object.keys(POSES)];
const DANCES = ["47_Jazz Dancing", "70_Silly Dancing", "83_Swing Dancing", "45_House Dancing", "54_Macarena Dance", "dm_38", "41_Hip Hop Dancing", "67_Rumba Dancing"];
// Clips that use the whole body: frame the full figure while they play.
const FULL_BODY = /jump|danc|bow|defeat|crying|kneel|sitting|cheer|tantrum|throw|macarena|dm_(19|32|38|45|53|58|9)$/i;

const IDLE_SETS = {
  calm: ["119_Idle", "dm_120", "dm_121", "dm_122"],
  happy: ["dm_24", "dm_46", "dm_59", "dm_101"],
  cool: ["dm_23", "dm_33"],
  sad: ["142_Sad Idle", "dm_17"],
  sleepy: ["dm_110", "dm_111", "dm_22"],
};
const TALKING = ["dm_5", "dm_6", "dm_7", "dm_13", "dm_14", "dm_15", "86_Talking"];
const IDLE_ACTIONS = {
  calm: ["131_Neck Stretching", "look_around", "tilt", "dm_101", "lean_toward", "curious_lean"],
  happy: ["dm_26", "look_around", "dm_24", "116_Happy Hand Gesture", "cute_pose", "peek", "lean_toward"],
  sad: ["65_Relieved Sigh", "look_around"],
  sleepy: ["dm_22", "131_Neck Stretching"],
};
const MOOD_FACE = {
  happy: "happy", excited: "excited", affectionate: "shy", shy: "shy", confused: "thinking",
  surprised: "surprised", angry: "annoyed", frustrated: "annoyed", sad: "sad", neutral: "neutral",
  tired: "sleepy", loving: "shy", embarrassed: "shy", smug: "smug",
};

// Words for what her body is doing, for describe().
const GESTURE_WORDS = {};
for (const [name, id] of Object.entries(GESTURES)) GESTURE_WORDS[id] ||= name.replace(/_/g, " ");
Object.assign(GESTURE_WORDS, {
  "161_Waving": "waving", "118_Head Nod Yes": "nodding", "144_Shaking Head No": "shaking your head",
  "145_Shrugging": "shrugging", "88_Thinking": "striking a thinking pose", "19_Clapping": "clapping",
  "156_Thankful": "hand on your chest", "131_Neck Stretching": "stretching your neck", "dm_22": "yawning",
  "65_Relieved Sigh": "sighing", "dm_51": "being shy", "dm_26": "making a peace sign", "dm_29": "making a heart",
  "dm_101": "swaying a little", "22_Crying": "crying",
});
for (const id of ["47_Jazz Dancing", "70_Silly Dancing", "83_Swing Dancing", "45_House Dancing", "54_Macarena Dance", "dm_38", "41_Hip Hop Dancing", "67_Rumba Dancing"]) GESTURE_WORDS[id] = "dancing";
const FACE_WORDS = {
  happy: "happy", smile: "softly smiling", laugh: "laughing", excited: "lit up", playful: "playful",
  sad: "sad", cry: "teary", angry: "angry", annoyed: "annoyed", surprised: "surprised",
  shocked: "shocked", shy: "blushing", smug: "smug", relaxed: "relaxed", thinking: "thoughtful",
  worried: "worried", sleepy: "sleepy", pout: "pouting", neutral: "calm",
};

const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
const rand = (a, b) => a + Math.random() * (b - a);

export class SarahDirector {
  constructor(avatar) {
    this.avatar = avatar;
    this.mode = "idle";               // idle | listening | thinking | speaking
    this.mood = { emotion: "neutral", intensity: 0.3 };
    this.attention = null;            // { target: () => Vector3, until, source }
    this.cursor = { x: 0, y: 0, at: 0 };
    this.lastActivity = performance.now();
    this.lastIdleAction = performance.now();
    this.nextGlance = 0;
    this.nextNod = 0;
    this.timers = new Set();
    this.speech = null;               // { cues:[], startedAt, duration }
    this.streamFired = 0;             // cues already performed from a streaming reply
    this.lastReplyAt = 0;
    this.wakeFrame = null;
    this.feeling = null;              // { label, amount, reason, at } her own
    this.lastTyping = 0;
    this.onSensation = null;          // (kind, detail) => void, set by presence.js
    this._bindWindow();
    avatar.onFrame = (dt, now) => this.update(dt, now);
    this._setBaseForMode();
    avatar.animator.preload([...IDLE_SETS.calm, ...TALKING.slice(0, 3), "161_Waving", "118_Head Nod Yes", "88_Thinking", "145_Shrugging", "156_Thankful"]);
  }

  // ----- targets -----------------------------------------------------------
  _element(selector) {
    const el = document.querySelector(selector);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    if (!r.width && !r.height) return null;
    return this.avatar.screenToWorld(r.left + r.width / 2, r.top + r.height / 2);
  }

  target(name) {
    const head = this.avatar.headPosition(new THREE.Vector3());
    const key = String(name || "user").toLowerCase();
    const offsets = {
      left: [-0.9, -0.05, 0.6], right: [0.9, -0.05, 0.6], up: [0.2, 0.42, 0.9], down: [0, -0.8, 0.7],
      away: [-0.7, 0.2, 0.5], sky: [0, 0.9, 0.5], floor: [0.1, -1.4, 0.6],
    };
    if (offsets[key]) return head.add(new THREE.Vector3(...offsets[key]));
    if (key === "user" || key === "you" || key === "viewer" || key === "camera") return this.avatar.camera.position.clone();
    if (key === "cursor" || key === "mouse") return this.avatar.screenToWorld(this.cursor.x, this.cursor.y);
    if (key === "chat" || key === "messages" || key === "conversation") return this._element("#chat-log") || head.add(new THREE.Vector3(-1.2, 0, 0.6));
    if (key === "input" || key === "keyboard" || key === "typing") return this._element("#chat-input") || head.add(new THREE.Vector3(-1, -0.8, 0.7));
    if (key === "sidebar" || key === "menu") return this._element(".sidebar") || head.add(new THREE.Vector3(-2, 0, 0.6));
    if (key === "screen" || key === "top") return this._element(".top-bar") || head.add(new THREE.Vector3(-0.5, 0.6, 0.6));
    if (key === "self" || key === "me") return this.avatar.points.chest.clone().add(new THREE.Vector3(0, 0, 0.1));
    return null;
  }

  lookAt(name, hold = 2.5, source = "cue") {
    const key = name;
    const fn = () => this.target(key) || this.avatar.camera.position.clone();
    const first = fn();
    if (!first) return false;
    const big = first.distanceTo(this.avatar.body.gazeTarget) > 0.8;
    this.attention = { target: fn, until: performance.now() + hold * 1000, source, name: String(key || "user").toLowerCase() };
    if (big) this.avatar.face.blink(); // people blink on large gaze shifts
    return true;
  }

  // ----- stage directions ----------------------------------------------------
  perform(cue) {
    if (!cue) return false;
    const { type, value, amount } = cue;
    if (type === "feel") return this.feel(value, amount, cue.reason);
    if (type === "face") return this.avatar.face.express(value, amount ?? (cue.auto ? 0.6 : 1), cue.auto ? 3.5 : 4.5);
    if (type === "look") return this.lookAt(value, 2.8);
    if (type === "point") {
      const t = this.target(value);
      if (!t) return false;
      this.avatar.body.pointAt(t, { hold: 1.9 });
      if (!/^(self|me|user|you)$/.test(value)) this.lookAt(value, 1.6);
      return true;
    }
    if (type === "gesture") return this.gesture(value, amount);
    return false;
  }

  // Her feeling: the state she's in, not a one-off expression. It shows at
  // once (before a word is spoken), then settles into her resting face, her
  // idle stance and how she carries herself until something changes it.
  feel(label, amount = 0.5, reason = "") {
    const name = String(label || "").toLowerCase();
    if (!name) return false;
    const strength = Math.max(0, Math.min(1, Number(amount ?? 0.5)));
    const same = this.feeling?.label === name && Math.abs(this.feeling.amount - strength) < 0.1
      && performance.now() - this.feeling.at < 4000;
    this.feeling = { label: name, amount: strength, reason: reason || "", at: performance.now() };
    if (same) return true; // streamed twice (preview + speech): already feeling it
    this.setMood(name, strength);
    this.avatar.face.express(name, 0.5 + strength * 0.5, 2.5);
    const body = this.avatar.body;
    // Posture follows the feeling: up and open when bright, drawn in when low.
    if (/excited|happy|proud|playful|surprised|affection|loving|curious/.test(name)) {
      body.leanTarget = 0.08 * strength;          // drawn toward you
      body.approachTarget = 0.05 * strength;
    } else if (/sad|hurt|lonely|worried|guilty|tired|sleepy/.test(name)) body.leanTarget = -0.04 * strength;
    else if (/shy|embarrass/.test(name)) body.tiltTarget = 0.12 * strength;
    this._later(2500, () => {
      body.leanTarget = this.mode === "listening" ? 0.12 : 0;
      body.approachTarget = this.mode === "listening" ? 0.06 : 0;
      body.tiltTarget = 0;
    });
    return true;
  }

  resolveGesture(name) {
    const anim = this.avatar.animator;
    if (anim.has(name)) return name; // exact catalog id, e.g. "131_Neck Stretching"
    const key = String(name || "").toLowerCase().replace(/[\s-]+/g, "_");
    if (GESTURES[key]) return GESTURES[key];
    const tagged = anim.withTag(key);
    return tagged.length ? pick(tagged) : null;
  }

  gesture(name, amount = 1) {
    const key = String(name || "").toLowerCase().replace(/[\s-]+/g, "_");
    const body = this.avatar.body;
    const poseName = POSES[key] ? key : POSE_ALIASES[key];
    if (poseName) return this.strikePose(poseName, amount);
    if (key === "step_back") { body.leanTarget = -0.1; this._later(1400, () => (body.leanTarget = 0)); return true; }
    if (key === "tilt" || key === "head_tilt") { body.tiltTarget = (Math.random() < 0.5 ? -1 : 1) * 0.2; this._later(1500, () => (body.tiltTarget = 0)); return true; }
    if (key === "nod_small") { this._nod(0.12); return true; }
    if (key === "look_around") { this._lookAround(); return true; }
    if (key === "surprise") { this.avatar.face.express("surprised", 1, 1.6); body.leanTarget = -0.08; this._later(900, () => (body.leanTarget = 0)); return true; }
    if (key === "point") return this.perform({ type: "point", value: "chat" });
    if (key === "dance" || key === "spin") {
      const dances = DANCES.filter((id) => this.avatar.animator.has(id));
      if (!dances.length) return false;
      return this._playClip(pick(dances), { full: true, maxSeconds: key === "spin" ? 3 : 8 });
    }
    const id = this.resolveGesture(name);
    if (!id) {
      // Expression-type names used as gestures (<gesture>wink</gesture>).
      return this.avatar.face.express(key, amount ?? 1, 3);
    }
    return this._playClip(id, { full: FULL_BODY.test(id) || FULL_BODY.test(key), maxSeconds: /Kneeling|Sitting/.test(id) ? 5 : null });
  }

  strikePose(name, amount = 1) {
    const base = POSES[name];
    if (!base) return false;
    const k = Math.max(0.4, Math.min(1, Number(amount ?? 1)));
    const spec = { ...base, lean: base.lean * k };
    const side = Math.random() < 0.5 ? -1 : 1;
    spec.turn = (spec.turn || 0) * side;
    if (spec.tilt == null) spec.tilt = side * (spec.tiltSize ?? 0.22); // head tips toward the side she turned
    this.avatar.body.strikePose(name, spec, spec.hold);
    this.avatar.face.express(spec.face, 0.9, spec.hold + 0.4);
    this.lookAt("user", spec.hold + 0.5, "pose");
    const clips = (spec.clips || []).filter((id) => this.avatar.animator.has(id));
    if (clips.length) this._playClip(pick(clips), { maxSeconds: spec.hold + 0.3 });
    this.lastIdleAction = performance.now();
    return true;
  }

  _playClip(id, { full = false, maxSeconds = null } = {}) {
    const anim = this.avatar.animator;
    if (full) this._frameFor("full");
    const playing = anim.playOnce(id);
    playing.then(() => { if (full) this._frameFor(null); });
    if (maxSeconds) this._later(maxSeconds * 1000, () => {
      if (anim.oneShot?.id === id) anim._onFinished({ action: anim.oneShot.action });
    });
    this.lastIdleAction = performance.now();
    return true;
  }

  _frameFor(name) {
    clearTimeout(this.wakeFrame);
    if (name) {
      this.avatar.setFrame(name);
    } else {
      this.wakeFrame = setTimeout(() => this.avatar.setFrame(this.userFrame || "full"), 400);
    }
  }

  _nod(amount = 0.15) {
    const body = this.avatar.body;
    let t = 0;
    const step = () => {
      t += 1 / 60;
      body.beat = Math.sin(Math.min(1, t / 0.45) * Math.PI) * amount;
      if (t < 0.45) requestAnimationFrame(step); else body.beat = 0;
    };
    requestAnimationFrame(step);
  }

  _lookAround() {
    const seq = [["left", 1.1], ["right", 1.1], ["user", 1.5]];
    let delay = 0;
    for (const [where, hold] of seq) {
      this._later(delay, () => this.lookAt(where, hold, "idle"));
      delay += hold * 1000;
    }
  }

  _later(ms, fn) {
    const id = setTimeout(() => { this.timers.delete(id); fn(); }, ms);
    this.timers.add(id);
    return id;
  }

  // ----- speech --------------------------------------------------------------
  // Called by the TTS player when a clip starts. `cues` carry `at` offsets in
  // `text`; each fires when playback reaches that point.
  speechStart({ analyser = null, text = "", cues = [], duration = 0 } = {}) {
    this.speaking = true;
    this.speechSerial = (this.speechSerial || 0) + 1;
    this._setMode("speaking");
    this.avatar.face.attachAnalyser(analyser);
    const seconds = duration && Number.isFinite(duration) ? duration : Math.max(1, text.length * 0.06);
    this.avatar.face.speakingFallback = analyser ? 0 : performance.now() + seconds * 1000;
    const plan = this._bodyLanguage(text, cues);
    const len = Math.max(1, text.length);
    this.speechTimers ||= new Set();
    for (const cue of plan) {
      const at = Math.min(0.92, cue.at / len) * seconds * 1000;
      const id = this._later(at, () => { this.speechTimers.delete(id); this.perform(cue); });
      this.speechTimers.add(id);
    }
    this.lookAt("user", Math.min(seconds, 3), "speech");
  }

  // Clip finished (or failed). Sentences arrive back to back, so only drop
  // out of the speaking pose if nothing new starts shortly.
  speechEnd() {
    this.speaking = false;
    this.avatar.face.attachAnalyser(null);
    this.avatar.face.speakingFallback = 0;
    const serial = this.speechSerial;
    this._later(900, () => {
      if (!this.speaking && serial === this.speechSerial && this.mode === "speaking") this._setMode("idle");
    });
  }

  // Playback was interrupted: drop cues that haven't happened yet.
  speechCancel() {
    for (const id of this.speechTimers || []) { clearTimeout(id); this.timers.delete(id); }
    this.speechTimers?.clear();
    this.speechEnd();
  }

  // Voice off: perform cues as the streamed text reveals them. `cues` is the
  // full list parsed so far; only ones not yet performed fire. With
  // final:true any stragglers fire and the stream state resets.
  streamCues(cues = [], { final = false } = {}) {
    const fresh = cues.slice(this.streamFired);
    this.streamFired = final ? 0 : cues.length;
    this.performSequence(fresh);
  }

  resetStream() {
    this.streamFired = 0;
  }

  // Perform a list of cues one after another (no audio to time them to).
  performSequence(cues = []) {
    let delay = 0;
    for (const cue of cues) {
      this._later(delay, () => this.perform(cue));
      delay += cue.type === "gesture" ? 900 : 350;
    }
    if (cues.length) this.lastReplyAt = performance.now();
  }

  // Replies read without voice: act them out with the same auto body
  // language a spoken reply would get.
  performText(text, cues = []) {
    this.performSequence(this._bodyLanguage(text, cues));
  }

  // Everything her body does while saying `text`: her own cues, the tone of
  // each sentence on her face, and natural movements if she chose none.
  _bodyLanguage(text, cues = []) {
    const moves = cues.some((c) => c.type === "gesture" || c.type === "point" || c.type === "look");
    const plan = [...cues, ...affectCues(text, cues), ...(moves ? [] : this._autoMoves(text))];
    return plan.sort((a, b) => a.at - b.at);
  }

  // Natural movements for words said without any of her own.
  _autoMoves(text) {
    const cues = [];
    const t = String(text || "");
    const lower = t.toLowerCase();
    if (/^\s*(hi|hello|hey|welcome( back)?|good (morning|evening|afternoon)|bye|goodbye|see you)\b/.test(lower)) cues.push({ type: "gesture", value: "wave", at: 0 });
    else if (/^\s*(yes|yeah|yep|sure|of course|absolutely|definitely|right)\b/.test(lower)) cues.push({ type: "gesture", value: "nod_small", at: 0 });
    else if (/^\s*(no|nope|not really|nah)\b/.test(lower)) cues.push({ type: "gesture", value: "shake_head", at: 0 });
    if (/\b(i think|maybe|perhaps|let me think|hmm)\b/.test(lower)) cues.push({ type: "look", value: "up", at: lower.search(/\b(i think|maybe|perhaps|let me think|hmm)\b/) });
    if (/\?\s*$/.test(t)) cues.push({ type: "gesture", value: "tilt", at: Math.max(0, t.length - 2) });
    if (/\b(above|up there|in the chat|below|here it is|look at)\b/.test(lower)) cues.push({ type: "point", value: "chat", at: lower.search(/\b(above|up there|in the chat|below|here it is|look at)\b/) });
    return cues;
  }

  // ----- app events ----------------------------------------------------------
  setMood(emotion, intensity = 0.4) {
    const e = String(emotion || "neutral").toLowerCase();
    const before = this._moodSet();
    this.mood = { emotion: e, intensity: Math.max(0, Math.min(1, Number(intensity) || 0)) };
    this.avatar.face.setBaseline(MOOD_FACE[e] || e, 0.2 + this.mood.intensity * 0.4);
    if (this.mode === "idle" && this._moodSet() !== before) this._setBaseForMode();
  }

  onUserTyping() {
    this._activity();
    this.lastTyping = performance.now();
    if (this.mode === "idle" || this.mode === "listening") this._setMode("listening");
    clearTimeout(this._typingIdle);
    this._typingIdle = setTimeout(() => { if (this.mode === "listening") this._setMode("idle"); }, 2500);
  }

  // She's using a tool: busy, focused on the screen (or the web).
  onWorking(tool) {
    this._setMode("thinking");
    this.avatar.face.express("thinking", 0.7, 4);
    const where = /look|control_input|open_item/.test(tool) ? "screen" : Math.random() < 0.5 ? "chat" : "down";
    this.lookAt(where, 3, "working");
  }

  // You started talking out loud: she turns to you and listens.
  onUserSpeaking() {
    this._activity();
    this.lastTyping = performance.now();
    clearTimeout(this._typingIdle);
    this.hearingVoice = true;
    if (this.mode !== "thinking") this._setMode("listening");
    this.lookAt("user", 4, "listening");
  }

  onUserStoppedSpeaking() {
    this.hearingVoice = false;
    if (this.mode === "listening") this._later(800, () => { if (this.mode === "listening") this._setMode("idle"); });
  }

  onUserMessage() {
    this.hearingVoice = false;
    this._activity();
    clearTimeout(this._typingIdle);
    this._setMode("thinking");
  }

  onModeHint(mode) {
    if (mode === "thinking") this._setMode("thinking");
    else if (mode === "listening") this._setMode("listening");
    else if (mode === "idle" && this.mode !== "speaking") this._setMode("idle");
  }

  onReply({ emotion, intensity } = {}) {
    if (emotion) this.setMood(emotion, intensity ?? 0.5);
    if (this.mode === "thinking") this._setMode("idle");
  }

  // Being touched: an instant reflex here; the sensation also goes to her
  // mind, which may answer in her own voice a moment later.
  onTouch(region) {
    this._activity();
    this.onSensation?.("touch", { region });
    if (region === "head") {
      this.avatar.face.express("shy", 1, 3);
      this.gesture("shy");
      this._later(300, () => this.lookAt("down", 1.2, "touch"));
    } else {
      this.avatar.face.express("happy", 1, 2.5);
      this.gesture(pick(["peace_lean", "happy_hands", "wave", "cute_pose"]));
      this.lookAt("user", 2, "touch");
    }
  }

  // What her body is doing, in words, for her mind (sent by presence.js and
  // told back to her each turn). Gaze targets stay symbolic ("user", "chat")
  // so the backend can name the user.
  describe() {
    const anim = this.avatar.animator;
    const now = performance.now();
    const pose = this.avatar.body.pose;
    const gesture = pose && now < pose.until ? POSES[pose.name]?.words || "posing"
      : anim.oneShot?.id ? GESTURE_WORDS[anim.oneShot.id] || "moving" : null;
    const stance = {
      speaking: "talking, gesturing along with your words",
      listening: "leaning in a little, listening",
      thinking: "thinking it over",
      idle: {
        calm: "standing beside the chat, relaxed", happy: "standing beside the chat, bright and a little bouncy",
        cool: "standing beside the chat, cool and composed", sad: "standing beside the chat, a bit subdued",
        sleepy: "standing beside the chat, drowsy",
      }[this._moodSet()],
    }[this.mode];
    const face = this.avatar.face;
    const active = face.override && now < face.override.until ? face.override.recipe : face.baseline.recipe;
    return {
      renderer: "vrm",
      mode: this.mode,
      activity: gesture ? `${gesture} (${stance})` : stance,
      expression: FACE_WORDS[active] || null,
      looking_at: this.attention?.name || "user",
      frame: { upper: "upper body", face: "face, close up", full: "whole body" }[this.avatar.frame] || null,
      visible: document.visibilityState === "visible",
      user_typing: now - this.lastTyping < 3000,
      user_idle_seconds: Math.round((now - this.lastActivity) / 1000),
      feeling: this.feeling?.label || null,
    };
  }

  _activity() {
    const now = performance.now();
    const away = now - this.lastActivity;
    this.lastActivity = now;
    // Back after a while: she notices (and her mind may say hello).
    if (away > 120000) this.onSensation?.("returned", { away_seconds: Math.round(away / 1000) });
    if (away > 120000 && this.mode === "idle") {
      if (away > 300000) this._setBaseForMode(); // wake up from the sleepy idle
      this.lookAt("user", 2.5, "greet");
      this.avatar.face.express("happy", 0.9, 3);
      this.gesture("wave");
      this._later(2800, () => { if (this.mode === "idle") this.gesture("lean_toward"); });
    }
  }

  _setMode(mode) {
    if (this.mode === mode) return;
    this.mode = mode;
    const face = this.avatar.face;
    const body = this.avatar.body;
    body.leanTarget = 0;
    body.approachTarget = 0;
    if (mode === "listening") {
      // You're talking to her: she leans in toward you while she listens.
      body.leanTarget = 0.12;
      body.approachTarget = 0.06;
      face.express("smile", 0.6, 3);
      this.lookAt("input", 3, "listening");
    } else if (mode === "thinking") {
      face.express("thinking", 1, 6);
      this.lookAt(Math.random() < 0.5 ? "up" : "away", 2.2, "thinking");
      this._later(1200, () => { if (this.mode === "thinking" && !this.avatar.animator.busy) this.gesture("think"); });
    } else if (mode === "idle") {
      this.lookAt("user", 2, "idle");
    }
    this._setBaseForMode();
  }

  _moodSet() {
    const hour = new Date().getHours();
    const e = this.mood.emotion;
    if (performance.now() - this.lastActivity > 300000) return "sleepy"; // left alone a while
    if (["tired", "sleepy", "bored"].includes(e)) return "sleepy";
    if (["sad", "hurt", "lonely", "cry", "worried", "guilty"].includes(e) && this.mood.intensity > 0.3) return "sad";
    if (hour >= 23 || hour < 5) return "sleepy";
    if (["happy", "excited", "affectionate", "loving", "playful", "proud", "delighted", "thrilled", "smile", "amused"].includes(e)
      && this.mood.intensity > 0.45) return "happy";
    return "calm";
  }

  _setBaseForMode() {
    const anim = this.avatar.animator;
    if (this.mode === "speaking") {
      anim.setBase(pick(TALKING), 0.5);
    } else {
      const set = IDLE_SETS[this._moodSet()] || IDLE_SETS.calm;
      anim.setBase(pick(set.filter((id) => anim.has(id))) || "119_Idle", 0.8);
    }
  }

  _bindWindow() {
    window.addEventListener("mousemove", (ev) => {
      this.cursor = { x: ev.clientX, y: ev.clientY, at: performance.now() };
      this._activity();
    }, { passive: true });
    window.addEventListener("focus", () => this._activity());
    // A single click is a touch; a double-click only changes the view, so
    // wait a moment before treating the click as touching her.
    this.avatar.canvas.addEventListener("pointerdown", (ev) => {
      const r = this.avatar.canvas.getBoundingClientRect();
      const y = (ev.clientY - r.top) / r.height;
      // Where her head actually is on screen (any framing, any pose): the
      // head bone sits at the base of the skull, so anything above chin level
      // counts as her head.
      const head = this.avatar.headPosition(new THREE.Vector3()).project(this.avatar.camera);
      const headY = Math.min(0.95, (1 - head.y) / 2 + 0.04);
      clearTimeout(this._touchTimer);
      if (ev.detail > 1) return;
      this._touchTimer = setTimeout(() => this.onTouch(y < headY ? "head" : "body"), 260);
    });
    this.avatar.canvas.addEventListener("dblclick", () => {
      clearTimeout(this._touchTimer);
      const order = ["full", "upper", "face"];
      this.userFrame = order[(order.indexOf(this.avatar.frame) + 1) % order.length];
      this.avatar.setFrame(this.userFrame);
      try { localStorage.setItem("sarah.avatar.view", this.userFrame); } catch {}
    });
    try {
      // New key: older saved framings were tighter; start from the full view.
      const saved = localStorage.getItem("sarah.avatar.view");
      if (saved) { this.userFrame = saved; this.avatar.setFrame(saved, true); }
    } catch {}
  }

  // ----- per frame -----------------------------------------------------------
  update(dt, now) {
    const body = this.avatar.body;

    // Attention: explicit/temporary targets win; otherwise choose by mode.
    if (this.attention && now > this.attention.until) this.attention = null;
    if (!this.attention) this._chooseAttention(now);
    const target = this.attention?.target?.();
    if (target) {
      // Micro-saccades keep the eyes from looking painted on.
      if (now > this.nextGlance) {
        this.saccade = new THREE.Vector3(rand(-0.03, 0.03), rand(-0.02, 0.02), 0);
        this.nextGlance = now + rand(350, 1600);
      }
      body.gazeTarget.lerp(target.add(this.saccade || new THREE.Vector3()), 1 - Math.exp(-10 * dt));
    }

    // Speech beats: nod on loud syllables.
    const energy = this.avatar.face.energy;
    this.energyAvg = (this.energyAvg ?? 0) * 0.97 + energy * 0.03;
    if (this.mode === "speaking") {
      const beat = Math.max(0, energy - this.energyAvg * 1.25) * 0.35;
      body.beat = THREE.MathUtils.damp(body.beat, beat, 10, dt);
    }

    // Listening: small acknowledging nods while the user types.
    if (this.mode === "listening" && now > this.nextNod) {
      this._nod(0.08);
      this.nextNod = now + rand(3500, 7000);
    }

    // Idle life: something now and then (every 1-2.5 min), a pose only
    // sometimes; drift off when left alone.
    if (this.mode === "idle" && !this.avatar.animator.busy && !body.pose) {
      this.idleGap ??= rand(...IDLE_GAP);
      if (now - this.lastIdleAction > this.idleGap) {
        const set = IDLE_ACTIONS[this._moodSet()] || IDLE_ACTIONS.calm;
        const poses = set.filter((g) => POSES[g]);
        const small = set.filter((g) => !POSES[g]);
        this.gesture(poses.length && (!small.length || Math.random() < POSE_SHARE) ? pick(poses) : pick(small));
        this.lastIdleAction = now;
        this.idleGap = rand(...IDLE_GAP);
        if (Math.random() < 0.35) this._setBaseForMode(); // change stance now and then
      }
    }
  }

  _chooseAttention(now) {
    const cursorFresh = now - this.cursor.at < 2500;
    if (this.mode === "listening") return this.lookAt(this.hearingVoice ? "user" : "input", 2.5, "listening");
    if (this.mode === "thinking") return this.lookAt(pick(["up", "away", "up"]), rand(1.2, 2.2), "thinking");
    if (this.mode === "speaking") {
      // Mostly eye contact, with brief glances away like people do mid-thought.
      return this.lookAt(Math.random() < 0.8 ? "user" : pick(["up", "away", "chat"]), rand(1.2, 3), "speaking");
    }
    if (cursorFresh) return this.lookAt("cursor", 0.4, "cursor");
    const roll = Math.random();
    const where = roll < 0.55 ? "user" : roll < 0.7 ? "chat" : roll < 0.85 ? pick(["left", "right", "up"]) : "down";
    return this.lookAt(where, rand(2, 6), "idle");
  }
}
