const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const renderer = path.join(ROOT, "renderer");
const sarah = path.join(renderer, "assets", "live2d", "sarah");

function read(rel) {
  return fs.readFileSync(path.join(ROOT, rel), "utf8");
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

// Issue #32: the avatar rig was swapped from ganyu to Lisette.
const model = readJson(path.join(sarah, "Lisette.model3.json"));
const cdi = readJson(path.join(sarah, "Lisette.cdi3.json"));
const scriptFiles = [
  "renderer/scripts/live2d-sarah.js",
  "renderer/scripts/live2d/avatar-config.js",
  "renderer/scripts/live2d/avatar-emotion-engine.js",
  "renderer/scripts/live2d/avatar-lipsync.js",
  "renderer/scripts/live2d/avatar-motion-controller.js",
  "renderer/scripts/live2d/avatar-ai-sync.js",
  "renderer/scripts/live2d/avatar-system.js",
];
const script = scriptFiles.map(read).join("\n");
const dashboard = read("renderer/dashboard.js");

const modelParams = new Set((cdi.Parameters || []).map((param) => param.Id));
const scriptParams = new Set(
  [...script.matchAll(/["'](Param[A-Za-z0-9_]+)["']/g)].map((match) => match[1])
);

const missingParams = [...scriptParams].filter((param) => !modelParams.has(param));
assert(
  missingParams.length === 0,
  `live2d-sarah.js references Cubism params missing from CDI: ${missingParams.join(", ")}`
);

// Ganyu-era params the Lisette migration (#32) dropped — these don't exist on
// Lisette, so any lingering reference would silently no-op. Guard against
// regressions reintroducing them. (ParamCheek is intentionally NOT here — it
// exists on Lisette and drives the blush gesture.)
const removedParams = [
  "Param40", "Param41", "Param42", "Param43", "Param46",
  "Param_Angle_Rotation19", "Param_Angle_Rotation20", "Param_Angle_Rotation21",
  "Param_Angle_Rotation22", "Param_Angle_Rotation23", "Param_Angle_Rotation24",
  "Param_Angle_Rotation25", "Param_Angle_Rotation26",
];
for (const param of removedParams) {
  assert(!script.includes(`"${param}"`) && !script.includes(`'${param}'`),
    `removed ganyu Cubism param is still referenced: ${param}`);
}

const groups = new Map((model.Groups || []).map((group) => [group.Name, group.Ids || []]));
assert(
  (groups.get("EyeBlink") || []).includes("ParamEyeLOpen")
    && (groups.get("EyeBlink") || []).includes("ParamEyeROpen"),
  "model3 EyeBlink group must include both eye-open params"
);
assert(
  (groups.get("LipSync") || []).includes("ParamMouthOpenY"),
  "model3 LipSync group must include ParamMouthOpenY"
);

const expressionNames = new Set(
  ((model.FileReferences || {}).Expressions || []).map((expr) => expr.Name)
);
const expectedExpressions = [
  "angry", "sad", "shy", "frenzy", "tear",
  "tongue_out", "dark_mask", "sans_eye_glow", "walking_toggle",
];
for (const expression of expectedExpressions) {
  assert(expressionNames.has(expression), `model3 missing expected expression ${expression}`);
}

const motions = (model.FileReferences || {}).Motions || {};
const expectedMotionGroups = ["Idle", "Hello", "Happy", "Sad", "Angry", "Shy", "Jump", "Run"];
for (const group of expectedMotionGroups) {
  assert(
    Array.isArray(motions[group]) && motions[group].length > 0,
    `model3 missing motion group: ${group}`
  );
}

const canonicalStates = [
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
];
for (const state of canonicalStates) {
  assert(script.includes(`${state}:`), `Live2D emotion preset missing state: ${state}`);
}

for (const api of ["applyVisemeTimeline", "setAvatarMode", "getRigDiagnostics", "clearVisemeTimeline"]) {
  assert(script.includes(api), `Live2D runtime missing public API: ${api}`);
}

for (const api of ["SarahAvatarSystem", "SarahAvatarEmotionEngine", "SarahAvatarMotionController", "SARAH_AVATAR_LIPSYNC"]) {
  assert(script.includes(api), `Modular Live2D system missing public API: ${api}`);
}

const requiredAvatarEmotions = [
  "neutral",
  "happy",
  "sad",
  "angry",
  "embarrassed",
  "surprised",
  "loving",
  "confused",
  "smug",
  "tired",
];
for (const emotion of requiredAvatarEmotions) {
  assert(script.includes(`${emotion}:`), `Modular Live2D expression missing emotion: ${emotion}`);
}

assert(
  !/Object\.entries\(avatarParams\)/.test(dashboard),
  "dashboard.js must not apply top-level avatarParams as raw Cubism parameter IDs"
);
assert(
  /applyMoodState/.test(dashboard),
  "dashboard.js should route mood/avatar updates through SARAH_LIVE2D.applyMoodState"
);

console.log(JSON.stringify({
  ok: true,
  modelParamCount: modelParams.size,
  scriptParamCount: scriptParams.size,
  expressionCount: expressionNames.size,
  motionGroups: Object.keys(motions).length,
  eyeBlinkIds: groups.get("EyeBlink"),
  lipSyncIds: groups.get("LipSync"),
  canonicalStates: canonicalStates.length,
}, null, 2));
