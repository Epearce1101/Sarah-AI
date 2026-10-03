// The body's end of /ws/senses: her mind (the backend) asks for things and
// the body does them.
//   {"type": "look", id, kinds, question}  -> take a fresh look, answer
//   {"type": "say", reply, conversation_id} -> show + speak a line she decided
//                                              to say (reminders, initiative)
//   {"type": "grab", id, kind}             -> a sharp still of the screen
//   {"type": "mark", marks, seconds}       -> circle/arrow/... on the screen overlay
import { API_PORT } from "./config.js";

export class SarahSenses {
  constructor({ eyes, ui }) {
    this.eyes = eyes;
    this.ui = ui;
    this._retry = 0;
  }

  start() {
    this._connect();
    return this;
  }

  _connect() {
    const ws = new WebSocket(`ws://127.0.0.1:${API_PORT}/ws/senses`);
    this.ws = ws;
    ws.onopen = () => { this._retry = 0; };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      this._handle(msg).catch((err) => console.warn("[Senses]", msg.type, err));
    };
    ws.onclose = () => {
      const delay = Math.min(15000, 1000 * 2 ** this._retry++);
      setTimeout(() => this._connect(), delay);
    };
  }

  _reply(id, data) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: "response", id, data }));
  }

  async _handle(msg) {
    if (msg.type === "look") {
      const kinds = (msg.kinds || ["screen"]).filter((k) => this.eyes?.sources?.[k]);
      if (!kinds.length) return this._reply(msg.id, null);
      window.SARAH_AVATAR_DIRECTOR?.lookAt?.(kinds.includes("camera") ? "user" : "screen", 2.5, "looking");
      const out = await this.eyes.look(kinds, { reason: "request", question: msg.question, urgent: true });
      this._reply(msg.id, out?.seen || (out?.skipped ? { unavailable: out.skipped } : null));
    } else if (msg.type === "say") {
      this.ui?.presentSpontaneousReply?.(msg.reply, msg.conversation_id ?? this.ui.activeConversationId);
    } else if (msg.type === "activity") {
      // She's using a tool on her own initiative: show it under her feet.
      this.ui?._showOwnActivity?.(msg);
    } else if (msg.type === "grab") {
      // A sharp still of the screen, for finding something by sight.
      const frame = this.eyes?.sources?.[msg.kind || "screen"] ? await this.eyes.grab(msg.kind || "screen") : null;
      this._reply(msg.id, frame);
    } else if (msg.type === "mark") {
      // Draw on the screen overlay; in pet mode she also points at it.
      await window.sarahApp?.overlayMark?.({ marks: msg.marks || [], seconds: msg.seconds || 6, clear: Boolean(msg.clear) });
      const m = msg.marks?.[0];
      if (m && document.documentElement.classList.contains("pet-mode") && window.sarahApp?.screenPointToClient) {
        const [x, y, w, h] = m.rect;
        const at = await window.sarahApp.screenPointToClient(x + w / 2, y + h / 2);
        if (at) window.SARAH_AVATAR_DIRECTOR?.pointAtScreen?.(at.x, at.y, { hold: 3.2, label: m.label || "the screen" });
      }
    }
  }
}
