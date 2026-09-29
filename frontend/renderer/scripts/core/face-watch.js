// Watching Zero's face through her camera (MediaPipe Face Landmarker, local,
// no cloud): their expression, nods, head shakes and head tilts, so she can
// smile back, nod along or mirror a tilt. Runs ~8 times a second while the
// camera is on.
import { FilesetResolver, FaceLandmarker } from "@mediapipe/tasks-vision";
import { expressionOf, headAngles, swings } from "./face-logic.js";

const WASM = "../node_modules/@mediapipe/tasks-vision/wasm";
const MODEL = "assets/models/face_landmarker.task";
const FPS = 8;
const HOLD_MS = { smiling: 700, surprised: 250, frowning: 1800 };
const COOLDOWN_MS = { smiling: 20000, surprised: 15000, frowning: 60000, nod: 6000, shake: 8000, tilt: 12000 };

export class SarahFaceWatch {
  constructor({ getVideo, onEvent }) {
    this.getVideo = getVideo;   // () => camera <video> or null
    this.onEvent = onEvent;     // (name, detail) => void
    this.landmarker = null;
    this.timer = null;
    this.expression = "neutral"; // what their face shows now (held)
    this.present = false;
    this._held = { name: "neutral", since: 0 };
    this._pose = [];
    this._last = {};
    this._lastTs = 0;
  }

  async start() {
    if (this.timer) return true;
    if (!this.landmarker) {
      try {
        const fileset = await FilesetResolver.forVisionTasks(WASM);
        this.landmarker = await FaceLandmarker.createFromOptions(fileset, {
          baseOptions: { modelAssetPath: MODEL, delegate: "GPU" },
          runningMode: "VIDEO",
          numFaces: 1,
          outputFaceBlendshapes: true,
          outputFacialTransformationMatrixes: true,
        });
      } catch (err) {
        console.warn("[FaceWatch] unavailable:", err);
        return false;
      }
    }
    if (this.timer) return true;
    this.timer = setInterval(() => this._tick(), 1000 / FPS);
    return true;
  }

  stop() {
    clearInterval(this.timer);
    this.timer = null;
    this.present = false;
    this.expression = "neutral";
  }

  _fire(name, now, detail = {}) {
    if (now - (this._last[name] || 0) < (COOLDOWN_MS[name] || 5000)) return;
    this._last[name] = now;
    this.onEvent?.(name, detail);
  }

  _tick() {
    const video = this.getVideo?.();
    if (!video || !this.landmarker || video.readyState < 2 || !video.videoWidth) return;
    const now = Math.max(performance.now(), this._lastTs + 1);
    this._lastTs = now;
    let result;
    try {
      result = this.landmarker.detectForVideo(video, now);
    } catch {
      return;
    }
    const shapes = result.faceBlendshapes?.[0]?.categories;
    this.present = Boolean(shapes);
    if (!shapes) {
      this._pose = [];
      this._held = { name: "neutral", since: now };
      this.expression = "neutral";
      return;
    }

    // Expression, held briefly so a passing twitch doesn't count.
    const exp = expressionOf(shapes);
    if (exp !== this._held.name) this._held = { name: exp, since: now };
    if (now - this._held.since >= (HOLD_MS[exp] || 0)) {
      if (exp !== this.expression && exp !== "neutral") this._fire(exp, now);
      this.expression = exp;
    }

    // Head movement: nods (pitch), shakes (yaw), a held tilt (roll).
    const pose = headAngles(result.facialTransformationMatrixes?.[0]);
    if (!pose) return;
    this._pose = [...this._pose.filter((p) => now - p.t < 2000), { t: now, ...pose }];
    if (swings(this._pose, "pitch", 6, now) >= 2) { this._pose = []; this._fire("nod", now); return; }
    if (swings(this._pose, "yaw", 9, now) >= 2) { this._pose = []; this._fire("shake", now); return; }
    const recent = this._pose.filter((p) => now - p.t < 700);
    if (recent.length >= 4 && recent.every((p) => Math.abs(p.roll) > 12 && Math.sign(p.roll) === Math.sign(recent[0].roll))) {
      this._fire("tilt", now, { side: Math.sign(recent[0].roll) });
    }
  }
}
