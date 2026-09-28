// Real-time hand gestures from Sarah's camera (MediaPipe, runs locally on
// the GPU/CPU, no cloud): wave, thumbs up/down, peace, "I love you", point
// up. Each one gets an instant body reaction; waves also reach her mind so
// she can greet you out loud.
import { FilesetResolver, GestureRecognizer } from "@mediapipe/tasks-vision";

const WASM = "../node_modules/@mediapipe/tasks-vision/wasm";
const MODEL = "assets/models/gesture_recognizer.task";
const FPS = 15;
const HOLD_MS = 450;          // a static gesture must be held this long
const COOLDOWN_MS = 5000;     // per gesture

export class SarahGestures {
  constructor({ getVideo, onGesture }) {
    this.getVideo = getVideo;       // () => the camera <video> (or null when off)
    this.onGesture = onGesture;     // (name) => void
    this.recognizer = null;
    this.timer = null;
    this.held = { name: null, since: 0 };
    this.last = {};                 // name -> time fired
    this.wrist = [];                // [{t, x}] for wave detection
  }

  async start() {
    if (this.timer) return true;
    if (!this.recognizer) {
      try {
        const fileset = await FilesetResolver.forVisionTasks(WASM);
        this.recognizer = await GestureRecognizer.createFromOptions(fileset, {
          baseOptions: { modelAssetPath: MODEL, delegate: "GPU" },
          runningMode: "VIDEO",
          numHands: 2,
        });
      } catch (err) {
        console.warn("[Gestures] unavailable:", err);
        return false;
      }
    }
    if (this.timer) return true;  // started twice while loading
    this.timer = setInterval(() => this._tick(), 1000 / FPS);
    return true;
  }

  stop() {
    clearInterval(this.timer);
    this.timer = null;
  }

  _fire(name, now) {
    if (now - (this.last[name] || 0) < COOLDOWN_MS) return;
    this.last[name] = now;
    this.onGesture?.(name);
  }

  _tick() {
    const video = this.getVideo?.();
    if (!video || !this.recognizer || video.readyState < 2 || !video.videoWidth) return;
    // MediaPipe rejects a timestamp that doesn't move forward.
    const now = Math.max(performance.now(), (this.lastTs || 0) + 1);
    this.lastTs = now;
    let result;
    try {
      result = this.recognizer.recognizeForVideo(video, now);
    } catch {
      return;
    }
    const top = result.gestures?.[0]?.[0];
    const name = top && top.score > 0.6 ? top.categoryName : "None";
    const wrist = result.landmarks?.[0]?.[0];

    // Wave: an open hand swinging side to side (2+ direction changes in ~1.2 s).
    this.wrist = this.wrist.filter((p) => now - p.t < 1200);
    if (name === "Open_Palm" && wrist) {
      this.wrist.push({ t: now, x: wrist.x });
      if (this._swings() >= 2) {
        this.wrist = [];
        this._fire("wave", now);
        return;
      }
    }

    // Static gestures: held briefly so a passing shape doesn't count.
    if (name !== this.held.name) {
      this.held = { name, since: now };
      return;
    }
    const map = { Thumb_Up: "thumbs_up", Thumb_Down: "thumbs_down", Victory: "peace",
                  ILoveYou: "love", Pointing_Up: "point_up" };
    if (map[name] && now - this.held.since >= HOLD_MS) this._fire(map[name], now);
  }

  _swings() {
    let changes = 0;
    let dir = 0;
    let anchor = this.wrist[0]?.x;
    for (const p of this.wrist) {
      const d = p.x - anchor;
      if (Math.abs(d) < 0.035) continue;          // ignore small jitter
      const now = Math.sign(d);
      if (dir && now !== dir) changes += 1;
      dir = now;
      anchor = p.x;
    }
    return changes;
  }
}
