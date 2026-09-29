// The link between Sarah's body and her mind (backend/embodiment).
//
// - Proprioception: what her body is doing (director.describe()) is sent to
//   the backend whenever it changes, so every prompt knows it.
// - Senses: things that happen to her (a touch, the user coming back, the
//   app opening) are sent as events. Her body has already reacted on reflex;
//   her mind may answer in her own voice, and that line comes back here to
//   be shown and spoken like any other reply.
import { API_BASE } from "../core/config.js";

const STATE_URL = `${API_BASE}/api/embodiment/state`;
const EVENT_URL = `${API_BASE}/api/embodiment/event`;

async function post(url, body) {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return res.ok ? await res.json() : null;
  } catch {
    return null; // backend restarting: her body carries on regardless
  }
}

// Coarse buckets so an idle clock ticking doesn't count as a change.
function idleBucket(seconds) {
  if (seconds < 60) return 0;
  if (seconds < 600) return Math.floor(seconds / 60);
  return 10 + Math.floor(seconds / 600);
}

export class SarahPresence {
  constructor(director, { getConversationId = () => null, onSpontaneousReply = null } = {}) {
    this.director = director;
    this.getConversationId = getConversationId;
    this.onSpontaneousReply = onSpontaneousReply;
    this.lastKey = "";
    this.lastSent = 0;
  }

  start() {
    this.director.onSensation = (kind, detail) => this.sense(kind, detail);
    this._timer = setInterval(() => this.report(), 1500);
    document.addEventListener("visibilitychange", () => {
      this.report(true);
      if (document.visibilityState === "hidden") this.sense("dismissed", {});
    });
    this.report(true);
    this._arrive();
    return this;
  }

  stop() {
    clearInterval(this._timer);
    this.director.onSensation = null;
  }

  async report(force = false) {
    // Hidden while the pet window (or the tray) has her: the active window reports.
    if (window.SARAH_UI?._inTray && window.SARAH_UI?._trayForPet) return;
    const view = window.SARAH_UI?.eyes?.view?.() || {};
    const face = window.SARAH_UI?.faceWatch;
    const state = { ...this.director.describe(), conversation_id: this.getConversationId(),
                    user_expression: face?.timer && face.present ? face.expression : null,
                    extra_moves: (this.director.avatar.animator.extras || []).map((s) => s.replace(/_/g, " ")).join(", ").slice(0, 118) || null,
                    eyes_screen: view.screen || "off", eyes_camera: view.camera || "off",
                    ears: !window.SARAH_UI?.liveVoice?.active ? "off"
                      : window.SARAH_UI?._micMode === "wake" ? "wake word" : "on" };
    const key = JSON.stringify({ ...state, user_idle_seconds: idleBucket(state.user_idle_seconds) });
    const now = Date.now();
    if (!force && key === this.lastKey && now - this.lastSent < 30000) return;
    this.lastKey = key;
    this.lastSent = now;
    await post(STATE_URL, state);
  }

  async sense(kind, detail = {}) {
    const conversationId = this.getConversationId();
    await this.report(true); // her mind should see the body as it is right now
    const out = await post(EVENT_URL, { type: kind, conversation_id: conversationId, detail });
    if (out?.spoke && out.reply && this.getConversationId() === conversationId) {
      this.onSpontaneousReply?.(out.reply, conversationId);
    }
    return out;
  }

  // She "comes to" when the app opens; after a long gap her mind may greet.
  async _arrive() {
    for (let i = 0; i < 40 && this.getConversationId() == null; i++) {
      await new Promise((r) => setTimeout(r, 500));
    }
    await new Promise((r) => setTimeout(r, 1500)); // let the history paint first
    this.sense("arrived", {});
  }
}
