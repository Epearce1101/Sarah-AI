// Sarah's eyes: your webcam and your screen.
//
// Both are watched locally at low cost: every 1.5 s each source is shrunk
// to a tiny grey thumbnail and compared with what she last *looked at*.
// Only when the view has meaningfully changed (or it's been a long while)
// is a real frame sent to /api/perception/look, where a free vision model
// describes it (the backend also budgets how often). Frames are never saved.
import { API_BASE } from "./config.js";

const TICK_MS = 1500;
const THUMB_W = 48;
const THUMB_H = 27;
// Mean absolute difference (0..1) that counts as "something changed".
const CHANGE = { screen: 0.05, camera: 0.09 };
const HEARTBEAT_MS = 10 * 60 * 1000;     // look again after this even if static
const COMPANION_MS = 5 * 60 * 1000;      // include the other view if older than this

function thumb(video, canvas) {
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(video, 0, 0, THUMB_W, THUMB_H);
  const px = ctx.getImageData(0, 0, THUMB_W, THUMB_H).data;
  const grey = new Uint8Array(THUMB_W * THUMB_H);
  for (let i = 0; i < grey.length; i++) grey[i] = (px[i * 4] * 3 + px[i * 4 + 1] * 6 + px[i * 4 + 2]) / 10;
  return grey;
}

function difference(a, b) {
  if (!a || !b) return 1;
  let sum = 0;
  for (let i = 0; i < a.length; i++) sum += Math.abs(a[i] - b[i]);
  return sum / a.length / 255;
}

// A frame with (almost) no light and no variation: the source isn't
// delivering picture yet.
function isBlank(grey) {
  let min = 255, max = 0;
  for (const v of grey) { if (v < min) min = v; if (v > max) max = v; }
  return max < 12 || max - min < 3;
}

function jpeg(video, maxWidth, quality = 0.6) {
  const w = video.videoWidth;
  const h = video.videoHeight;
  if (!w || !h) return null;
  const scale = Math.min(1, maxWidth / w);
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(w * scale);
  canvas.height = Math.round(h * scale);
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", quality).split(",")[1];
}

function hiddenVideo() {
  const v = document.createElement("video");
  v.muted = true;
  v.playsInline = true;
  v.style.display = "none";
  document.body.appendChild(v);
  return v;
}

export class SarahEyes {
  constructor({ onStatus = () => {}, onSeen = () => {} } = {}) {
    this.onStatus = onStatus;
    this.onSeen = onSeen;
    this.mode = "off";              // "on" (screen + camera) | "screen" | "off"
    this.sources = {};              // kind -> { stream, video, canvas, lastSent, lastLookAt, prev }
    this.inFlight = false;
    this.motion = 0;                // camera motion level (people moving), local only
    this.minIntervalMs = 10000;     // updated from the backend's budget
    this.restUntil = 0;             // don't send frames before this (spacing/backoff)
  }

  async setMode(mode) {
    this.mode = mode;
    const wantCamera = mode === "on";
    const wantScreen = mode === "on" || mode === "screen";
    if (wantScreen) await this._open("screen"); else this._close("screen");
    if (wantCamera) await this._open("camera"); else this._close("camera");
    clearInterval(this._timer);
    if (mode !== "off") this._timer = setInterval(() => this._tick(), TICK_MS);
    this._status();
  }

  _status() {
    const open = Object.keys(this.sources);
    this.onStatus(this.mode, open);
  }

  async _open(kind) {
    if (this.sources[kind]) return;
    let stream;
    try {
      stream = kind === "camera"
        ? await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480, frameRate: 5 }, audio: false })
        : await this._openScreen();
    } catch (err) {
      console.warn(`[Eyes] ${kind} unavailable:`, err?.message || err);
      return;
    }
    const video = hiddenVideo();
    video.srcObject = stream;
    await video.play().catch(() => {});
    const canvas = document.createElement("canvas");
    canvas.width = THUMB_W;
    canvas.height = THUMB_H;
    this.sources[kind] = { stream, video, canvas, lastSent: null, lastLookAt: 0, prev: null };
    stream.getVideoTracks()[0]?.addEventListener("ended", () => { this._close(kind); this._status(); });
  }

  // The primary screen without a user gesture (getDisplayMedia needs one):
  // Electron's desktopCapturer id through getUserMedia.
  async _openScreen() {
    const id = await window.sarahVision?.screenSourceId?.();
    if (!id) return navigator.mediaDevices.getDisplayMedia({ video: { frameRate: 2 }, audio: false });
    return navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { mandatory: { chromeMediaSource: "desktop", chromeMediaSourceId: id, maxWidth: 1920, maxHeight: 1080, maxFrameRate: 2 } },
    });
  }

  _close(kind) {
    const s = this.sources[kind];
    if (!s) return;
    s.stream.getTracks().forEach((t) => t.stop());
    s.video.remove();
    delete this.sources[kind];
  }

  async _tick() {
    // Keeps watching when her window is covered (you're in a game): the
    // BrowserWindow has backgroundThrottling off.
    if (this.inFlight) return;
    const now = Date.now();
    const changes = {};
    for (const [kind, s] of Object.entries(this.sources)) {
      if (!s.video.videoWidth) continue;
      const t = thumb(s.video, s.canvas);
      s.blank = isBlank(t);
      if (s.blank) continue; // not painting yet (or a black screen): nothing to see
      changes[kind] = difference(t, s.lastSent);
      if (kind === "camera") this.motion = 0.7 * this.motion + 0.3 * difference(t, s.prev);
      s.prev = t;
      s.current = t;
    }
    // Which views are worth a look this tick?
    const due = Object.keys(changes).filter((kind) => {
      const s = this.sources[kind];
      return changes[kind] >= CHANGE[kind] || (now - s.lastLookAt > HEARTBEAT_MS && changes[kind] > 0.01);
    });
    if (!due.length || now < this.restUntil) return;
    // Bring the other view along if it has changed a bit or is stale.
    for (const kind of Object.keys(changes)) {
      if (due.includes(kind)) continue;
      const s = this.sources[kind];
      if (changes[kind] >= CHANGE[kind] / 2 || now - s.lastLookAt > COMPANION_MS) due.push(kind);
    }
    await this.look(due, { reason: "change" });
  }

  // Send the current frames of `kinds` to be looked at. Also used on demand.
  async look(kinds = Object.keys(this.sources), { reason = "request", question = null, urgent = false } = {}) {
    if (this.inFlight) return null;
    const body = { reason, question, urgent };
    for (const kind of kinds) {
      const s = this.sources[kind];
      if (!s || s.blank) continue;
      body[kind] = jpeg(s.video, kind === "screen" ? 1280 : 512);
    }
    if (!body.screen && !body.camera) return null;
    this.inFlight = true;
    try {
      const res = await fetch(`${API_BASE}/api/perception/look`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const out = res.ok ? await res.json() : null;
      const budget = out?.budget;
      if (budget?.min_interval_seconds) this.minIntervalMs = budget.min_interval_seconds * 1000;
      // Rest before the next automatic look: normal spacing, or the
      // backend's rate-limit backoff, or long if today's budget is spent.
      const rest = out?.skipped === "daily budget used" ? 30 * 60 * 1000
        : Math.max(this.minIntervalMs, (budget?.backoff_seconds || 0) * 1000);
      this.restUntil = Date.now() + (out?.skipped === "already looking" ? 3000 : rest);
      if (out?.seen) {
        const now = Date.now();
        for (const kind of kinds) {
          const s = this.sources[kind];
          if (s) { s.lastSent = s.current || s.lastSent; s.lastLookAt = now; }
        }
        this.onSeen(out.seen);
      }
      return out;
    } catch {
      return null;
    } finally {
      this.inFlight = false;
    }
  }
}
