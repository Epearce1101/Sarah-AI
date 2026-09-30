// Volume-based lip sync: listens to Sarah's voice and returns how open her
// mouth should be, plus a rough vowel shape (bright sounds -> "ih", dark
// sounds -> "ou"). Cheap, works with any TTS, no extra libraries.

const connectedElements = new WeakMap(); // an <audio> can only be wired up once

export class LipSync {
  constructor({ noiseFloor = 0.02, gain = 6, smoothing = 0.5 } = {}) {
    this.noiseFloor = noiseFloor;
    this.gain = gain;
    this.smoothing = smoothing; // 0 = jumpy, 1 = frozen
    this.ctx = null;
    this.analyser = null;
    this.timeData = null;
    this.freqData = null;
    this.level = 0;
    this.brightness = 0.5;
  }

  _ensureContext() {
    if (!this.ctx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      this.ctx = new Ctx();
      // Listen-only: the analyser isn't wired to the speakers, so nothing plays twice
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 1024;
      this.timeData = new Float32Array(this.analyser.fftSize);
      this.freqData = new Uint8Array(this.analyser.frequencyBinCount);
    }
    if (this.ctx.state === 'suspended') this.ctx.resume().catch(() => {});
    return this.ctx;
  }

  /**
   * Follow an <audio>/<video> element; it keeps playing through the speakers.
   * Only call this once resume() has returned true, otherwise the browser
   * keeps the element silent.
   */
  connectElement(mediaElement) {
    const ctx = this._ensureContext();
    let source = connectedElements.get(mediaElement);
    if (!source) {
      source = ctx.createMediaElementSource(mediaElement);
      connectedElements.set(mediaElement, source);
    }
    source.disconnect();
    source.connect(ctx.destination);
    source.connect(this.analyser);
  }

  /** Follow any Web Audio node (listen only). Returns a function that stops following. */
  connectNode(node) {
    this._ensureContext();
    node.connect(this.analyser);
    return () => { try { node.disconnect(this.analyser); } catch { /* already gone */ } };
  }

  get audioContext() {
    return this._ensureContext();
  }

  /** Browsers start audio "suspended" until the user interacts with the page. */
  async resume() {
    const ctx = this._ensureContext();
    if (ctx.state !== 'running') {
      // Before any click, resume() can stay pending forever, so don't wait long
      const timeout = new Promise((r) => setTimeout(r, 300));
      try { await Promise.race([ctx.resume(), timeout]); } catch { /* not allowed yet */ }
    }
    return ctx.state === 'running';
  }

  /** Call once per frame. Returns mouth shape weights 0..1. */
  update() {
    if (!this.analyser) return { aa: 0, ih: 0, ou: 0 };

    this.analyser.getFloatTimeDomainData(this.timeData);
    let sum = 0;
    for (let i = 0; i < this.timeData.length; i++) sum += this.timeData[i] * this.timeData[i];
    const rms = Math.sqrt(sum / this.timeData.length);
    const target = Math.min(1, Math.max(0, (rms - this.noiseFloor) * this.gain));
    this.level = this.level * this.smoothing + target * (1 - this.smoothing);

    // Share of energy above ~1.5 kHz decides the vowel shape
    this.analyser.getByteFrequencyData(this.freqData);
    const hzPerBin = this.ctx.sampleRate / 2 / this.freqData.length;
    const split = Math.floor(1500 / hzPerBin);
    let low = 0;
    let high = 0;
    for (let i = 1; i < this.freqData.length; i++) {
      if (i < split) low += this.freqData[i]; else high += this.freqData[i];
    }
    const bright = low + high > 0 ? high / (low + high) : 0.5;
    this.brightness = this.brightness * 0.8 + bright * 0.2;

    const lvl = this.level;
    return {
      aa: lvl * 0.8,
      ih: lvl * Math.max(0, this.brightness - 0.3) * 0.8,
      ou: lvl * Math.max(0, 0.3 - this.brightness) * 1.5,
    };
  }
}
