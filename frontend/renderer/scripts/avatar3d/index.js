// Boots Sarah's 3D body and routes the existing avatar hooks to it.
// If the VRM can't load (file missing, WebGL unavailable, forced off), the
// Live2D avatar starts instead - live2d-sarah.js waits on
// window.SARAH_AVATAR_3D_PENDING before initialising.
import { SarahVRM } from "./sarah-vrm.js";
import { SarahDirector, GESTURES, PROCEDURAL } from "./director.js";
import { parseCues, stripCues, CUE_KINDS } from "./cues.js";
import { SarahPresence } from "./presence.js";

const MODEL_URL = "assets/vrm/sarah.vrm";
const ANIMATIONS_URL = "assets/vrm/animations/";

window.SARAH_AVATAR_CUES = { parseCues, stripCues, CUE_KINDS };

function preferred() {
  try { return localStorage.getItem("sarah.avatar.renderer") || "vrm"; } catch { return "vrm"; }
}

function domReady() {
  if (document.readyState !== "loading") return Promise.resolve();
  return new Promise((resolve) => document.addEventListener("DOMContentLoaded", resolve, { once: true }));
}

function routeFacade(director) {
  const live2d = (window.SARAH_LIVE2D ||= {});
  // Mode hints from the dashboard / sync controller. "speaking" comes from
  // the TTS player itself (speechStart) with real audio timing.
  live2d.setAvatarMode = (mode) => { director.onModeHint(mode); };
  // The old engine's guessed mood must not overwrite what she actually
  // feels; her mood arrives via onAIResponse / syncBackendMood below.
  live2d.applyMoodState = () => {};

  const system = window.SARAH_AVATAR_SYSTEM;
  if (!system) return;
  system.motion?.stop?.(); // Live2D procedural motion has nothing to drive now
  const original = {
    onUserMessage: system.onUserMessage.bind(system),
    onAIResponse: system.onAIResponse.bind(system),
    syncBackendMood: system.syncBackendMood.bind(system),
  };
  // Keep the emotion engine (mood memory, context), but don't let it fire
  // Live2D gestures: body language now comes from cues and the director.
  if (system.ai) system.ai.motion = null;
  system.triggerGesture = (name, amount) => director.gesture(name, amount);
  system.listGestures = () => [...new Set([...Object.keys(GESTURES), ...PROCEDURAL])];
  system.onUserMessage = (text) => {
    const state = original.onUserMessage(text);
    director.onUserMessage(text);
    return state;
  };
  // Her mood is what she herself felt (the backend's `emotion`, from her
  // <feel>), not the old renderer engine's guess from the words.
  system.onAIResponse = (text, meta = {}) => {
    const state = original.onAIResponse(text, meta);
    director.onReply({ emotion: meta.emotion || state?.emotion, intensity: meta.intensity ?? state?.intensity });
    return state;
  };
  system.syncBackendMood = (mood, params) => {
    const state = original.syncBackendMood(mood, params);
    const emotion = mood?.emotion || state?.emotion;
    if (emotion && !director.feeling) director.setMood(emotion, mood?.intensity ?? state?.intensity ?? 0.4);
    return state;
  };
  system.getDiagnostics = () => ({
    renderer: "vrm",
    mode: director.mode,
    mood: director.mood,
    frame: director.avatar.frame,
    base: director.avatar.animator.base?.id || null,
    gesture: director.avatar.animator.oneShot?.id || null,
    animations: director.avatar.animator.catalog.size,
  });
}

async function boot() {
  if (preferred() === "live2d") return false;
  await domReady();
  const container = document.getElementById("avatar-container");
  if (!container) return false;
  const canvas = document.createElement("canvas");
  canvas.id = "sarah-vrm-canvas";
  canvas.className = "sarah-vrm-canvas";
  container.appendChild(canvas);
  try {
    const avatar = await new SarahVRM({ container, canvas, modelUrl: MODEL_URL, animationsUrl: ANIMATIONS_URL }).init();
    const director = new SarahDirector(avatar);
    avatar.start();
    // Never leave her invisible if the idle clip is slow or missing.
    setTimeout(() => { avatar.vrm.scene.visible = true; }, 4000);
    document.getElementById("sarah-canvas")?.classList.add("sarah-hidden");
    document.getElementById("avatar-fallback")?.classList.add("sarah-hidden"); // the "^_^" placeholder
    container.classList.add("avatar-3d");
    routeFacade(director);
    const input = document.getElementById("chat-input");
    input?.addEventListener("input", () => director.onUserTyping());
    // Body <-> mind: report what her body does and senses; spoken reactions
    // from her mind come back through the dashboard like any reply.
    const presence = new SarahPresence(director, {
      getConversationId: () => window.SARAH_UI?.activeConversationId ?? null,
      onSpontaneousReply: (reply, conversationId) => window.SARAH_UI?.presentSpontaneousReply?.(reply, conversationId),
    }).start();
    window.SARAH_AVATAR_DIRECTOR = director;
    window.SARAH_PRESENCE = presence;
    window.SARAH_VRM = avatar;
    console.info("[Sarah/VRM] ready:", avatar.vrm.meta?.name || "model", `${avatar.animator.catalog.size} animations`);
    return true;
  } catch (err) {
    console.error("[Sarah/VRM] failed, falling back to Live2D:", err);
    canvas.remove();
    return false;
  }
}

window.SARAH_AVATAR_3D_PENDING = boot().catch((err) => {
  console.error("[Sarah/VRM] boot error:", err);
  return false;
});
