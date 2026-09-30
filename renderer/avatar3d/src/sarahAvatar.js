// Sarah's 3D avatar: a VRM model + Mixamo animations + expressions + lip sync.
//
//   const avatar = new SarahAvatar(canvas);
//   await avatar.loadModel('models/sarah.vrm');
//   await avatar.loadAnimations(entries, 'animations/');
//   avatar.setEmotion('happy', 0.8);
//   avatar.play('wave');
//   await avatar.speak(ttsBlob);
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
import { loadMixamoFile, retargetClip } from './retargetMixamo.js';
import { LipSync } from './lipSync.js';
import { VRM_EMOTIONS, emotionToExpression } from './expressions.js';

const MOUTH_SHAPES = ['aa', 'ih', 'ou'];
// Played now and then while she's idle, if these groups are installed
const FIDGET_GROUPS = ['look', 'stretch', 'yawn'];

export class SarahAvatar {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {{framing?: 'upper'|'full', emotionHoldSeconds?: number,
   *          preserveDrawingBuffer?: boolean}} options
   */
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.framing = options.framing || 'upper';
    this.emotionHoldSeconds = options.emotionHoldSeconds ?? 30;

    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: true, // transparent background, so it can float over Sarah's UI
      preserveDrawingBuffer: !!options.preserveDrawingBuffer,
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(30, 1, 0.05, 50);
    this.camera.position.set(0, 1.4, 1.6);

    const light = new THREE.DirectionalLight(0xffffff, Math.PI);
    light.position.set(1, 1.5, 2);
    this.scene.add(light);
    this.scene.add(new THREE.AmbientLight(0xffffff, 0.6));

    this.clock = new THREE.Clock();
    this.elapsed = 0;
    this.vrm = null;
    this.mixer = null;

    this.entries = [];            // animation list, kept so a new model can reuse it
    this.clips = new Map();       // name -> { action, loop, group }
    this.groups = new Map();      // group -> [names]
    this._fileCache = new Map();  // url -> Promise<{root, clips}>
    this.current = null;          // name of the playing animation
    this.speaking = false;

    this.expressionTargets = Object.fromEntries(VRM_EMOTIONS.map((e) => [e, 0]));
    this.expressionValues = { ...this.expressionTargets };
    this.emotion = null;
    this._emotionSetAt = 0;

    this._nextFidget = 25 + Math.random() * 20;
    this._nextBlink = 2 + Math.random() * 3;
    this._blinkTime = -1;

    this.lipSync = new LipSync();
    this.audioEl = null;
    this._audioUrl = null;
    this._lipSyncActive = false;
    this._fakeTalkUntil = 0;
    this.mouth = { aa: 0, ih: 0, ou: 0 };

    // Browsers only allow audio after a click/key press; unlock it on the first one
    const unlockAudio = () => { this.lipSync.resume(); };
    window.addEventListener('pointerdown', unlockAudio, { once: true });
    window.addEventListener('keydown', unlockAudio, { once: true });

    this._resize = this._resize.bind(this);
    this._resizeObserver = new ResizeObserver(this._resize);
    this._resizeObserver.observe(canvas);
    this._resize();

    this.renderer.setAnimationLoop(() => this._tick());
  }

  // ------------------------------------------------------------
  // MODEL
  // ------------------------------------------------------------
  async loadModel(url, onProgress) {
    const loader = new GLTFLoader();
    loader.register((parser) => new VRMLoaderPlugin(parser));
    const gltf = await loader.loadAsync(url, onProgress);
    const vrm = gltf.userData.vrm;
    if (!vrm) throw new Error(`${url} is not a VRM model`);

    VRMUtils.removeUnnecessaryVertices(gltf.scene);
    VRMUtils.combineSkeletons(gltf.scene);
    VRMUtils.rotateVRM0(vrm); // old VRoid exports face backwards
    vrm.scene.traverse((obj) => { obj.frustumCulled = false; });

    if (this.vrm) {
      this.scene.remove(this.vrm.scene);
      VRMUtils.deepDispose(this.vrm.scene);
    }
    if (this.mixer) this.mixer.stopAllAction();

    this.vrm = vrm;
    this.scene.add(vrm.scene);
    if (vrm.lookAt) vrm.lookAt.target = this.camera; // eyes follow the viewer

    this.mixer = new THREE.AnimationMixer(vrm.scene);
    this.mixer.addEventListener('finished', (e) => {
      const entry = this.current && this.clips.get(this.current);
      if (entry && entry.action === e.action) this._returnToBase();
    });

    this.clips.clear();
    this.groups.clear();
    this.current = null;
    this._frameCamera();

    // Animations are fitted to a specific body, so redo them for the new model
    if (this.entries.length) await this.loadAnimations(this.entries, this._animationBaseUrl);
    return vrm;
  }

  setFraming(framing) {
    this.framing = framing;
    this._frameCamera();
  }

  _frameCamera() {
    if (!this.vrm) return;
    this.vrm.update(0);
    this.vrm.scene.updateMatrixWorld(true);
    const head = this.vrm.humanoid.getRawBoneNode('head');
    const headY = head ? head.getWorldPosition(new THREE.Vector3()).y : 1.4;
    if (this.framing === 'full') {
      const height = headY * 1.12;
      const dist = (height / 2) / Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)) * 1.15;
      this.camera.position.set(0, height / 2, dist);
      this.camera.lookAt(0, height / 2, 0);
    } else { // head and shoulders, like a VTuber
      this.camera.position.set(0, headY, 1.5);
      this.camera.lookAt(0, headY - 0.1, 0);
    }
  }

  // ------------------------------------------------------------
  // ANIMATIONS
  // ------------------------------------------------------------
  /**
   * @param {Array<{name: string, file: string, clip?: string, loop?: boolean,
   *                group?: string, inPlace?: boolean}>} entries
   * @param {string} baseUrl folder the files are in
   * @returns {Promise<{loaded: string[], missing: {name: string, error: string}[]}>}
   */
  async loadAnimations(entries, baseUrl = '') {
    this.entries = entries;
    this._animationBaseUrl = baseUrl;
    if (!this.vrm) return { loaded: [], missing: [] };

    // Replace any previously loaded set
    this.mixer.stopAllAction();
    for (const { action } of this.clips.values()) this.mixer.uncacheClip(action.getClip());
    this.clips.clear();
    this.groups.clear();
    this.current = null;

    // Download all files at once, then fit them to the model one by one
    const base = new URL(baseUrl || './', document.baseURI);
    const urls = entries.map((entry) => new URL(entry.file, base).href);
    for (const url of urls) {
      if (!this._fileCache.has(url)) this._fileCache.set(url, loadMixamoFile(url));
    }
    await Promise.allSettled(urls.map((url) => this._fileCache.get(url)));

    const loaded = [];
    const missing = [];
    for (let i = 0; i < entries.length; i++) {
      const entry = entries[i];
      const url = urls[i];
      try {
        const { root, clips } = await this._fileCache.get(url);
        const source = entry.clip
          ? clips.find((c) => c.name === entry.clip)
          : (clips.find((c) => c.name === 'mixamo.com') || clips[0]);
        if (!source) throw new Error(`clip "${entry.clip}" not found in ${entry.file}`);

        const clip = retargetClip(source, root, this.vrm, {
          name: entry.name,
          inPlace: entry.inPlace !== false,
        });
        const loop = entry.loop !== false;
        const action = this.mixer.clipAction(clip);
        action.setLoop(loop ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);
        action.clampWhenFinished = !loop;

        this.clips.set(entry.name, { action, loop, group: entry.group || entry.name });
        const group = entry.group || entry.name;
        if (!this.groups.has(group)) this.groups.set(group, []);
        this.groups.get(group).push(entry.name);
        loaded.push(entry.name);
      } catch (err) {
        this._fileCache.delete(url); // allow retry after the file is added
        missing.push({ name: entry.name, error: String(err?.message || err) });
      }
    }
    if (!this.current) this._returnToBase(0);
    return { loaded, missing };
  }

  /** Names of loaded animations and groups. */
  get animationNames() {
    return [...this.clips.keys()];
  }

  get groupNames() {
    return [...this.groups.keys()];
  }

  _resolve(nameOrGroup) {
    if (this.clips.has(nameOrGroup)) return nameOrGroup;
    const names = this.groups.get(nameOrGroup);
    if (!names || names.length === 0) return null;
    const choices = names.length > 1 ? names.filter((n) => n !== this.current) : names;
    return choices[Math.floor(Math.random() * choices.length)];
  }

  /**
   * Play an animation by name, or a random one from a group ("wave", "idle"...).
   * One-shot animations return to idle/talking when they finish.
   * @returns {boolean} false if nothing by that name is loaded
   */
  play(nameOrGroup, { fade = 0.4 } = {}) {
    const playing = this.current && this.clips.get(this.current);
    if (playing && playing.loop && this.groups.get(nameOrGroup)?.includes(this.current)) {
      return true; // already looping something from this group: don't swap variants
    }
    const name = this._resolve(nameOrGroup);
    if (!name) return false;
    const next = this.clips.get(name);
    const prev = this.current && this.clips.get(this.current);
    if (prev && prev.action === next.action && next.loop) return true;

    next.action.reset().setEffectiveTimeScale(1).setEffectiveWeight(1).fadeIn(fade).play();
    if (prev && prev.action !== next.action) prev.action.fadeOut(fade);
    this.current = name;
    return true;
  }

  _idleGroup() {
    const emotional = this.emotion && `idle_${this.emotion}`;
    if (emotional && this.groups.has(emotional)) return emotional;
    return 'idle';
  }

  _returnToBase(fade = 0.4) {
    const base = this.speaking && this.groups.has('talk') ? 'talk' : this._idleGroup();
    if (this.play(base, { fade })) return;
    // No idle animation installed: fade out and fall back to the built-in pose
    const prev = this.current && this.clips.get(this.current);
    if (prev) prev.action.fadeOut(fade);
    this.current = null;
  }

  /** Go back to idle (or talking, if she's speaking). */
  returnToIdle() {
    this._returnToBase();
  }

  _isOneShotPlaying() {
    const entry = this.current && this.clips.get(this.current);
    return !!entry && !entry.loop;
  }

  // Gentle breathing pose used until real idle animations are installed
  _proceduralIdle(t) {
    const h = this.vrm.humanoid;
    const set = (bone, x, y, z) => {
      const node = h.getNormalizedBoneNode(bone);
      if (node) node.rotation.set(x, y, z);
    };
    const breathe = Math.sin(t * 1.6);
    set('leftUpperArm', 0, 0, 1.2 - breathe * 0.015);
    set('rightUpperArm', 0, 0, -1.2 + breathe * 0.015);
    set('leftLowerArm', 0, -0.15, 0);
    set('rightLowerArm', 0, 0.15, 0);
    set('spine', breathe * 0.012, 0, 0);
    set('chest', breathe * 0.012, 0, 0);
    set('neck', 0, Math.sin(t * 0.35) * 0.04, 0);
    set('head', Math.sin(t * 0.5) * 0.02, Math.sin(t * 0.27) * 0.05, Math.sin(t * 0.4) * 0.02);
  }

  // ------------------------------------------------------------
  // EMOTIONS
  // ------------------------------------------------------------
  /** Show one of Sarah's emotions (e.g. from /api/chat's "emotion"). */
  setEmotion(emotion, intensity = 0.8) {
    const { expression, weight } = emotionToExpression(emotion, intensity);
    for (const e of VRM_EMOTIONS) this.expressionTargets[e] = e === expression ? weight : 0;
    const changed = this.emotion !== expression;
    this.emotion = expression;
    this._emotionSetAt = performance.now();
    // Switch to an emotion-specific idle (e.g. "idle_sad") if one is installed
    if (changed && !this.speaking && !this._isOneShotPlaying()) this._returnToBase();
    return { expression, weight };
  }

  // ------------------------------------------------------------
  // SPEAKING
  // ------------------------------------------------------------
  setSpeaking(on) {
    if (this.speaking === on) return;
    this.speaking = on;
    if (!this._isOneShotPlaying()) this._returnToBase(0.3);
  }

  /**
   * Play Sarah's voice with lip sync.
   * @param {Blob|string|HTMLAudioElement} source TTS audio (Blob, URL or element)
   * @returns {Promise<void>} resolves when she finishes talking
   */
  async speak(source) {
    // A new line interrupts whatever she was saying
    if (this._cancelSpeech) this._cancelSpeech();
    clearTimeout(this._fakeTalkTimer);
    this._fakeTalkUntil = 0;

    // If the browser hasn't unlocked audio yet, routing the voice through the
    // lip-sync analyser would make it silent. Play it plainly and fake the mouth.
    const audioReady = await this.lipSync.resume();

    let el;
    if (source instanceof HTMLMediaElement) {
      el = source;
    } else {
      if (this._audioUrl) URL.revokeObjectURL(this._audioUrl);
      this._audioUrl = null;
      if (audioReady) {
        if (!this.audioEl) this.audioEl = new Audio();
        el = this.audioEl;
      } else {
        el = new Audio(); // never wired into Web Audio, so it can always play
      }
      if (source instanceof Blob) {
        this._audioUrl = URL.createObjectURL(source);
        el.src = this._audioUrl;
      } else {
        el.src = source;
      }
    }
    if (audioReady) this.lipSync.connectElement(el);

    return new Promise((resolve, reject) => {
      let finished = false;
      const cleanup = () => {
        if (finished) return false;
        finished = true;
        el.removeEventListener('ended', onEnded);
        el.removeEventListener('error', onError);
        if (this._cancelSpeech === cancel) this._cancelSpeech = null;
        this._lipSyncActive = false;
        this._fakeTalkUntil = 0;
        this.setSpeaking(false);
        return true;
      };
      const onEnded = () => { if (cleanup()) resolve(); };
      const onError = () => { if (cleanup()) reject(new Error('Could not play Sarah\'s voice')); };
      const cancel = () => { el.pause(); if (cleanup()) resolve(); };
      this._cancelSpeech = cancel;
      el.addEventListener('ended', onEnded);
      el.addEventListener('error', onError);
      if (audioReady) {
        this._lipSyncActive = true;
      } else {
        el.addEventListener('loadedmetadata', () => {
          if (!finished) this._fakeTalkUntil = performance.now() + (el.duration || 3) * 1000;
        }, { once: true });
        this._fakeTalkUntil = performance.now() + 3000;
      }
      this.setSpeaking(true);
      el.play().catch((err) => { if (cleanup()) reject(err); });
    });
  }

  /** Lip sync from any Web Audio node instead of an <audio> element. */
  speakFromNode(node) {
    const disconnect = this.lipSync.connectNode(node);
    this._lipSyncActive = true;
    this.setSpeaking(true);
    return () => { disconnect(); this._lipSyncActive = false; this.setSpeaking(false); };
  }

  /** Move the mouth for `ms` without audio (used when TTS isn't available). */
  fakeTalk(ms) {
    this._fakeTalkUntil = performance.now() + ms;
    this.setSpeaking(true);
    clearTimeout(this._fakeTalkTimer);
    this._fakeTalkTimer = setTimeout(() => this.setSpeaking(false), ms);
  }

  // ------------------------------------------------------------
  // FRAME LOOP
  // ------------------------------------------------------------
  _updateExpressions(delta) {
    const em = this.vrm.expressionManager;
    if (!em) return;

    // Real time, not animation time, so a slow PC doesn't stretch the hold
    const now = performance.now();
    if (this.speaking) this._emotionSetAt = now; // keep the face while she talks
    if (this.emotion && now - this._emotionSetAt > this.emotionHoldSeconds * 1000) {
      this.setEmotion('neutral', 0); // relax back to a neutral face after a while
    }
    const k = 1 - Math.exp(-delta * 6); // smooth ~0.3 s transitions
    for (const e of VRM_EMOTIONS) {
      this.expressionValues[e] += (this.expressionTargets[e] - this.expressionValues[e]) * k;
      if (em.getExpression(e)) em.setValue(e, this.expressionValues[e]);
    }

    // Blinking (skipped while smiling, since VRoid's smile already closes the eyes)
    this._nextBlink -= delta;
    if (this._nextBlink <= 0) {
      this._blinkTime = 0;
      this._nextBlink = Math.random() < 0.15 ? 0.25 : 2 + Math.random() * 4;
    }
    let blink = 0;
    if (this._blinkTime >= 0) {
      this._blinkTime += delta;
      const b = this._blinkTime;
      blink = b < 0.06 ? b / 0.06 : b < 0.16 ? 1 - (b - 0.06) / 0.1 : 0;
      if (b >= 0.16) this._blinkTime = -1;
    }
    if (em.getExpression('blink')) em.setValue('blink', blink * (1 - this.expressionValues.happy));

    // Mouth
    let mouth = { aa: 0, ih: 0, ou: 0 };
    if (this._lipSyncActive) {
      mouth = this.lipSync.update();
    } else if (performance.now() < this._fakeTalkUntil) {
      const t = this.elapsed;
      const open = Math.max(0, Math.sin(t * 13)) * (0.55 + 0.45 * Math.sin(t * 3.7));
      mouth = { aa: open * 0.7, ih: open * 0.2 * (Math.sin(t * 5.3) > 0 ? 1 : 0), ou: 0 };
    }
    for (const shape of MOUTH_SHAPES) {
      this.mouth[shape] += (mouth[shape] - this.mouth[shape]) * Math.min(1, delta * 25);
      if (em.getExpression(shape)) em.setValue(shape, this.mouth[shape]);
    }
  }

  _maybeFidget(delta) {
    const entry = this.current && this.clips.get(this.current);
    const idling = entry && entry.loop && entry.group.startsWith('idle') && !this.speaking;
    if (!idling) return;
    this._nextFidget -= delta;
    if (this._nextFidget > 0) return;
    this._nextFidget = 25 + Math.random() * 20;
    const available = FIDGET_GROUPS.filter((g) => this.groups.has(g));
    if (available.length) this.play(available[Math.floor(Math.random() * available.length)]);
  }

  _tick() {
    const delta = Math.min(this.clock.getDelta(), 0.1);
    this.elapsed += delta;
    if (this.vrm) {
      if (!this.current) this._proceduralIdle(this.elapsed);
      this._maybeFidget(delta);
      this.mixer.update(delta);
      this._updateExpressions(delta);
      this.vrm.update(delta); // applies bones, expressions, look-at and hair/cloth physics
    }
    this.renderer.render(this.scene, this.camera);
  }

  _resize() {
    const w = this.canvas.clientWidth || 1;
    const h = this.canvas.clientHeight || 1;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  /** Snapshot for debugging/tests. */
  getState() {
    return {
      model: !!this.vrm,
      current: this.current,
      speaking: this.speaking,
      emotion: this.emotion,
      expressions: { ...this.expressionValues },
      mouth: { ...this.mouth },
      animations: this.animationNames,
      groups: this.groupNames,
    };
  }

  dispose() {
    this.renderer.setAnimationLoop(null);
    this._resizeObserver.disconnect();
    if (this.vrm) VRMUtils.deepDispose(this.vrm.scene);
    this.renderer.dispose();
  }
}
