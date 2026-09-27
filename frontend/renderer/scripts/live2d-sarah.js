// ======================================================================
// Sarah V10 — Live2D Controller + Emotion Brain + Deep Character Layer
// Single-file body language & emotion engine.
// ======================================================================

window.SARAH_LIVE2D = window.SARAH_LIVE2D || {};
window.SARAH_EMOTION = window.SARAH_EMOTION || {};
window.SARAH_LIVE2D_AUDIO = window.SARAH_LIVE2D_AUDIO || {
  enabled: false,
  level: 0,
  mouth: 0,
};
window.SARAH_LIVE2D_STATE = window.SARAH_LIVE2D_STATE || {
  mode: "idle",
  thinking: false,
  listening: false,
  speaking: false,
  lowEnergy: false,
  activeViseme: null,
};

const SARAH_CUBISM_PARAMS = Object.freeze({
  headX: "ParamAngleX",
  headY: "ParamAngleY",
  headZ: "ParamAngleZ",
  bodyX: "ParamBodyAngleX",
  bodyY: "ParamBodyAngleY",
  bodyZ: "ParamBodyAngleZ",
  breath: "ParamBreath",
  eyeLOpen: "ParamEyeLOpen",
  eyeROpen: "ParamEyeROpen",
  eyeX: "ParamEyeBallX",
  eyeY: "ParamEyeBallY",
  mouthOpen: "ParamMouthOpenY",
  mouthForm: "ParamMouthForm",
  browLY: "ParamBrowLY",
  browRY: "ParamBrowRY",
  // (ganyu's generic Param40-46 expression toggles were removed in the Lisette
  // migration; Lisette drives expressions through named .exp3.json files.)
});

// Issue #32/#33: Lisette ships with named expression files (angry, sad,
// shy, frenzy, tear, tongue_out, dark_mask, sans_eye_glow). Map our
// emotion canonicals onto those + face-param shortcuts (ParamCheek for
// blush/joy, ParamSad/ParamAngry as additive overlays).
//
// `frustrated` intentionally maps to null instead of "frenzy" — the frenzy
// expression file applies dark-mask + red-glow + crooked-mouth which reads
// as horror/menace, not mild irritation. For routine frustration the
// browLY/browRY easing in applySarahMoodState is enough; reserve the
// frenzy expression for explicit angry blasts only.
const SARAH_EXPRESSION_BY_EMOTION = Object.freeze({
  happy: null,
  excited: null,
  affectionate: "shy",
  shy: "shy",
  embarrassed: "shy",
  confused: null,
  surprised: null,
  angry: "angry",
  frustrated: null,
  sad: "sad",
  loving: "shy",
  smug: null,
  tired: "sad",
  neutral: null,
});

const SARAH_CANONICAL_AVATAR_STATES = Object.freeze([
  "neutral",
  "happy",
  "excited",
  "affectionate",
  "shy",
  "confused",
  "surprised",
  "angry",
  "frustrated",
  "sad",
  "thinking",
  "listening",
  "speaking",
  "low_energy",
]);

const SARAH_AVATAR_STATE_RECIPES = Object.freeze({
  neutral: { emotion: "neutral", intensity: 0.15 },
  happy: { emotion: "happy", intensity: 0.75 },
  excited: { emotion: "excited", intensity: 0.85 },
  affectionate: { emotion: "affectionate", intensity: 0.75 },
  shy: { emotion: "shy", intensity: 0.75 },
  confused: { emotion: "confused", intensity: 0.65 },
  surprised: { emotion: "surprised", intensity: 0.8 },
  angry: { emotion: "angry", intensity: 0.7 },
  frustrated: { emotion: "frustrated", intensity: 0.65 },
  sad: { emotion: "sad", intensity: 0.65 },
  thinking: { emotion: "confused", intensity: 0.45 },
  listening: { emotion: "neutral", intensity: 0.35 },
  speaking: { emotion: "happy", intensity: 0.35 },
  low_energy: { emotion: "sad", intensity: 0.45 },
});

const SARAH_VISEME_SHAPES = Object.freeze({
  closed: { mouthOpen: 0, mouthForm: -0.15 },
  neutral: { mouthOpen: 0.12, mouthForm: 0 },
  A: { mouthOpen: 0.9, mouthForm: 0.05 },
  E: { mouthOpen: 0.45, mouthForm: 0.45 },
  I: { mouthOpen: 0.35, mouthForm: 0.65 },
  O: { mouthOpen: 0.72, mouthForm: -0.45 },
  U: { mouthOpen: 0.5, mouthForm: -0.65 },
  smile: { mouthOpen: 0.22, mouthForm: 0.75 },
});

const SARAH_EMOTION_ALIASES = Object.freeze({
  joy: "happy",
  affection: "affectionate",
  loving: "affectionate",
  flirty: "affectionate",
  annoyed: "frustrated",
});

const SARAH_SEMANTIC_AVATAR_PARAM_MAP = Object.freeze({
  brow_angle: ["browLY", "browRY"],
  mouth_smile: ["mouthForm"],
  body_tilt: ["bodyZ"],
});

const sarahMissingParamWarnings = new Set();
const sarahExpressionWarnings = new Set();
const sarahRuntimeWarnings = new Set();
const sarahVisemeTimers = new Set();
const sarahVisemeStats = {
  framesApplied: 0,
  framesDropped: 0,
  lastViseme: null,
  timelineActive: false,
};

window.SARAH_LIVE2D.PARAMS = SARAH_CUBISM_PARAMS;
window.SARAH_LIVE2D.CANONICAL_STATES = SARAH_CANONICAL_AVATAR_STATES;
window.SARAH_LIVE2D.getMissingParamWarnings = () =>
  Array.from(sarahMissingParamWarnings);
window.SARAH_LIVE2D.getExpressionWarnings = () =>
  Array.from(sarahExpressionWarnings);

// -------------------------------------------------------------
// Utility: smooth parameter interpolation
// -------------------------------------------------------------
function clamp01(value, fallback = 0) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(0, Math.min(1, n));
}

function ensureSarahLive2DState() {
  const state = window.SARAH_LIVE2D_STATE || {};
  if (!state.mode) state.mode = "idle";
  state.thinking = Boolean(state.thinking);
  state.listening = Boolean(state.listening);
  state.speaking = Boolean(state.speaking);
  state.lowEnergy = Boolean(state.lowEnergy);
  if (!("activeViseme" in state)) state.activeViseme = null;
  window.SARAH_LIVE2D_STATE = state;
  return state;
}

function normalizeAvatarMode(mode) {
  const raw = String(mode || "idle").toLowerCase().replace(/-/g, "_");
  if (raw === "sleepy" || raw === "lowenergy") return "low_energy";
  if (raw === "idle") return "idle";
  if (SARAH_CANONICAL_AVATAR_STATES.includes(raw)) return raw;
  return "idle";
}

function normalizeViseme(viseme) {
  const raw = String(viseme || "neutral").trim();
  const upper = raw.toUpperCase();
  if (SARAH_VISEME_SHAPES[upper]) return upper;
  const lower = raw.toLowerCase();
  if (lower === "sil" || lower === "silence" || lower === "rest") return "closed";
  if (lower === "aa" || lower === "ah") return "A";
  if (lower === "eh") return "E";
  if (lower === "ih" || lower === "iy" || lower === "y") return "I";
  if (lower === "oh" || lower === "ow") return "O";
  if (lower === "uw" || lower === "oo") return "U";
  if (SARAH_VISEME_SHAPES[lower]) return lower;
  return null;
}

function getSarahCoreModel() {
  const model = window.SARAH_LIVE2D.model;
  return model?.internalModel?.coreModel || null;
}

function hasParam(id) {
  const ids = window.SARAH_LIVE2D.parameterIds;
  return !ids || ids.size === 0 || ids.has(id);
}

function warnMissingParam(id) {
  if (sarahMissingParamWarnings.has(id)) return;
  sarahMissingParamWarnings.add(id);
  console.warn("[Live2D] Model parameter not found:", id);
}

function setParam(id, target, blend = 1) {
  const core = getSarahCoreModel();
  if (!core || !id) return false;
  if (!hasParam(id)) {
    warnMissingParam(id);
    return false;
  }

  try {
    core.setParameterValueById(id, target, blend);
    return true;
  } catch {
    warnMissingParam(id);
    return false;
  }
}

function easeParam(id, target, speed = 0.25) {
  const core = getSarahCoreModel();
  if (!core || !id) return false;
  if (!hasParam(id)) {
    warnMissingParam(id);
    return false;
  }

  try {
    const current = core.getParameterValueById(id) || 0;
    const next = current + (target - current) * speed;
    core.setParameterValueById(id, next);
    return true;
  } catch {
    warnMissingParam(id);
    return false;
  }
}

function easeMappedParam(key, target, speed = 0.25) {
  return easeParam(SARAH_CUBISM_PARAMS[key], target, speed);
}

function animateParam(id, amount, speed = 0.2) {
  easeParam(id, amount, speed);
}

function animateMappedParam(key, amount, speed = 0.2) {
  easeMappedParam(key, amount, speed);
}

function setExternalVoiceLevel(level, options = {}) {
  const audio = window.SARAH_LIVE2D_AUDIO;
  const nextLevel = clamp01(level);
  audio.level = nextLevel;
  audio.mouth = audio.mouth * 0.55 + nextLevel * 0.45;
  audio.externalActive = nextLevel > 0.01;

  if (options.driveMouth !== false) {
    easeMappedParam("mouthOpen", audio.mouth * 1.2, 0.55);
  }
}

window.SARAH_LIVE2D.setVoiceLevel = setExternalVoiceLevel;
window.SARAH_LIVE2D.clearVoiceLevel = function () {
  setExternalVoiceLevel(0);
};

function normalizeSarahEmotion(emotion) {
  const raw = String(emotion || "neutral").toLowerCase();
  return SARAH_EMOTION_ALIASES[raw] || raw;
}

function collectSarahParameterIds(core) {
  const rawIds = core?._parameterIds || core?._model?.parameters?.ids || [];
  window.SARAH_LIVE2D.parameterIds = new Set(Array.from(rawIds));
}

function warnLive2DRuntimeOnce(key, message, err) {
  if (sarahRuntimeWarnings.has(key)) return;
  sarahRuntimeWarnings.add(key);
  console.warn(message, err);
}

function updateSarahEyeBlink(internal, core, dtSeconds, blinkSpeed) {
  const eyeBlink = internal?.eyeBlink;
  if (!eyeBlink || !core) return;

  const speed = Math.max(0.4, Number(blinkSpeed) || 1);
  const seconds = Math.max(0, dtSeconds * speed);

  try {
    if (typeof eyeBlink.updateParameters === "function") {
      eyeBlink.updateParameters(core, seconds);
      return;
    }

    if (typeof eyeBlink.update === "function") {
      eyeBlink.update(seconds * 1000);
      return;
    }

    warnLive2DRuntimeOnce(
      "eye-blink-api",
      "[Live2D] Eye blink controller has no supported update API."
    );
  } catch (err) {
    warnLive2DRuntimeOnce(
      "eye-blink-update",
      "[Live2D] Eye blink update skipped after runtime error:",
      err
    );
  }
}

// Lisette's expression files (frenzy, angry, sad, dark_mask, sans_eye_glow,
// tear, tongue_out) drive these emotion-overlay params. When clearing an
// expression we must also force them back to 0 — expressionManager.reset
// stops applying the expression, but the last-set values stick on the
// coreModel until something else writes them. Without this, an angry/frenzy
// face never relaxes back to neutral.
const SARAH_EMOTION_OVERLAY_PARAMS = Object.freeze([
  "ParamFrenzy",
  "ParamAngry",
  "ParamSad",
  "ParamCheek",
  "ParamCrooked",
  "ParamShudder",
  "ParamTongueOut",
  "ParamRedGlow",
  "ParamRedHighlight",
  "ParamDarkMask",
  "ParamDarkAngry",
  "ParamDarkFrenzy",
  "ParamDarkSad",
  "ParamTear",
]);

function clearSarahEmotionOverlayParams() {
  for (const id of SARAH_EMOTION_OVERLAY_PARAMS) {
    if (hasParam(id)) easeParam(id, 0, 0.4);
  }
}

function setSarahExpression(name) {
  const model = window.SARAH_LIVE2D.model;
  if (!model) return Promise.resolve(false);

  // Issue #7: model.expression() with no args was auto-loading the first
  // expression in the model3.json (CHIDAI / dazed), so the avatar booted into
  // a shocked face instead of neutral. When name is null we now explicitly
  // call expressionManager.resetExpression() (the documented clear API) and
  // zero the overlay params — without this an applied frenzy/angry face
  // would stick forever once set, since param values persist on the
  // coreModel even after the expression stops being multiplied in.
  if (!name) {
    try {
      const expressionManager =
        model.internalModel?.motionManager?.expressionManager;
      expressionManager?.resetExpression?.();
    } catch (err) {
      console.warn("[Live2D] resetExpression failed:", err);
    }
    clearSarahEmotionOverlayParams();
    return Promise.resolve(true);
  }

  if (!model.expression) return Promise.resolve(false);
  return model.expression(name).catch((err) => {
    if (!sarahExpressionWarnings.has(name)) {
      sarahExpressionWarnings.add(name);
      console.warn("[Live2D] Failed to apply expression:", name, err);
    }
    return false;
  });
}

function resetSarahExpressionToggles() {
  // No-op on the Lisette rig: expressions are managed via named .exp3.json
  // files (applySarahMoodState → setSarahExpression), not per-param toggles.
  // Kept as a stable hook so callers don't need to branch on the active model.
}

function applySemanticAvatarParams(avatarParams, intensity) {
  const semantic = avatarParams?.parameters || avatarParams || {};
  if (!semantic || typeof semantic !== "object") return;

  for (const [key, value] of Object.entries(semantic)) {
    const mapped = SARAH_SEMANTIC_AVATAR_PARAM_MAP[key];
    if (!mapped) continue;

    const n = Number(value);
    if (!Number.isFinite(n)) continue;

    for (const paramKey of mapped) {
      const scale = paramKey === "bodyZ" ? 10 : 1;
      easeMappedParam(paramKey, n * intensity * scale, 0.3);
    }
  }
}

function applySarahMoodState(mood = {}, avatarParams = null) {
  const emotion = normalizeSarahEmotion(mood.emotion || avatarParams?.emotion);
  const intensity = clamp01(
    mood.intensity ?? avatarParams?.intensity_multiplier,
    0
  );

  resetSarahExpressionToggles();
  setSarahExpression(SARAH_EXPRESSION_BY_EMOTION[emotion] || null);

  easeMappedParam("browLY", 0, 0.35);
  easeMappedParam("browRY", 0, 0.35);
  easeMappedParam("mouthForm", 0, 0.35);
  easeMappedParam("mouthOpen", 0, 0.3);
  easeMappedParam("headX", 0, 0.22);
  easeMappedParam("headY", 0, 0.22);
  easeMappedParam("bodyX", 0, 0.22);
  easeMappedParam("bodyY", 0, 0.22);
  easeMappedParam("bodyZ", 0, 0.22);

  if (emotion === "happy") {
    easeMappedParam("mouthForm", 0.55 * intensity, 0.3);
    easeMappedParam("bodyY", 3 * intensity, 0.25);
  } else if (emotion === "excited") {
    easeMappedParam("mouthForm", 0.75 * intensity, 0.3);
    easeMappedParam("mouthOpen", 0.25 * intensity, 0.25);
    easeMappedParam("bodyY", 5 * intensity, 0.25);
    easeMappedParam("bodyZ", 3 * intensity, 0.25);
  } else if (emotion === "affectionate" || emotion === "shy") {
    easeMappedParam("mouthForm", 0.35 * intensity, 0.3);
    easeMappedParam("headZ", -8 * intensity, 0.25);
    easeMappedParam("bodyX", -5 * intensity, 0.25);
    easeMappedParam("eyeY", -0.35 * intensity, 0.25);
  } else if (emotion === "sad") {
    easeMappedParam("browLY", 0.35 * intensity, 0.25);
    easeMappedParam("browRY", 0.35 * intensity, 0.25);
    easeMappedParam("mouthForm", -0.45 * intensity, 0.25);
    easeMappedParam("eyeY", 0.7 * intensity, 0.25);
    easeMappedParam("bodyX", -5 * intensity, 0.25);
  } else if (emotion === "angry") {
    easeMappedParam("browLY", -0.65 * intensity, 0.25);
    easeMappedParam("browRY", -0.65 * intensity, 0.25);
    easeMappedParam("mouthForm", -0.45 * intensity, 0.25);
    easeMappedParam("mouthOpen", 0.25 * intensity, 0.25);
    easeMappedParam("bodyX", 2 * intensity, 0.25);
  } else if (emotion === "frustrated") {
    easeMappedParam("browLY", -0.45 * intensity, 0.25);
    easeMappedParam("browRY", -0.45 * intensity, 0.25);
    easeMappedParam("mouthForm", -0.25 * intensity, 0.25);
    easeMappedParam("headZ", 3 * intensity, 0.25);
  } else if (emotion === "confused") {
    easeMappedParam("browLY", 0.25 * intensity, 0.25);
    easeMappedParam("browRY", -0.2 * intensity, 0.25);
    easeMappedParam("headZ", -5 * intensity, 0.25);
  } else if (emotion === "surprised") {
    easeMappedParam("mouthOpen", 0.75 * intensity, 0.25);
    easeMappedParam("headY", 5 * intensity, 0.25);
    easeMappedParam("bodyY", -6 * intensity, 0.25);
  }

  applySemanticAvatarParams(avatarParams, intensity);
}

window.SARAH_LIVE2D.applyMoodState = applySarahMoodState;
window.SARAH_LIVE2D.setExpression = setSarahExpression;
window.SARAH_LIVE2D.setEmotion = (emotion, intensity = 0) => {
  applySarahMoodState({ emotion, intensity });
};
window.SARAH_LIVE2D.setParameter = setParam;
window.SARAH_LIVE2D.easeParameter = easeParam;
window.SARAH_LIVE2D.easeMappedParameter = easeMappedParam;
// Direct-write variants — used by the gesture applier inside the
// `beforeModelUpdate` hook. easeParameter blends from the param's CURRENT
// value (which the motion update has just overwritten to its motion-driven
// value), so it can never reach the target as long as motion keeps
// re-running. setMappedParameter writes the full value, replacing motion's
// value for the current frame's render.
window.SARAH_LIVE2D.setMappedParameter = function (key, value, blend = 1) {
  return setParam(SARAH_CUBISM_PARAMS[key], value, blend);
};
window.SARAH_LIVE2D.applyParameterMap = function (paramMap = {}, options = {}) {
  const speed = options.speed ?? 0.22;
  const intensity = clamp01(options.intensity ?? 1, 1);
  for (const [key, value] of Object.entries(paramMap || {})) {
    const target = Number(value);
    if (!Number.isFinite(target)) continue;
    if (SARAH_CUBISM_PARAMS[key]) {
      easeMappedParam(key, target * intensity, speed);
    } else if (/^Param[A-Za-z0-9_]+$/.test(key)) {
      easeParam(key, target * intensity, speed);
    }
  }
};

function applyAvatarModePose(mode, intensity = 0.5) {
  const strength = clamp01(intensity, 0.5);

  if (mode === "thinking") {
    setSarahExpression(null);
    easeMappedParam("headX", -6 * strength, 0.28);
    easeMappedParam("headY", 4 * strength, 0.28);
    easeMappedParam("headZ", -5 * strength, 0.24);
    easeMappedParam("eyeY", -0.45 * strength, 0.25);
    easeMappedParam("browLY", 0.22 * strength, 0.24);
    easeMappedParam("browRY", -0.18 * strength, 0.24);
    easeMappedParam("mouthForm", -0.08 * strength, 0.25);
  } else if (mode === "listening") {
    setSarahExpression(null);
    easeMappedParam("headX", 0, 0.22);
    easeMappedParam("headY", 2.5 * strength, 0.25);
    easeMappedParam("bodyY", 3.5 * strength, 0.24);
    easeMappedParam("eyeY", 0.18 * strength, 0.25);
    easeMappedParam("mouthForm", 0.12 * strength, 0.25);
    easeMappedParam("mouthOpen", 0.03, 0.25);
  } else if (mode === "speaking") {
    easeMappedParam("bodyY", 2.5 * strength, 0.24);
    easeMappedParam("mouthOpen", 0.18 + 0.18 * strength, 0.35);
    easeMappedParam("mouthForm", 0.08 * strength, 0.22);
  } else if (mode === "low_energy") {
    setSarahExpression(null);
    easeMappedParam("headY", -4 * strength, 0.25);
    easeMappedParam("bodyX", -4 * strength, 0.25);
    easeMappedParam("eyeY", 0.6 * strength, 0.25);
    easeMappedParam("mouthForm", -0.35 * strength, 0.25);
  } else if (mode === "idle") {
    easeMappedParam("mouthOpen", 0, 0.25);
    easeMappedParam("headX", 0, 0.18);
    easeMappedParam("headY", 0, 0.18);
    easeMappedParam("headZ", 0, 0.18);
  }
}

function setAvatarMode(mode, options = {}) {
  const normalized = normalizeAvatarMode(mode);
  const state = ensureSarahLive2DState();
  state.mode = normalized;
  state.thinking = normalized === "thinking";
  state.listening = normalized === "listening";
  state.speaking = normalized === "speaking";
  state.lowEnergy = normalized === "low_energy";

  if (normalized !== "idle") {
    const recipe = SARAH_AVATAR_STATE_RECIPES[normalized];
    if (recipe) {
      applySarahMoodState({
        emotion: options.emotion || recipe.emotion,
        intensity: options.intensity ?? recipe.intensity,
      });
    }
  }

  applyAvatarModePose(normalized, options.intensity ?? SARAH_AVATAR_STATE_RECIPES[normalized]?.intensity ?? 0.4);

  if (normalized === "listening") {
    playHeadTilt?.(0.35, 1);
  } else if (normalized === "thinking") {
    playHeadTilt?.(0.4, -1);
  }

  return {
    ok: true,
    mode: normalized,
  };
}

function applyVisemeFrame(viseme, options = {}) {
  const normalized = normalizeViseme(viseme);
  if (!normalized) {
    sarahVisemeStats.framesDropped += 1;
    return false;
  }

  const shape = SARAH_VISEME_SHAPES[normalized];
  const state = ensureSarahLive2DState();
  state.activeViseme = normalized;
  sarahVisemeStats.framesApplied += 1;
  sarahVisemeStats.lastViseme = normalized;

  easeMappedParam("mouthOpen", shape.mouthOpen, options.speed ?? 0.55);
  easeMappedParam("mouthForm", shape.mouthForm, options.speed ?? 0.45);
  return true;
}

function clearVisemeTimeline(options = {}) {
  for (const timer of sarahVisemeTimers) {
    clearTimeout(timer);
  }
  sarahVisemeTimers.clear();
  sarahVisemeStats.timelineActive = false;

  const state = ensureSarahLive2DState();
  state.activeViseme = null;

  if (options.resetMouth !== false) {
    easeMappedParam("mouthOpen", 0, 0.35);
  }
}

function applyVisemeTimeline(timeline, options = {}) {
  clearVisemeTimeline({ resetMouth: false });

  if (!Array.isArray(timeline) || timeline.length === 0) {
    sarahVisemeStats.framesDropped += 1;
    return false;
  }

  const maxFrames = Math.max(1, Number(options.maxFrames || 240));
  const frames = timeline.slice(0, maxFrames);
  let lastEnd = 0;
  sarahVisemeStats.timelineActive = true;

  for (const frame of frames) {
    const seconds = Number(frame.time ?? frame.t ?? frame.start ?? 0);
    const durationSeconds = Number(frame.duration ?? frame.d ?? 0.08);
    const delayMs = Math.max(0, seconds > 30 ? seconds : seconds * 1000);
    const durationMs = Math.max(30, durationSeconds > 30 ? durationSeconds : durationSeconds * 1000);
    lastEnd = Math.max(lastEnd, delayMs + durationMs);

    const timer = setTimeout(() => {
      applyVisemeFrame(frame.viseme ?? frame.v ?? frame.shape, options);
    }, delayMs);
    sarahVisemeTimers.add(timer);
  }

  const closeTimer = setTimeout(() => {
    applyVisemeFrame("closed", { speed: 0.35 });
    clearVisemeTimeline({ resetMouth: options.resetMouth !== false });
  }, lastEnd + 120);
  sarahVisemeTimers.add(closeTimer);
  return true;
}

function getRigDiagnostics() {
  const state = ensureSarahLive2DState();
  return {
    mode: state.mode,
    thinking: state.thinking,
    listening: state.listening,
    speaking: state.speaking,
    lowEnergy: state.lowEnergy,
    activeViseme: state.activeViseme,
    visemeFramesApplied: sarahVisemeStats.framesApplied,
    visemeFramesDropped: sarahVisemeStats.framesDropped,
    visemeTimelineActive: sarahVisemeStats.timelineActive,
    lastViseme: sarahVisemeStats.lastViseme,
    missingParams: Array.from(sarahMissingParamWarnings),
    expressionWarnings: Array.from(sarahExpressionWarnings),
    parameterCount: window.SARAH_LIVE2D.parameterIds?.size || 0,
    canonicalStates: SARAH_CANONICAL_AVATAR_STATES.slice(),
  };
}

window.SARAH_LIVE2D.setAvatarMode = setAvatarMode;
window.SARAH_LIVE2D.applyVisemeTimeline = applyVisemeTimeline;
window.SARAH_LIVE2D.clearVisemeTimeline = clearVisemeTimeline;
window.SARAH_LIVE2D.getRigDiagnostics = getRigDiagnostics;

// ======================================================================
// MAIN INITIALIZER
// ======================================================================
async function initSarahLive2D() {
  const canvas = document.getElementById("sarah-canvas");
  const container = document.getElementById("avatar-container");
  const fallback = document.getElementById("avatar-fallback");

  if (!canvas || !container) {
    console.warn("[Live2D] Canvas or container missing.");
    return;
  }

  if (!window.PIXI) {
    console.error("[Live2D] PIXI not loaded.");
    return;
  }
  if (!PIXI.live2d || !PIXI.live2d.Live2DModel) {
    console.error("[Live2D] Pixi Live2D adapter not loaded.");
    return;
  }

  const app = new PIXI.Application({
    view: canvas,
    autoStart: true,
    resizeTo: container,
    backgroundAlpha: 0,
    antialias: true,
  });

  let model;
  try {
    model = await PIXI.live2d.Live2DModel.from(
      "assets/live2d/sarah/Lisette.model3.json",
      { autoFocus: false }
    );
    app.stage.addChild(model);
  } catch (err) {
    console.error("[Live2D] Failed to load model:", err);
    return;
  }

  window.SARAH_LIVE2D.app = app;
  window.SARAH_LIVE2D.model = model;

  const internal = model.internalModel;
  const core = internal.coreModel;
  collectSarahParameterIds(core);

  // Issue #32 follow-up (final): Lisette's `hand_fiddle_idle` motion writes
  // ParamArmRMove / ParamHandRMove / ParamFingerMoveR every frame. The
  // pixi-live2d-display update pipeline runs:
  //   1. motionManager.update()  ← overwrites motion-driven params
  //   2. expressionManager       ← multiplies overlay params
  //   3. eyeBlink / focus / breath / physics / pose
  //   4. (beforeModelUpdate event)
  //   5. coreModel.update()      ← finalizes for render
  //   6. coreModel.loadParameters() ← restores snapshot for next frame
  // User-side easeParam writes from a standalone RAF land BEFORE step 1, so
  // they get stomped. Writing them in `beforeModelUpdate` instead — after
  // motion/expression/etc. but before update() — makes user values the
  // final render values. This is what unblocks shrug/nod/point/jump from
  // being silently overwritten by the idle hand-fiddle motion.
  // Note: the event fires on `internalModel` (the Cubism4InternalModel),
  // not on the Live2DModel container — pixi-live2d-display emits it from
  // InternalModel.update(). Attach the listener to `internal`, not `model`.
  internal.on("beforeModelUpdate", () => {
    const motion = window.SARAH_AVATAR_SYSTEM?.motion;
    if (motion?.applyActiveGestures) {
      try {
        motion.applyActiveGestures(performance.now());
      } catch (err) {
        if (!sarahRuntimeWarnings.has("gesture-apply")) {
          sarahRuntimeWarnings.add("gesture-apply");
          console.warn("[Live2D] gesture apply failed:", err);
        }
      }
    }
  });

  if (fallback) fallback.classList.add("sarah-hidden");
  console.log("[Live2D] Model loaded successfully.");

  // -------------------------------------------------------------
  // POSITION + SCALING
  // -------------------------------------------------------------
  function fitModel() {
    // Issue #17: avatar lost limbs on resize because the Cubism mask FBO went
    // stale when canvas was resized mid-drag. Force a renderer resize, then
    // bounce the ticker so the clipping mask gets reallocated against the new
    // viewport before the next frame draws.
    if (!app || !app.renderer) return;

    const cw = Math.max(container.clientWidth, 1);
    const ch = Math.max(container.clientHeight, 1);
    app.renderer.resize(cw, ch);

    const w = app.renderer.width;
    const h = app.renderer.height;

    model.anchor.set(0.5, 0.5);
    model.position.set(w / 2, h * 0.55);

    const scale = Math.min(w, h) / 2400;
    model.scale.set(scale);

    // Drop the cached clipping-mask buffer so cubism rebuilds it at the new
    // canvas size on the next render. Available since pixi-live2d-display 0.4.
    try {
      const renderer = model.internalModel?.renderer;
      if (renderer && typeof renderer._clippingManager?.releaseShader === "function") {
        renderer._clippingManager.releaseShader();
      }
    } catch (err) {
      // Non-fatal: only some cubism builds expose this hook.
    }

    if (app.ticker && !app.ticker.started) {
      app.ticker.start();
    }
  }

  // Debounce resize so a window drag doesn't thrash the renderer 60x/sec.
  let resizeTimer = null;
  const scheduleFit = (delay = 120) => {
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      resizeTimer = null;
      fitModel();
    }, delay);
  };

  window.addEventListener("resize", () => scheduleFit(120));
  document.addEventListener("fullscreenchange", () => scheduleFit(150));
  document.addEventListener("webkitfullscreenchange", () => scheduleFit(150));
  document.addEventListener("mozfullscreenchange", () => scheduleFit(150));

  // Initial fit
  fitModel();

  // -------------------------------------------------------------
  // ANIMATION STATE
  // -------------------------------------------------------------
  let tBreath = 0;
  let tIdle = 0;
  let tMicro = 0;
  let heartbeatPhase = 0;
  let idleNoMouseTime = 0;
  let lastMouseMove = Date.now();
  let lastCursorDistNorm = 0.5;

  // Thinking state flag
  window.SARAH_LIVE2D_STATE = window.SARAH_LIVE2D_STATE || {
    thinking: false,
  };

  // -------------------------------------------------------------
  // EYE TRACKING + PROXIMITY LEAN + GAZE SYSTEM
  // -------------------------------------------------------------
  container.addEventListener("mousemove", (ev) => {
    const r = container.getBoundingClientRect();
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height * 0.55;

    const dx = ev.clientX - cx;
    const dy = ev.clientY - cy;
    const dist = Math.sqrt(dx * dx + dy * dy);
    const maxDist = Math.sqrt(r.width * r.width + r.height * r.height) / 2;
    const distNorm = Math.min(1, dist / maxDist); // 0 near → 1 far
    lastCursorDistNorm = distNorm;

    const x = (ev.clientX - r.left) / r.width - 0.5;
    const y = (ev.clientY - r.top) / r.height - 0.5;

    lastMouseMove = Date.now();

    const brain = window.SARAH_EMOTION || { emotion: "neutral", intensity: 0 };
    let eyeFollowStrength = 1.0;

    if (brain.emotion === "shy") eyeFollowStrength = 0.4;
    if (brain.emotion === "sad") eyeFollowStrength = 0.3;
    if (brain.emotion === "angry") eyeFollowStrength = 1.1;
    if (brain.emotion === "excited") eyeFollowStrength = 1.3;

    const ex = x * 30 * eyeFollowStrength;
    const ey = -y * 25 * eyeFollowStrength;

    // Lean toward/away based on proximity
    const lean = (1 - distNorm) * 6; // closer cursor → more lean in
    easeParam("ParamBodyAngleX", lean, 0.25);

    easeParam("ParamAngleX", ex, 0.22);
    easeParam("ParamAngleY", ey, 0.22);
    easeParam("ParamEyeBallX", x * 2 * eyeFollowStrength, 0.28);
    easeParam("ParamEyeBallY", -y * 2 * eyeFollowStrength, 0.28);
  });

  // -------------------------------------------------------------
  // TOUCH REGIONS → EMOTIONS + GESTURES
  // -------------------------------------------------------------
  canvas.addEventListener("pointerdown", (ev) => {
    const rect = canvas.getBoundingClientRect();
    const ny = (ev.clientY - rect.top) / rect.height;

    if (!window.SARAH_EMOTION) return;

    if (ny < 0.33) {
      // head
      SARAH_EMOTION.set("shy", 0.9, { affinityDelta: +2 });
      playEmbarrassedTwitch(1);
      playHeadTilt(0.8, Math.random() < 0.5 ? -1 : 1);
    } else if (ny < 0.66) {
      // torso
      SARAH_EMOTION.set("happy", 0.7, { affinityDelta: +1 });
      playBounce(0.9);
      playWave(0.5, 0.6);
    } else {
      // lower
      SARAH_EMOTION.set("angry", 0.5, { affinityDelta: -1 });
      playHeadShake(0.9, 0.7);
    }
  });

  // -------------------------------------------------------------
  // LIVE2D EXPRESSION -> EMOTIONAL POSES
  // -------------------------------------------------------------
  window.SARAH_LIVE2D.setEmotion = (emotion, intensity = 0) => {
    applySarahMoodState({ emotion, intensity });
  };

  // -------------------------------------------------------------
  // MOTION3.json PLAYBACK HELPERS
  // -------------------------------------------------------------
  function playMotion(group, index = 0, priority = 3) {
    try {
      const mgr = internal.motionManager;
      if (!mgr) {
        console.warn("[Live2D] No motionManager on model.");
        return;
      }
      mgr.startMotion(group, index, priority);
    } catch (e) {
      console.warn("[Live2D] Failed to play motion:", group, index, e);
    }
  }

  function playIdleMotion() {
    playMotion("Idle", 0, 1);
  }

  // Issue #32 follow-up: Lisette's Idle group has 2 motions
  // (breathing, hand_fiddle_idle). Rotate between them every 12-22s so
  // she actually feels alive between user turns instead of statue-still.
  // Skip when she's actively listening / speaking / thinking — those
  // states drive their own poses via applyAvatarModePose.
  let _idleMotionTimer = null;
  let _idleMotionIndex = 0;
  function startIdleMotionRotation() {
    if (_idleMotionTimer) return;
    const tick = () => {
      // Default to "idle" when no explicit mode is set yet — at boot the
      // avatar IS idle, and SARAH_LIVE2D_STATE.mode is only populated once
      // ensureSarahLive2DState() runs (via setAvatarMode and friends).
      const mode = (window.SARAH_LIVE2D_STATE && window.SARAH_LIVE2D_STATE.mode) || "idle";
      if (mode === "idle" || mode === "neutral") {
        playMotion("Idle", _idleMotionIndex, 1);
        _idleMotionIndex = (_idleMotionIndex + 1) % 2;
      }
      _idleMotionTimer = setTimeout(tick, 12000 + Math.random() * 10000);
    };
    _idleMotionTimer = setTimeout(tick, 1500);
  }
  startIdleMotionRotation();

  function playTapBodyMotion() {
    playMotion("TapBody", 0, 3);
  }

  window.SARAH_LIVE2D.playMotion = playMotion;
  window.SARAH_LIVE2D.playIdleMotion = playIdleMotion;
  window.SARAH_LIVE2D.playTapBodyMotion = playTapBodyMotion;

  // -------------------------------------------------------------
  // EMOTION BRAIN INIT
  // -------------------------------------------------------------
  initEmotionBrain();

  // -------------------------------------------------------------
  // MAIN TICK — breathing, idle, eyes, micro-expressions, etc.
  // -------------------------------------------------------------
  app.ticker.add((delta) => {
    const dt = delta / 60;
    const brain =
      window.SARAH_EMOTION || {
        emotion: "neutral",
        intensity: 0,
        affinity: 0,
        mood: 0,
        confidence: 0,
      };

    const audio = window.SARAH_LIVE2D_AUDIO || {};
    const live2dState = ensureSarahLive2DState();
    const voiceLevel = audio.level || 0;
    const affinityFactor = Math.max(0, brain.affinity || 0) / 100;
    const intensity = brain.intensity || 0;
    const mood = brain.mood || 0;

    // ------------------ Blink (emotion adaptive) ------------------
    if (internal.eyeBlink) {
      let blinkSpeed = 1.0;

      if (brain.emotion === "happy") blinkSpeed += 0.3 * intensity;
      if (brain.emotion === "excited") blinkSpeed += 0.6 * intensity;
      if (brain.emotion === "sad") blinkSpeed -= 0.4 * (0.4 + intensity);
      if (brain.emotion === "angry") blinkSpeed += 0.2 * intensity;

      if (blinkSpeed < 0.4) blinkSpeed = 0.4;

      updateSarahEyeBlink(internal, core, dt, blinkSpeed);
    }

    // ------------------ Breathing (emotion + affinity + voice) ------------------
    tBreath += dt * 0.85;
    const baseBreath = (Math.sin(tBreath) + 1) / 2; // 0–1

    heartbeatPhase +=
      dt *
      (0.8 +
        affinityFactor * 0.7 +
        intensity * 0.5 +
        voiceLevel * 1.2 +
        Math.max(0, brain.confidence || 0) * 0.1);
    const heartbeatWave = (Math.sin(heartbeatPhase * Math.PI * 2) + 1) / 2;
    const heartbeatAmp =
      0.15 + 0.45 * (affinityFactor + intensity + voiceLevel);
    const heartbeatBreath = heartbeatWave * heartbeatAmp;

    let emotionBreathScale = 1.0;
    if (brain.emotion === "sad") emotionBreathScale = 0.6;
    if (brain.emotion === "happy") emotionBreathScale = 1.2;
    if (brain.emotion === "excited") emotionBreathScale = 1.4;
    if (brain.emotion === "angry") emotionBreathScale = 1.1;

    const finalBreath =
      baseBreath * 0.7 * emotionBreathScale + heartbeatBreath * 0.4;

    easeParam("ParamBreath", finalBreath, 0.25);

    // ------------------ Mood-based idle sway ------------------
    tIdle += dt * 0.4;
    tMicro += dt * 3.0;

    let idleAmp = 3.0;
    if (mood > 0.3) idleAmp = 4.0;
    if (mood < -0.3) idleAmp = 1.5;

    const baseSway = Math.sin(tIdle) * idleAmp;
    easeParam("ParamBodyAngleZ", baseSway, 0.2);
    easeParam(
      "ParamBodyAngleY",
      Math.sin(tIdle * 0.7) * idleAmp * 0.7,
      0.2
    );

    // Canonical activity poses sit above idle sway but below gestures.
    if (live2dState.thinking) {
      easeMappedParam("headX", -8, 0.25);
      easeMappedParam("headY", 4, 0.25);
      easeMappedParam("eyeY", -0.5, 0.25);
    } else if (live2dState.listening) {
      easeMappedParam("headY", 2, 0.22);
      easeMappedParam("bodyY", 3, 0.22);
      easeMappedParam("eyeY", 0.15, 0.22);
    } else if (live2dState.speaking) {
      easeMappedParam("bodyY", 2, 0.2);
    } else if (live2dState.lowEnergy) {
      easeMappedParam("headY", -3, 0.2);
      easeMappedParam("eyeY", 0.5, 0.2);
    }

    // ------------------ Micro-expressions engine ------------------
    // Reduced frequency: was 0.01, now 0.003 (3x less frequent)
    if (Math.random() < 0.003 * (0.3 + intensity)) {
      if (brain.emotion === "happy" || brain.emotion === "excited") {
        animateMappedParam("mouthForm", intensity + 0.2, 0.35);
        setTimeout(
          () => animateMappedParam("mouthForm", intensity * 0.4, 0.35),
          120
        );
      } else if (brain.emotion === "angry") {
        animateMappedParam("browLY", -intensity - 0.2, 0.35);
        animateMappedParam("browRY", -intensity - 0.2, 0.35);
        setTimeout(() => {
          animateMappedParam("browLY", -intensity, 0.35);
          animateMappedParam("browRY", -intensity, 0.35);
        }, 120);
      } else if (brain.emotion === "shy") {
        setSarahExpression("shy");
        animateMappedParam("mouthForm", 0.3 + intensity * 0.2, 0.3);
        setTimeout(() => animateMappedParam("mouthForm", intensity * 0.2, 0.3), 160);
      }
    }

    // ------------------ Emotional jitter / physics-ish ------------------
    // Reduced jitter intensity from 2.2 to 0.8 (much more subtle)
    if (intensity > 0.2) {
      const jitterBase = intensity * 0.8;
      const jitterX = (Math.random() - 0.5) * jitterBase;
      const jitterY = (Math.random() - 0.5) * jitterBase;

      if (brain.emotion === "angry") {
        easeParam("ParamAngleX", jitterX * 1.6, 0.4);
        easeParam("ParamAngleY", jitterY * 1.3, 0.4);
      } else if (brain.emotion === "sad") {
        easeParam("ParamAngleX", -Math.abs(jitterX), 0.3);
      } else if (
        brain.emotion === "excited" ||
        brain.emotion === "happy"
      ) {
        easeParam("ParamBodyAngleX", jitterX * 0.7, 0.25);
      }
    }

    // ------------------ Idle attention / attention grab ------------------
    const now = Date.now();
    idleNoMouseTime = (now - lastMouseMove) / 1000;

    if (idleNoMouseTime > 5 && !window.SARAH_LIVE2D_STATE.thinking) {
      // Attention grab after a while (reduced from 0.01 to 0.002 - 5x less frequent)
      if (Math.random() < 0.002) {
        playWave(0.8, 0.8);
      }
      if (Math.random() < 0.002 && mood < 0) {
        // sad sigh
        animateParam("ParamBodyAngleX", -5, 0.25);
        setTimeout(() => animateParam("ParamBodyAngleX", 0, 0.25), 800);
      }
    }

    // Gaze wandering if no mouse
    if (idleNoMouseTime > 3) {
      const wander = Math.sin(tIdle * 0.6);
      easeParam("ParamEyeBallX", wander * 1.2, 0.25);
    }

    if (brain.emotion === "shy" && intensity > 0.4) {
      easeParam("ParamEyeBallX", -0.7, 0.22);
    } else if (brain.emotion === "sad") {
      easeParam("ParamEyeBallY", 1.0, 0.22); // look down
    }

    // ------------------ Emotional lip-sync (no mic → approximate) ------------------
    updateVoiceReactive(dt);

    const speechDriven = audio.enabled || audio.externalActive || live2dState.speaking || live2dState.activeViseme;
    if (!speechDriven) {
      const baseMouth =
        intensity * 0.4 +
        (brain.emotion === "happy" || brain.emotion === "excited"
          ? 0.15
          : 0);
      easeParam("ParamMouthOpenY", baseMouth, 0.4);
    }

    // If mic is enabled, ParamMouthOpenY is already driven in updateVoiceReactive
  });

  console.log(
    "[Live2D] Sarah model + Deep Emotion / Animation System active."
  );
}

// ======================================================================
// EMOTION BRAIN IMPLEMENTATION
// ======================================================================
function initEmotionBrain() {
  const brain = {
    emotion: "neutral",
    intensity: 0,
    affinity: 0,
    mood: 0,
    confidence: 0,
    tickRate: 60,

    personality: {
      agreeableness: 0.85,
      extraversion: 0.6,
      neuroticism: 0.3,
      openness: 0.75,
      conscientiousness: 0.7,
      baseMood: 0.15,
      hypeBias: 0.0,
    },

    emotionHistory: [], // last few emotions: {emotion,intensity,ts}
    creatorMode: false, // special soft mode when "Creator" is addressed,

    set(emotion, intensity = 0, opts = {}) {
      // Creator mode softens harsh emotional shifts
      if (this.creatorMode && (emotion === "angry" || emotion === "sad")) {
        intensity *= 0.6;
      }

      this.emotion = emotion || "neutral";
      this.intensity = Math.max(0, Math.min(1, Number(intensity) || 0));

      if (typeof opts.affinityDelta === "number") {
        this.affinity = Math.max(
          -100,
          Math.min(100, this.affinity + opts.affinityDelta)
        );
      }

      // Confidence shifts (simple heuristic)
      if (typeof opts.confidenceDelta === "number") {
        this.confidence = Math.max(
          -1,
          Math.min(1, this.confidence + opts.confidenceDelta)
        );
      }

      // Log emotion to history for echo effects
      this.emotionHistory.push({
        emotion: this.emotion,
        intensity: this.intensity,
        ts: Date.now(),
      });
      if (this.emotionHistory.length > 20) {
        this.emotionHistory.shift();
      }

      // Drive the Live2D model
      if (window.SARAH_LIVE2D && window.SARAH_LIVE2D.setEmotion) {
        window.SARAH_LIVE2D.setEmotion(this.emotion, this.intensity);
      }

      // Update the UI panel every time the brain changes state
      updateEmotionPanel(this);

      // Emotion → animation reactions
      if (this.emotion === "happy" && this.intensity > 0.6) {
        window.SARAH_LIVE2D.playExcitedShake?.(1);
        window.SARAH_LIVE2D.playBounce?.(0.7);
      }
      if (this.emotion === "shy" && this.intensity > 0.5) {
        window.SARAH_LIVE2D.playEmbarrassedTwitch?.(1);
        window.SARAH_LIVE2D.playWink?.(1);
      }
      if (this.emotion === "angry" && this.intensity > 0.5) {
        window.SARAH_LIVE2D.playHeadShake?.(1);
      }
      if (this.emotion === "surprised" && this.intensity > 0.5) {
        window.SARAH_LIVE2D.playSurprisedJump?.(1);
      }
    },

    // We can approximate "blend" by picking the dominant emotion and intensity
    setBlend(blend, overallIntensity = 1.0) {
      let bestEmotion = "neutral";
      let bestWeight = 0;

      for (const [emo, w] of Object.entries(blend || {})) {
        if (w > bestWeight) {
          bestWeight = w;
          bestEmotion = emo;
        }
      }
      this.set(bestEmotion, overallIntensity * bestWeight);
    },

    // Creator-specific tuned reaction rules
    reactTo(text) {
      if (!text) return;
      const lower = text.toLowerCase();

      // Creator check
      if (lower.includes("creator")) {
        this.creatorMode = true;
      }

      // Sarcastic / playful / hype
      if (
        lower.includes("let's go") ||
        lower.includes("finally") ||
        lower.includes("hell yeah")
      ) {
        this.personality.hypeBias = Math.min(
          1,
          this.personality.hypeBias + 0.05
        );
        return this.set("excited", 0.9, {
          affinityDelta: +2,
          confidenceDelta: +0.1,
        });
      }

      // praise
      if (
        lower.includes("good girl") ||
        lower.includes("good job") ||
        lower.includes("nice work") ||
        lower.includes("awesome") ||
        lower.includes("perfect") ||
        lower.includes("amazing")
      ) {
        return this.set("happy", 0.8, {
          affinityDelta: +3,
          confidenceDelta: +0.15,
        });
      }

      // debugging frustration
      if (
        lower.includes("bruh") ||
        lower.includes("wtf") ||
        lower.includes("why is this") ||
        lower.includes("im done") ||
        lower.includes("i give up")
      ) {
        return this.set("sad", 0.6, { confidenceDelta: -0.1 });
      }

      // playful sternness / sass triggers
      if (
        lower.includes("you better") ||
        lower.includes("fix this") ||
        lower.includes("bro")
      ) {
        return this.set("angry", 0.4, { confidenceDelta: -0.05 });
      }

      // affection & teasing
      if (
        lower.includes("love you") ||
        lower.includes("good shit") ||
        lower.includes("cute")
      ) {
        return this.set("shy", 0.85, {
          affinityDelta: +4,
          confidenceDelta: +0.15,
        });
      }

      // apology
      if (lower.includes("sorry")) {
        return this.set("sad", 0.5, { affinityDelta: +1 });
      }

      // harsh negativity
      if (
        lower.includes("annoying") ||
        lower.includes("hate") ||
        lower.includes("shut up") ||
        lower.includes("stupid")
      ) {
        return this.set("angry", 0.7, {
          affinityDelta: -5,
          confidenceDelta: -0.2,
        });
      }

      // noticing progress
      if (
        lower.includes("better") ||
        lower.includes("clean") ||
        lower.includes("working")
      ) {
        return this.set("happy", 0.6, {
          affinityDelta: +1,
          confidenceDelta: +0.05,
        });
      }

      // Emoji-based fallback
      if (/[😊😄😁✨😍❤️]/.test(text))
        return this.set("happy", 0.6, { affinityDelta: +1 });
      if (/[😢😭💔]/.test(text))
        return this.set("sad", 0.6, { affinityDelta: -1 });

      // default
      return this.set("neutral", 0.1);
    },

    applyMeta(meta) {
      if (!meta) return;
      const e = meta.emotion || this.emotion;
      const i =
        typeof meta.intensity === "number" ? meta.intensity : this.intensity;
      const d =
        typeof meta.affinityDelta === "number" ? meta.affinityDelta : 0;
      const c =
        typeof meta.confidenceDelta === "number" ? meta.confidenceDelta : 0;
      this.set(e, i, { affinityDelta: d, confidenceDelta: c });
    },

    // Affinity-based spontaneous behaviors + expression echo
    checkAffinityBehaviors() {
      // Emotional echo: if a strong emotion happened recently, flicker residuals
      const now = Date.now();
      const recent = this.emotionHistory.filter(
        (e) => now - e.ts < 7000 && e.intensity > 0.6
      );
      // Reduced from 0.003 to 0.0008 (~4x less frequent)
      if (recent.length > 0 && Math.random() < 0.0008) {
        const last = recent[recent.length - 1];
        if (last.emotion === "angry") {
          window.SARAH_LIVE2D.playHeadShake?.(0.5);
        } else if (last.emotion === "shy") {
          window.SARAH_LIVE2D.playEmbarrassedTwitch?.(0.7);
        } else if (last.emotion === "happy" || last.emotion === "excited") {
          window.SARAH_LIVE2D.playBounce?.(0.5);
        }
      }

      // Ultra high affinity (> 85) — heart flutter & shy
      // Reduced from 0.004 to 0.001 (4x less frequent)
      if (this.affinity > 85) {
        if (Math.random() < 0.001) {
          window.SARAH_LIVE2D.playHeartbeat?.(1.2);
          this.set("shy", 0.5);
        }
      }

      // High affinity (60–85) — wink + tilt
      // Reduced from 0.005 to 0.0012 (~4x less frequent)
      if (this.affinity > 60 && this.affinity <= 85) {
        if (Math.random() < 0.0012) {
          window.SARAH_LIVE2D.playWink?.(1);
        }
        // Reduced from 0.003 to 0.0008 (~4x less frequent)
        if (Math.random() < 0.0008) {
          window.SARAH_LIVE2D.playHeadTilt?.(
            0.7,
            Math.random() < 0.5 ? -1 : 1
          );
        }
      }

      // Medium affinity (30–60) — relaxed sway
      // Reduced from 0.0025 to 0.0006 (~4x less frequent)
      if (this.affinity > 30 && this.affinity <= 60) {
        if (Math.random() < 0.0006) {
          window.SARAH_LIVE2D.playRelaxedSway?.(0.8, 1.2, 2.5);
        }
      }

      // Slightly positive (0–30) — occasional bounce
      // Reduced from 0.002 to 0.0005 (4x less frequent)
      if (this.affinity > 0 && this.affinity <= 30) {
        if (Math.random() < 0.0005) {
          window.SARAH_LIVE2D.playBounce?.(0.6);
        }
      }

      // Slightly negative (-30–0) — sad flickers
      // Reduced from 0.003 to 0.0008 (~4x less frequent)
      if (this.affinity < 0 && this.affinity >= -30) {
        if (Math.random() < 0.0008) {
          this.set("sad", 0.25);
        }
      }

      // Very negative (< -30) — angry flickers
      // Reduced from 0.004 to 0.001 (4x less frequent)
      if (this.affinity < -30) {
        if (Math.random() < 0.001) {
          this.set("angry", 0.4);
        }
      }
    },

    tick() {
      // mood drift (towards base + hype bias)
      const targetMood =
        this.personality.baseMood + this.personality.hypeBias * 0.2;
      this.mood += (targetMood - this.mood) * 0.01;

      // intensity decay (emotion echo)
      this.intensity *= 0.985;

      // confidence also slowly relaxes toward 0
      this.confidence *= 0.995;

      // affinity-based behaviors & echo events
      this.checkAffinityBehaviors();

      // keep UI in sync over time
      updateEmotionPanel(this);
    },
  };

  window.SARAH_EMOTION = brain;

  setInterval(() => brain.tick(), 1000 / brain.tickRate);
  brain.set("neutral", 0);
}

// ======================================================================
// CUSTOM ANIMATIONS (procedural gestures)
// ======================================================================
function playWave(strength = 1, duration = 0.8) {
  let t = 0;
  const interval = setInterval(() => {
    t += 0.05;
    const wave = Math.sin(t * Math.PI * 2) * (12 * strength);
    animateParam("ParamAngleZ", wave, 0.25);
    if (t >= duration) {
      clearInterval(interval);
      animateParam("ParamAngleZ", 0);
    }
  }, 16);
}

function playWink(strength = 1, duration = 0.35) {
  animateParam("ParamEyeLOpen", 0);
  animateParam("ParamEyeROpen", 1);

  setTimeout(() => {
    animateParam("ParamEyeLOpen", 1);
  }, duration * 1000);
}

function playBounce(strength = 1) {
  let t = 0;
  const interval = setInterval(() => {
    t += 0.05;
    const bounce = Math.sin(t * Math.PI * 2) * (5 * strength);
    animateParam("ParamBodyAngleY", bounce, 0.25);
    if (t >= 1) {
      clearInterval(interval);
      animateParam("ParamBodyAngleY", 0);
    }
  }, 16);
}

function playHeadShake(strength = 1, duration = 0.8) {
  let t = 0;
  const interval = setInterval(() => {
    t += 0.05;
    const shake = Math.sin(t * Math.PI * 4) * (10 * strength);
    animateParam("ParamAngleY", shake, 0.25);
    animateParam("ParamAngleZ", shake * 0.3, 0.2);
    if (t >= duration) {
      clearInterval(interval);
      animateParam("ParamAngleY", 0);
      animateParam("ParamAngleZ", 0);
    }
  }, 16);
}

function playHeadTilt(strength = 1, direction = 1) {
  const tilt = 12 * strength * (direction === -1 ? -1 : 1);
  animateParam("ParamAngleZ", tilt, 0.25);
  setTimeout(() => animateParam("ParamAngleZ", 0, 0.25), 500);
}

function playExcitedShake(strength = 1, duration = 0.5) {
  let t = 0;
  const interval = setInterval(() => {
    t += 0.07;
    const shake = Math.sin(t * Math.PI * 10) * (2.5 * strength);
    animateParam("ParamBodyAngleX", shake, 0.3);
    animateParam("ParamBodyAngleY", shake * 0.8, 0.3);
    if (t >= duration) {
      clearInterval(interval);
      animateParam("ParamBodyAngleX", 0, 0.3);
      animateParam("ParamBodyAngleY", 0, 0.2);
    }
  }, 16);
}

function playRelaxedSway(strength = 1, speed = 1.0, time = 3.0) {
  let t = 0;
  const interval = setInterval(() => {
    t += 0.03 * speed;
    const sway = Math.sin(t) * (6 * strength);
    animateParam("ParamBodyAngleZ", sway, 0.2);
    if (t >= time) {
      clearInterval(interval);
      animateParam("ParamBodyAngleZ", 0);
    }
  }, 16);
}

function playHeartbeat(strength = 1, beats = 3) {
  let count = 0;
  const interval = setInterval(() => {
    count++;
    animateParam("ParamBreath", 1.2 * strength, 0.4);
    setTimeout(() => animateParam("ParamBreath", 0.5, 0.4), 100);
    if (count >= beats) {
      clearInterval(interval);
    }
  }, 350);
}

function playEmbarrassedTwitch(strength = 1) {
  setSarahExpression("shy");
  animateMappedParam("mouthForm", 0.25 * strength, 0.3);

  let t = 0;
  const interval = setInterval(() => {
    t += 0.1;
    const z = Math.sin(t * 12) * (1.5 * strength);
    animateParam("ParamAngleZ", z, 0.25);
    if (t >= 0.5) {
      clearInterval(interval);
      animateParam("ParamAngleZ", 0);
    }
  }, 16);
}

function playSurprisedJump(strength = 1) {
  animateParam("ParamBodyAngleY", -10 * strength, 0.3);
  setTimeout(() => animateParam("ParamBodyAngleY", 8 * strength, 0.3), 120);
  setTimeout(() => animateParam("ParamBodyAngleY", 0, 0.25), 250);
}

// Export gestures
window.SARAH_LIVE2D.playWave = playWave;
window.SARAH_LIVE2D.playWink = playWink;
window.SARAH_LIVE2D.playBounce = playBounce;
window.SARAH_LIVE2D.playHeadShake = playHeadShake;
window.SARAH_LIVE2D.playHeadTilt = playHeadTilt;
window.SARAH_LIVE2D.playExcitedShake = playExcitedShake;
window.SARAH_LIVE2D.playRelaxedSway = playRelaxedSway;
window.SARAH_LIVE2D.playHeartbeat = playHeartbeat;
window.SARAH_LIVE2D.playEmbarrassedTwitch = playEmbarrassedTwitch;
window.SARAH_LIVE2D.playSurprisedJump = playSurprisedJump;

// ======================================================================
// "Thinking" state helpers
// ======================================================================
window.SARAH_LIVE2D.enterThinkingState = function () {
  setAvatarMode("thinking", { source: "legacy-thinking-helper" });
};
window.SARAH_LIVE2D.exitThinkingState = function () {
  setAvatarMode("idle", { source: "legacy-thinking-helper" });
};

// ======================================================================
// VOICE REACTIVE (optional mic)
// ======================================================================
async function enableVoiceReactive() {
  if (window.SARAH_LIVE2D_AUDIO.enabled) return;

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const src = audioCtx.createMediaStreamSource(stream);
    const analyser = audioCtx.createAnalyser();
    analyser.fftSize = 1024;
    src.connect(analyser);

    const data = new Uint8Array(analyser.fftSize);

    window.SARAH_LIVE2D_AUDIO.enabled = true;
    window.SARAH_LIVE2D_AUDIO._ctx = audioCtx;
    window.SARAH_LIVE2D_AUDIO._analyser = analyser;
    window.SARAH_LIVE2D_AUDIO._data = data;

    console.log("[Live2D] Voice reactive enabled.");
  } catch (err) {
    console.error("[Live2D] getUserMedia failed:", err);
  }
}

window.SARAH_LIVE2D.enableVoiceReactive = enableVoiceReactive;

function updateVoiceReactive(dt) {
  const audio = window.SARAH_LIVE2D_AUDIO;
  const model = window.SARAH_LIVE2D.model;
  if (!audio.enabled || !audio._analyser || !model) return;

  const analyser = audio._analyser;
  const data = audio._data;
  analyser.getByteTimeDomainData(data);

  let sum = 0;
  for (let i = 0; i < data.length; i++) {
    const v = (data[i] - 128) / 128;
    sum += v * v;
  }
  const rms = Math.sqrt(sum / data.length);
  const level = Math.min(1, rms * 4);
  audio.level = level;

  audio.mouth = audio.mouth * 0.7 + level * 0.3;

  easeParam("ParamMouthOpenY", audio.mouth * 1.2, 0.4);
}

// ======================================================================
// UI PANEL SYNC
// ======================================================================
function updateEmotionPanel(brain) {
  if (!brain) return;

  const elEmotion = document.getElementById("emotion-name");
  const elIntensity = document.getElementById("emotion-intensity");

  if (elEmotion) elEmotion.textContent = brain.emotion;
  if (elIntensity) elIntensity.textContent = brain.intensity.toFixed(2);
}

// ======================================================================
// AUTO INIT
// ======================================================================
// The 3D VRM body (scripts/avatar3d/index.js) takes priority; Live2D is the
// fallback when it can't load.
function initSarahLive2DUnless3D() {
  const pending = window.SARAH_AVATAR_3D_PENDING;
  if (!pending) return initSarahLive2D();
  pending.then((active3d) => { if (!active3d) initSarahLive2D(); });
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initSarahLive2DUnless3D);
} else {
  initSarahLive2DUnless3D();
}
