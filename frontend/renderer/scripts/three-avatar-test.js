import * as THREE from "three";
import { GLTFLoader } from "../../node_modules/three/examples/jsm/loaders/GLTFLoader.js";

const MODEL_URL = "assets/live2d/sarah/Woman.glb";

function clamp01(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(1, n));
}

function easeInOut(t) {
  const x = clamp01(t);
  return x * x * (3 - 2 * x);
}

class Sarah3DTestAvatar {
  constructor() {
    this.refreshElements();
    this.visible = false;
    this.loaded = false;
    this.loading = null;
    this.renderer = null;
    this.scene = null;
    this.camera = null;
    this.root = null;
    this.mixer = null;
    this.actions = new Map();
    this.currentAction = null;
    this.clock = new THREE.Clock();
    this.raf = null;
    this.spin = null;
  }

  refreshElements() {
    this.container = document.getElementById("avatar-container");
    this.canvas = document.getElementById("sarah-3d-canvas");
    this.live2dCanvas = document.getElementById("sarah-canvas");
  }

  async show() {
    this.refreshElements();
    if (!this.container || !this.canvas) {
      console.warn("[Sarah/3D] missing avatar container/canvas");
      return false;
    }
    await this.load();
    this.visible = true;
    this.canvas.classList.remove("sarah-hidden");
    this.live2dCanvas?.classList.add("sarah-hidden");
    this.resize();
    this.start();
    console.log("[Sarah/3D] visible; animations=", this.listAnimations());
    return true;
  }

  hide() {
    this.refreshElements();
    this.visible = false;
    this.canvas?.classList.add("sarah-hidden");
    this.live2dCanvas?.classList.remove("sarah-hidden");
    this.stop();
    return true;
  }

  diagnostics() {
    return {
      visible: this.visible,
      loaded: this.loaded,
      hasContainer: !!this.container,
      hasCanvas: !!this.canvas,
      hasRenderer: !!this.renderer,
      hasRoot: !!this.root,
      animations: this.listAnimations(),
      canvasClass: this.canvas?.className || null,
      live2dClass: this.live2dCanvas?.className || null,
    };
  }

  async load() {
    if (this.loaded) return this;
    if (this.loading) return this.loading;

    this.loading = new Promise((resolve, reject) => {
      this.setupScene();
      const loader = new GLTFLoader();
      loader.load(
        MODEL_URL,
        (gltf) => {
          this.installModel(gltf);
          this.loaded = true;
          resolve(this);
        },
        undefined,
        (err) => {
          console.error("[Sarah/3D] failed to load", MODEL_URL, err);
          reject(err);
        }
      );
    });

    return this.loading;
  }

  setupScene() {
    if (this.renderer) return;

    this.renderer = new THREE.WebGLRenderer({
      canvas: this.canvas,
      alpha: true,
      antialias: true,
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(32, 1, 0.1, 100);
    this.camera.position.set(0, 0.9, 4.4);
    this.camera.lookAt(0, 0.35, 0);

    this.root = new THREE.Group();
    this.root.rotation.y = 0;
    this.scene.add(this.root);

    const key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(2.5, 4, 3);
    this.scene.add(key);

    const fill = new THREE.DirectionalLight(0x9fc7ff, 0.75);
    fill.position.set(-3, 2, 2);
    this.scene.add(fill);

    this.scene.add(new THREE.HemisphereLight(0xeef5ff, 0x2f2838, 1.25));

    window.addEventListener("resize", () => {
      if (this.visible) this.resize();
    });
  }

  installModel(gltf) {
    const model = gltf.scene;
    const box = new THREE.Box3().setFromObject(model);
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    model.position.sub(center);

    const maxDim = Math.max(size.x, size.y, size.z, 1);
    this.root.scale.setScalar(2.45 / maxDim);
    this.root.add(model);

    this.mixer = new THREE.AnimationMixer(model);
    for (const clip of gltf.animations || []) {
      const key = this.normalizeClipName(clip.name);
      const action = this.mixer.clipAction(clip);
      this.actions.set(key, action);
    }

    this.play("idle");
  }

  normalizeClipName(name) {
    return String(name || "")
      .split("|")
      .pop()
      .replace(/^Female_/i, "")
      .replace(/([a-z])([A-Z])/g, "$1_$2")
      .toLowerCase();
  }

  listAnimations() {
    return Array.from(this.actions.keys());
  }

  play(name = "idle", options = {}) {
    if (!this.mixer) return false;
    const key = this.normalizeClipName(name);
    const action = this.actions.get(key) || this.actions.get("idle") || this.actions.values().next().value;
    if (!action) return false;

    const once = options.once ?? !["idle", "walk", "run"].includes(key);
    action.reset();
    action.enabled = true;
    action.setLoop(once ? THREE.LoopOnce : THREE.LoopRepeat, once ? 1 : Infinity);
    action.clampWhenFinished = once;

    if (this.currentAction && this.currentAction !== action) {
      this.currentAction.crossFadeTo(action, 0.18, false);
    }
    action.play();
    this.currentAction = action;

    if (once) {
      const onDone = (event) => {
        if (event.action !== action) return;
        this.mixer.removeEventListener("finished", onDone);
        this.play("idle", { once: false });
      };
      this.mixer.addEventListener("finished", onDone);
    }
    return true;
  }

  triggerGesture(name, amount = 1) {
    const gesture = String(name || "").toLowerCase();
    if (gesture === "spin") return this.triggerSpin(amount);
    if (gesture === "jump") return this.play("jump", { once: true });
    if (gesture === "wave" || gesture === "arms_up") return this.play("clapping", { once: true });
    if (gesture === "point" || gesture === "shake_head") return this.play("punch", { once: true });
    if (gesture === "step_back" || gesture === "lean_in") return this.play("walk", { once: true });
    return this.play("standing", { once: true }) || this.play("idle");
  }

  triggerSpin(amount = 1) {
    if (!this.root) return false;
    this.play("idle");
    const strength = 0.85 + clamp01(amount) * 0.35;
    this.spin = {
      startedAt: performance.now(),
      duration: 1050 / strength,
      from: this.root.rotation.y,
      turns: Math.PI * 2,
    };
    return true;
  }

  resize() {
    this.refreshElements();
    if (!this.renderer || !this.container) return;
    const width = Math.max(1, this.container.clientWidth);
    const height = Math.max(1, this.container.clientHeight);
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  }

  start() {
    if (this.raf) return;
    this.clock.getDelta();
    const tick = () => {
      this.raf = requestAnimationFrame(tick);
      this.update();
    };
    tick();
  }

  stop() {
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = null;
  }

  update() {
    const dt = Math.min(0.05, this.clock.getDelta());
    this.mixer?.update(dt);

    if (this.spin && this.root) {
      const p = clamp01((performance.now() - this.spin.startedAt) / this.spin.duration);
      this.root.rotation.y = this.spin.from + this.spin.turns * easeInOut(p);
      if (p >= 1) {
        this.root.rotation.y = this.spin.from;
        this.spin = null;
      }
    }

    this.renderer?.render(this.scene, this.camera);
  }
}

window.SARAH_3D_TEST = window.SARAH_3D_TEST || new Sarah3DTestAvatar();
