// The body's end of /ws/senses: her mind (the backend) asks for things and
// the body does them.
//   {"type": "look", id, kinds, question}  -> take a fresh look, answer
//   {"type": "say", reply, conversation_id} -> show + speak a line she decided
//                                              to say (reminders, initiative)
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
    }
  }
}
