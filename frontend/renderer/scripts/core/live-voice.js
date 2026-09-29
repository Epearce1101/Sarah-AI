// Live voice: an always-open, echo-cancelled microphone streamed to the
// backend (/ws/voice), which detects when you start and stop talking and
// transcribes each turn on the GPU. No wake word, no push-to-talk.
//
// Chromium's echo canceller uses this renderer's own audio output (Sarah's
// voice) as its reference, so she doesn't hear herself; the backend also
// asks for a louder, longer voice while she's speaking before treating it
// as you interrupting her.
import { API_PORT } from "./config.js";

const WORKLET = `
class SarahMicCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / 16000;   // e.g. 3 at 48 kHz
    this.pos = 0; this.sum = 0; this.count = 0;
    this.out = new Int16Array(512); this.n = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.sum += ch[i]; this.count++; this.pos += 1;
      if (this.pos >= this.step) {           // box-filter decimation to 16 kHz
        this.pos -= this.step;
        const s = Math.max(-1, Math.min(1, this.sum / this.count));
        this.sum = 0; this.count = 0;
        this.out[this.n++] = s * 32767;
        if (this.n === this.out.length) {
          this.port.postMessage(this.out.buffer.slice(0));
          this.n = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("sarah-mic-capture", SarahMicCapture);
`;

export class LiveVoice {
  constructor({ onEvent = () => {}, onStatus = () => {} } = {}) {
    this.onEvent = onEvent;
    this.onStatus = onStatus;
    this.active = false;
    this.muted = false;
    this.ws = null;
    this.ctx = null;
    this.stream = null;
    this.ready = false;
    this._retry = 0;
    this._speaking = false;
  }

  async start() {
    if (this.active) return true;
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      });
    } catch (err) {
      this.onStatus("no-mic", String(err?.message || err));
      return false;
    }
    this.ctx = new AudioContext({ latencyHint: "interactive" });
    const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
    await this.ctx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    const source = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, "sarah-mic-capture");
    this.node.port.onmessage = (ev) => {
      if (!this.muted && this.ws?.readyState === WebSocket.OPEN && this.ready) this.ws.send(ev.data);
    };
    // The worklet must be pulled by the graph; route it to a silent sink.
    const sink = this.ctx.createGain();
    sink.gain.value = 0;
    source.connect(this.node).connect(sink).connect(this.ctx.destination);
    this.active = true;
    this._connect();
    return true;
  }

  stop() {
    this.active = false;
    this.ready = false;
    clearTimeout(this._reconnect);
    try { this.ws?.close(); } catch {}
    this.ws = null;
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.ctx?.close().catch(() => {});
    this.ctx = null;
    this.onStatus("off");
  }

  setMuted(muted) {
    this.muted = Boolean(muted);
    if (this.muted) this._send({ type: "reset" });
    this.onStatus(this.muted ? "muted" : this.ready ? "listening" : "connecting");
  }

  // Tell the listener whether Sarah is talking (echo guard / barge-in).
  setSpeaking(speaking) {
    if (speaking === this._speaking) return;
    this._speaking = speaking;
    this._send({ type: "tts", speaking });
  }

  _send(obj) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj));
  }

  _connect() {
    if (!this.active) return;
    this.onStatus("connecting");
    const ws = new WebSocket(`ws://127.0.0.1:${API_PORT}/ws/voice`);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.type === "ready") {
        this.ready = true;
        this._retry = 0;
        this._send({ type: "tts", speaking: this._speaking });
        this.onStatus(this.muted ? "muted" : "listening", `${msg.model} on ${msg.device}`);
        return;
      }
      if (msg.type === "error") { this.onStatus("error", msg.detail); return; }
      this.onEvent(msg);
    };
    ws.onclose = () => {
      this.ready = false;
      if (!this.active) return;
      // The backend restarts or is still loading: keep trying, gently.
      const delay = Math.min(10000, 500 * 2 ** this._retry++);
      this.onStatus("connecting");
      this._reconnect = setTimeout(() => this._connect(), delay);
    };
  }
}

// Her name as speech recognition tends to write it.
const NAME = "(?:sarah|sara|sarrah|serah|sera|zarah|sahra)";
const WAKE_ANYWHERE = new RegExp(`\\b${NAME}\\b`, "i");
const WAKE_LEADING = new RegExp(`^\\s*(?:(?:hey|hi|hello|ok|okay|yo|oi)[\\s,]+)?${NAME}\\b[\\s,.!?:;-]*`, "i");

/**
 * Wake word: did they say her name, and what's the request?
 * "Hey Sarah, what's the time?" -> { heard: true, rest: "what's the time?" }
 * "What do you think, Sarah?"    -> { heard: true, rest: the whole sentence }
 * "Hey Sarah."                   -> { heard: true, rest: "" } (she waits for the rest)
 */
export function wakeWord(text) {
  const t = String(text || "").trim();
  if (!WAKE_ANYWHERE.test(t)) return { heard: false, rest: "" };
  const lead = t.match(WAKE_LEADING);
  const rest = lead ? t.slice(lead[0].length).trim() : t;
  return { heard: true, rest: rest.replace(/^[,.!?\s]+/, "") };
}

// Words heard that are just Sarah's own voice leaking back in.
export function isEcho(heard, spoken) {
  const words = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9' ]+/g, " ").split(/\s+/).filter(Boolean);
  const h = words(heard);
  if (!h.length) return true;
  const said = new Set(words(spoken));
  if (!said.size) return false;
  const overlap = h.filter((w) => said.has(w)).length / h.length;
  return h.length >= 2 ? overlap >= 0.7 : overlap === 1;
}
