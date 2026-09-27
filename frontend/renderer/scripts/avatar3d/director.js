// The director turns what is happening in the app into what Sarah's body
// does. It has three jobs:
//   1. Perform stage directions from her replies (<face>, <look>, <point>,
//      <gesture>), each at the moment its words are spoken.
//   2. Keep her alive between prompts: attention/gaze shifts, idle actions,
//      listening while the user types, thinking while waiting, greeting the
//      user when they come back, reacting to being clicked.
//   3. Add natural body language when a reply carries no cues of its own
//      (nod on "yes", tilt on questions, wave on greetings...).
import * as THREE from "three";

// Friendly gesture names -> animation ids (catalog) or procedural actions.
export const GESTURES = {
  wave: "161_Waving", hello: "79_Standing Greeting", greet: "79_Standing Greeting", hi: "dm_4",
  nod: "118_Head Nod Yes", yes: "118_Head Nod Yes", agree: "dm_12",
  shake_head: "144_Shaking Head No", no: "144_Shaking Head No", refuse: "dm_27", tease: "56_No", annoyed: "95_Annoyed Head Shake",
  crouch: "50_Kneeling Idle", kneel: "50_Kneeling Idle", sit: "75_Sitting", curious: "52_Looking", search: "52_Looking",
  shrug: "145_Shrugging", whatever: "93_Whatever Gesture",
  think: "88_Thinking", thinking: "88_Thinking", thinking_pose: "88_Thinking", idea: "dm_108", point_up: "dm_108",
  clap: "19_Clapping", cheer: "dm_28", encourage: "dm_2", yay: "dm_45", excited: "dm_53",
  thanks: "156_Thankful", thankful: "156_Thankful", hand_to_chest: "156_Thankful", grateful: "156_Thankful",
  bow: "138_Quick Informal Bow", formal_bow: "137_Quick Formal Bow", cute_bow: "dm_58",
  happy_hands: "116_Happy Hand Gesture", present: "dm_0", explain: "dm_90", show: "dm_0",
  peace: "dm_26", victory: "dm_30", heart: "dm_29", blow_kiss: "dm_20", kiss: "dm_41",
  shy: "dm_51", blush: "dm_51", sorry: "dm_40", apologize: "dm_40",
  shush: "dm_42", salute: "dm_10", tsundere: "dm_8", pout: "dm_8", tantrum: "dm_9",
  angry: "94_Angry Gesture", frustrated: "0_Angry",
  sigh: "65_Relieved Sigh", relieved: "65_Relieved Sigh", stretch: "131_Neck Stretching", yawn: "163_Yawn",
  jump: "49_Joyful Jump", joy: "49_Joyful Jump", arms_up: "dm_19", cute_jump: "dm_32", laugh: "dm_2",
  cat: "dm_43", nya: "dm_48", dog: "dm_47", tiger: "dm_57",
  cry: "22_Crying", sob: "23_Crying_2", defeat: "26_Defeat", facepalm: "26_Defeat",
  raise_hand: "39_Hand Raising", question: "39_Hand Raising", sing: "71_Singing", drum: "dm_94",
  throw: "158_Throw", phone: "155_Talking On Phone", distant: "142_Sad Idle",
};
export const PROCEDURAL = ["lean_in", "step_back", "tilt", "nod_small", "look_around", "surprise", "dance", "spin"];
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
  calm: ["131_Neck Stretching", "look_around", "tilt", "dm_101"],
  happy: ["dm_26", "look_around", "dm_24", "116_Happy Hand Gesture"],
  sad: ["65_Relieved Sigh", "look_around"],
  sleepy: ["163_Yawn", "131_Neck Stretching"],
};
const MOOD_FACE = {
  happy: "happy", excited: "excited", affectionate: "shy", shy: "shy", confused: "thinking",
  surprised: "surprised", angry: "annoyed", frustrated: "annoyed", sad: "sad", neutral: "neutral",
  tired: "sleepy", loving: "shy", embarrassed: "shy", smug: "smug",
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
    this.attention = { target: fn, until: performance.now() + hold * 1000, source };
    if (big) this.avatar.face.blink(); // people blink on large gaze shifts
    return true;
  }

  // ----- stage directions ----------------------------------------------------
  perform(cue) {
    if (!cue) return false;
    const { type, value, amount } = cue;
    if (type === "face") return this.avatar.face.express(value, amount ?? 1, 4.5);
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
    if (key === "lean_in") { body.leanTarget = 0.14; this._later(1600, () => (body.leanTarget = 0)); return true; }
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
      this.wakeFrame = setTimeout(() => this.avatar.setFrame(this.userFrame || "upper"), 400);
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
    const plan = cues.length ? cues : this._autoCues(text);
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
    this.performSequence(cues.length ? cues : this._autoCues(text));
  }

  // Natural body language for replies without explicit cues.
  _autoCues(text) {
    const cues = [];
    const t = String(text || "");
    const lower = t.toLowerCase();
    if (/^\s*(hi|hello|hey|welcome( back)?|good (morning|evening|afternoon)|bye|goodbye|see you)\b/.test(lower)) cues.push({ type: "gesture", value: "wave", at: 0 });
    else if (/^\s*(yes|yeah|yep|sure|of course|absolutely|definitely|right)\b/.test(lower)) cues.push({ type: "gesture", value: "nod_small", at: 0 });
    else if (/^\s*(no|nope|not really|nah)\b/.test(lower)) cues.push({ type: "gesture", value: "shake_head", at: 0 });
    if (/\b(i think|maybe|perhaps|let me think|hmm)\b/.test(lower)) cues.push({ type: "look", value: "up", at: lower.search(/\b(i think|maybe|perhaps|let me think|hmm)\b/) });
    if (/\b(thank you|thanks)\b/.test(lower)) cues.push({ type: "face", value: "smile", at: lower.search(/\b(thank you|thanks)\b/) });
    if (/!\s*$/.test(t)) cues.push({ type: "face", value: "happy", at: Math.max(0, t.length - 2) });
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
    if (this.mode === "idle" || this.mode === "listening") this._setMode("listening");
    clearTimeout(this._typingIdle);
    this._typingIdle = setTimeout(() => { if (this.mode === "listening") this._setMode("idle"); }, 2500);
  }

  onUserMessage() {
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

  onTouch(region) {
    this._activity();
    if (region === "head") {
      this.avatar.face.express("shy", 1, 3);
      this.gesture("shy");
      this._later(300, () => this.lookAt("down", 1.2, "touch"));
    } else {
      this.avatar.face.express("happy", 1, 2.5);
      this.gesture(pick(["peace", "happy_hands", "wave"]));
      this.lookAt("user", 2, "touch");
    }
  }

  _activity() {
    const now = performance.now();
    const away = now - this.lastActivity;
    this.lastActivity = now;
    // Back after a while: greet them.
    if (away > 120000 && this.mode === "idle") {
      if (away > 300000) this._setBaseForMode(); // wake up from the sleepy idle
      this.lookAt("user", 2.5, "greet");
      this.avatar.face.express("happy", 0.9, 3);
      this.gesture("wave");
    }
  }

  _setMode(mode) {
    if (this.mode === mode) return;
    this.mode = mode;
    const face = this.avatar.face;
    const body = this.avatar.body;
    body.leanTarget = 0;
    if (mode === "listening") {
      body.leanTarget = 0.06;
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
    if (["sad", "tired"].includes(e)) return e === "tired" ? "sleepy" : "sad";
    if (hour >= 23 || hour < 5) return "sleepy";
    if (["happy", "excited", "affectionate", "loving"].includes(e) && this.mood.intensity > 0.45) return "happy";
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
    this.avatar.canvas.addEventListener("pointerdown", (ev) => {
      const r = this.avatar.canvas.getBoundingClientRect();
      const y = (ev.clientY - r.top) / r.height;
      const headY = this.avatar.frame === "face" ? 0.7 : this.avatar.frame === "upper" ? 0.4 : 0.22;
      this.onTouch(y < headY ? "head" : "body");
    });
    this.avatar.canvas.addEventListener("dblclick", () => {
      const order = ["upper", "face", "full"];
      this.userFrame = order[(order.indexOf(this.avatar.frame) + 1) % order.length];
      this.avatar.setFrame(this.userFrame);
      try { localStorage.setItem("sarah.avatar.frame", this.userFrame); } catch {}
    });
    try {
      const saved = localStorage.getItem("sarah.avatar.frame");
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

    // Idle life: something small every 15-40 s; drift off when left alone.
    if (this.mode === "idle" && !this.avatar.animator.busy) {
      this.idleGap ??= rand(15000, 40000);
      if (now - this.lastIdleAction > this.idleGap) {
        const set = IDLE_ACTIONS[this._moodSet()] || IDLE_ACTIONS.calm;
        this.gesture(pick(set));
        this.lastIdleAction = now;
        this.idleGap = rand(15000, 40000);
        if (Math.random() < 0.35) this._setBaseForMode(); // change stance now and then
      }
    }
  }

  _chooseAttention(now) {
    const cursorFresh = now - this.cursor.at < 2500;
    if (this.mode === "listening") return this.lookAt("input", 2.5, "listening");
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
