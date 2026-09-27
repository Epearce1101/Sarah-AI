const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");

function read(rel) {
  return fs.readFileSync(path.join(ROOT, rel), "utf8");
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const moduleFiles = [
  "renderer/scripts/live2d/avatar-config.js",
  "renderer/scripts/live2d/avatar-emotion-engine.js",
  "renderer/scripts/live2d/avatar-lipsync.js",
  "renderer/scripts/live2d/avatar-motion-controller.js",
  "renderer/scripts/live2d/avatar-ai-sync.js",
  "renderer/scripts/live2d/avatar-system.js",
];

for (const rel of moduleFiles) {
  assert(fs.existsSync(path.join(ROOT, rel)), `missing avatar module: ${rel}`);
}

const index = read("renderer/index.html");
let previous = -1;
for (const rel of moduleFiles) {
  const scriptPath = rel.replace("renderer/", "");
  const idx = index.indexOf(scriptPath);
  assert(idx > previous, `script order is wrong or missing for ${scriptPath}`);
  previous = idx;
}
assert(
  previous < index.indexOf("dashboard.js"),
  "avatar modules must load before dashboard.js"
);

const context = {
  console,
  performance: { now: () => 1000 },
  localStorage: {
    data: new Map(),
    getItem(key) { return this.data.has(key) ? this.data.get(key) : null; },
    setItem(key, value) { this.data.set(key, String(value)); },
  },
  document: { readyState: "loading", addEventListener() {} },
  window: {},
};
context.window = {
  ...context.window,
  dispatchEvent() {},
  addEventListener() {},
  SARAH_LIVE2D: {},
};
context.CustomEvent = function CustomEvent(type, init) {
  return { type, ...init };
};
vm.createContext(context);

for (const rel of [
  "renderer/scripts/live2d/avatar-config.js",
  "renderer/scripts/live2d/avatar-emotion-engine.js",
  "renderer/scripts/live2d/avatar-lipsync.js",
]) {
  vm.runInContext(read(rel), context, { filename: rel });
}

const Engine = context.window.SarahAvatarEmotionEngine;
const engine = new Engine(context.window.SARAH_AVATAR_CONFIG);
const affection = engine.applyUserMessage("good girl, I love this");
assert(affection.emotion === "loving", "affectionate user text should map to loving");

const positiveContext = engine.updateFromContext([
  { role: "user", content: "good girl, I love this" },
  { role: "assistant", content: "I love that too. This is great." },
  { role: "user", content: "perfect, thank you" },
], { source: "test-context" });
assert(positiveContext.affinity > 0.55, "positive context should raise derived affinity");
assert(["happy", "loving"].includes(positiveContext.baseEmotion), "positive context should set warm baseline");

const shock = engine.applyUserMessage("this is broken and frustrating!");
assert(shock.overlayActive, "message reactions should use the transient overlay channel");
assert(["sad", "angry"].includes(shock.emotion), "overlay should react instantly to negative text");
assert(shock.baseEmotion === positiveContext.baseEmotion, "overlay reaction must not overwrite context baseline");
assert(shock.overlayDurationMs >= 1000 && shock.overlayDurationMs <= 3000, "overlay should fade over 1-3 seconds");
engine.decay(shock.overlayDurationMs / 2000);
assert(engine.getState().overlayActive, "overlay should remain active during the fade window");
engine.decay(3);
assert(!engine.getState().overlayActive, "overlay should clear after its fade window");
assert(engine.getState().emotion === engine.getState().baselineDisplayEmotion, "visible emotion should return to the inertial baseline");

const inertiaTarget = engine.baselineToState(engine.computeBaselineFromContext([
  { role: "user", content: "good girl, this is amazing and perfect" },
  { role: "assistant", content: "I love that. This is exciting!" },
  { role: "user", content: "thank you, Sarah!" },
]), { source: "test-context" });
const inertiaMetrics = engine.calculateInertiaMetrics(
  {
    baseEmotion: "neutral",
    emotion: "neutral",
    intensity: 0.15,
    baseWarmth: 0.45,
    sentiment: 0,
    affinity: 0.55,
    volatility: 0,
    responsiveness: 0.5,
  },
  { ...inertiaTarget, volatility: 0.5 }
);
assert(inertiaMetrics.durationMs >= 300 && inertiaMetrics.durationMs <= 800, "inertia duration should stay in 300-800ms range");
assert(inertiaMetrics.overshoot > 0, "intense baseline shifts should get overshoot");
assert(engine.pickAnticipationEmotion({ emotion: "neutral" }, inertiaTarget, 0.7) === "surprised", "warm shifts should use a surprise anticipation frame");
assert(engine.dampedSpring(0, 0.1) === 0 && engine.dampedSpring(1, 0.1) === 1, "spring endpoints should be stable");

let rafQueue = [];
context.requestAnimationFrame = (cb) => {
  rafQueue.push(cb);
  return rafQueue.length;
};
context.cancelAnimationFrame = () => {};
const inertiaEngine = new Engine(context.window.SARAH_AVATAR_CONFIG);
const inertialStart = inertiaEngine.updateFromContext([
  { role: "user", content: "good girl, this is amazing and perfect" },
  { role: "assistant", content: "I love that. This is exciting!" },
  { role: "user", content: "thank you, Sarah!" },
], { source: "test-inertia" });
assert(inertialStart.inertiaActive, "context baseline should start emotional inertia when RAF is available");
assert(inertialStart.inertiaDurationMs >= 300 && inertialStart.inertiaDurationMs <= 800, "active inertia should use the configured duration window");
assert(inertialStart.inertiaAnticipationEmotion === "surprised", "warm baseline jump should expose anticipation diagnostics");
rafQueue.shift()(1030);
assert(inertiaEngine.getState().displayEmotion === "surprised", "early inertia frame should use the anticipation expression");
const activeDuration = inertiaEngine.getState().inertiaDurationMs;
rafQueue.pop()(1000 + activeDuration + 8);
assert(!inertiaEngine.getState().inertiaActive, "final inertia frame should settle");
assert(inertiaEngine.getState().displayEmotion === inertiaEngine.getState().baseEmotion, "settled inertia should land on the target expression");
context.requestAnimationFrame = undefined;
context.cancelAnimationFrame = undefined;

const negative = engine.applyUserMessage("this is broken and frustrating");
assert(["sad", "angry"].includes(negative.emotion), "negative text should map to a negative emotion");

const negativeContext = engine.updateFromContext([
  { role: "user", content: "this is broken and frustrating" },
  { role: "assistant", content: "I can help fix it." },
  { role: "user", content: "bad and wrong again" },
], { source: "test-context" });
assert(negativeContext.affinity < positiveContext.affinity, "negative context should lower derived affinity");
assert(["neutral", "sad", "tired"].includes(negativeContext.baseEmotion), "negative context should avoid warm baseline");

assert(engine.registerExpression("curious", { params: { browLY: 0.2 } }), "custom expression registration failed");
assert(engine.getExpressionRecipe("curious").params.browLY === 0.2, "custom expression recipe was not stored");

const lipsync = context.window.SARAH_AVATAR_LIPSYNC;
const timeline = lipsync.buildTimeline("WOW! I really love this.", { maxFrames: 80 });
assert(timeline.length > 5, "lip sync timeline should include frames");
assert(timeline.some((frame) => frame.viseme === "closed"), "lip sync timeline should include closures for punctuation");

console.log(JSON.stringify({
  ok: true,
  moduleCount: moduleFiles.length,
  expressionCount: Object.keys(context.window.SARAH_AVATAR_CONFIG.expressions).length,
  timelineFrames: timeline.length,
}, null, 2));
