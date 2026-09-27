// Extracted from dashboard.js (improvement #7). Loaded as an ES module.
// SarahTTS: Piper playback, Live2D lip sync, and streaming sentence-by-sentence speech.

// -----------------------------------------------------------------------------
// TTS
// -----------------------------------------------------------------------------

export class SarahTTS {
  constructor(backend) {
    this.backend = backend;
    this.voiceEnabled =
      localStorage.getItem("sarah.voice.enabled") === "false" ? false : true;
    this.currentAudio = null;
    this.audioContext = null;
    this.lipSyncFrame = null;
    this.lipSyncSource = null;
    this.lipSyncAnalyser = null;
    this.visemeTimeline = [];
    // Half-duplex gate: when TTS is playing (or in the post-playback tail),
    // the renderer must not poll backend wake or process any STT capture —
    // otherwise the mic picks up Sarah's own voice and self-replies fire.
    this._ttsPlaying = false;
    this._ttsMuteUntil = 0;
    this._ttsTailMs = 1500;
    this._streamGeneration = 0;
  }

  isEnabled() {
    return this.voiceEnabled;
  }

  setEnabled(enabled) {
    this.voiceEnabled = enabled;
    localStorage.setItem("sarah.voice.enabled", enabled ? "true" : "false");
  }

  isMuting() {
    return this._ttsPlaying || Date.now() < this._ttsMuteUntil;
  }

  stop() {
    this._streamGeneration += 1; // invalidates any createStream() in flight
    if (this.currentAudio) {
      this.currentAudio.pause();
      this.currentAudio = null;
    }
    this._ttsPlaying = false;
    this._ttsMuteUntil = Date.now() + this._ttsTailMs;
    this._stopLive2DLipSync();
  }

  _stopLive2DLipSync() {
    if (this.lipSyncFrame) {
      cancelAnimationFrame(this.lipSyncFrame);
      this.lipSyncFrame = null;
    }

    try {
      this.lipSyncSource?.disconnect();
    } catch {
      // MediaElementAudioSourceNode can throw if already disconnected.
    }
    try {
      this.lipSyncAnalyser?.disconnect();
    } catch {
      // Analyzer cleanup is best-effort.
    }

    this.lipSyncSource = null;
    this.lipSyncAnalyser = null;
    window.SARAH_LIVE2D?.clearVoiceLevel?.();
    window.SARAH_LIVE2D?.clearVisemeTimeline?.();
    window.SARAH_LIVE2D?.setAvatarMode?.("idle", { source: "tts-stop" });
  }

  _buildApproxVisemeTimeline(text) {
    const modularTimeline = window.SARAH_AVATAR_SYSTEM?.buildVisemeTimeline?.(text, {
      source: "tts",
    });
    if (Array.isArray(modularTimeline) && modularTimeline.length) {
      return modularTimeline;
    }

    const timeline = [];
    const source = String(text || "").toLowerCase().slice(0, 360);
    const vowelMap = {
      a: "A",
      e: "E",
      i: "I",
      y: "I",
      o: "O",
      u: "U",
    };
    let time = 0;

    for (const ch of source) {
      if (/\s/.test(ch)) {
        time += 0.05;
        continue;
      }

      const viseme = vowelMap[ch] || ("bmp".includes(ch) ? "closed" : "neutral");
      timeline.push({ time, viseme, duration: 0.09 });
      time += /[.!?]/.test(ch) ? 0.16 : 0.075;

      if (timeline.length >= 220) break;
    }

    if (!timeline.length) {
      timeline.push({ time: 0, viseme: "neutral", duration: 0.12 });
    }
    timeline.push({ time: time + 0.04, viseme: "closed", duration: 0.12 });
    return timeline;
  }

  async _startLive2DLipSync(audio, text = "") {
    if (!window.SARAH_LIVE2D?.setVoiceLevel) return;
    const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextCtor) return;

    try {
      this.audioContext = this.audioContext || new AudioContextCtor();
      if (this.audioContext.state === "suspended") {
        await this.audioContext.resume();
      }

      const source = this.audioContext.createMediaElementSource(audio);
      const analyser = this.audioContext.createAnalyser();
      analyser.fftSize = 1024;
      source.connect(analyser);
      analyser.connect(this.audioContext.destination);

      const data = new Uint8Array(analyser.fftSize);
      this.lipSyncSource = source;
      this.lipSyncAnalyser = analyser;

      const tick = () => {
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i += 1) {
          const v = (data[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / data.length);
        window.SARAH_LIVE2D?.setVoiceLevel?.(Math.min(1, rms * 5));
        if (!audio.ended && !audio.paused) {
          this.lipSyncFrame = requestAnimationFrame(tick);
        }
      };

      audio.addEventListener("play", () => {
        window.SARAH_LIVE2D?.setAvatarMode?.("speaking", { source: "tts" });
        window.SARAH_LIVE2D?.applyVisemeTimeline?.(
          this._buildApproxVisemeTimeline(text),
          { source: "tts-text" }
        );
        this.lipSyncFrame = requestAnimationFrame(tick);
      }, { once: true });
      audio.addEventListener("ended", () => this._stopLive2DLipSync(), { once: true });
      audio.addEventListener("pause", () => {
        if (!audio.ended) window.SARAH_LIVE2D?.setVoiceLevel?.(0);
      });
    } catch (err) {
      console.warn("[TTS] Live2D lip sync unavailable:", err);
      this._stopLive2DLipSync();
    }
  }

  // Issue #18: map emotion/intensity → Piper prosody (length_scale, noise_w).
  // Higher intensity → faster + more variation. "sad" pulls toward slower +
  // quieter, "joy/excited" toward faster + livelier.
  _prosodyFor(emotion, intensity) {
    const i = Math.max(0, Math.min(1, Number(intensity) || 0));
    let length = 1.0 - (i - 0.5) * 0.18;        // i=0 → 1.09, i=1 → 0.91
    let noiseW = 0.7 + i * 0.35;                // i=0 → 0.70, i=1 → 1.05
    let noiseScale = 0.55 + i * 0.18;           // i=0 → 0.55, i=1 → 0.73

    const e = String(emotion || "").toLowerCase();
    if (/sad|melanchol|tired|down/.test(e))      { length += 0.06; noiseW -= 0.08; }
    else if (/joy|happy|excited|playful/.test(e)){ length -= 0.04; noiseW += 0.05; }
    else if (/angry|annoyed|frustrat/.test(e))   { length -= 0.03; noiseScale += 0.05; }
    else if (/calm|gentle|warm/.test(e))         { length += 0.02; noiseW -= 0.03; }

    return {
      length_scale: Math.max(0.7, Math.min(1.3, length)),
      noise_scale: Math.max(0.3, Math.min(0.9, noiseScale)),
      noise_w: Math.max(0.4, Math.min(1.2, noiseW)),
    };
  }

  _ttsOptions(opts = {}) {
    return (opts.emotion != null || opts.intensity != null)
      ? this._prosodyFor(opts.emotion, opts.intensity)
      : undefined;
  }

  // Start playing one synthesized clip. Resolves once playback has started
  // with a promise that settles when the clip ends (or is stopped/fails).
  async _startClip(url, text) {
    const audio = new Audio(url);
    // Issue #28: greetings ("Hello") were getting clipped at the start
    // because audio.play() fired before the element had decoded its first
    // PCM samples. Force eager preload and wait until the buffer has enough
    // data to play through (readyState >= HAVE_FUTURE_DATA) before starting
    // playback. Capped at 250ms so a slow decode never deadlocks the call.
    audio.preload = "auto";
    this.currentAudio = audio;
    audio.addEventListener("play", () => {
      this._ttsPlaying = true;
    }, { once: true });
    const release = () => {
      this._ttsPlaying = false;
      this._ttsMuteUntil = Date.now() + this._ttsTailMs;
    };
    audio.addEventListener("ended", release, { once: true });
    audio.addEventListener("error", release, { once: true });
    const ended = new Promise((resolve) => {
      for (const evt of ["ended", "error", "pause"]) {
        audio.addEventListener(evt, () => resolve(), { once: true });
      }
    });
    await this._startLive2DLipSync(audio, text);
    await new Promise((resolve) => {
      if (audio.readyState >= 3) {
        resolve();
        return;
      }
      const done = () => {
        audio.removeEventListener("canplay", done);
        audio.removeEventListener("canplaythrough", done);
        resolve();
      };
      audio.addEventListener("canplay", done, { once: true });
      audio.addEventListener("canplaythrough", done, { once: true });
      setTimeout(done, 250);
    });
    const tPlay = performance.now();
    await audio.play();
    if (window.B6_TIMING) console.log(`[TIMING] stage=audio.playback_start ms=${Math.round(performance.now() - tPlay)}`);
    return ended;
  }

  async speak(text, opts = {}) {
    if (!this.voiceEnabled) return;
    this.stop();

    const t0 = performance.now();
    try {
      const url = await this.backend.tts(text, this._ttsOptions(opts));
      if (window.B6_TIMING) console.log(`[TIMING] stage=tts.response_complete ms=${Math.round(performance.now() - t0)} text_len=${text.length}`);
      await this._startClip(url, text);
    } catch (err) {
      console.warn("TTS failed:", err);
      this._ttsPlaying = false;
      this._ttsMuteUntil = Date.now() + this._ttsTailMs;
    }
  }

  // Speak a reply while it is still being written. push(preview) takes the
  // whole cleaned text so far; each finished sentence is synthesized as soon
  // as it appears (the next one while the current one plays) and clips play
  // in order. finish(finalText, voiceOpts) speaks whatever is left. Returns
  // null when voice is off. Any stop() (new reply, voice toggled) cancels it.
  createStream(opts = {}) {
    if (!this.voiceEnabled) return null;
    this.stop();
    const generation = this._streamGeneration;
    const norm = (s) => String(s || "").replace(/\s+/g, " ").trim();
    const boundary = /[.!?…]+["')\]]*(?=\s)|\n{2,}/g;
    let voiceOpts = opts;
    let spokenText = "";
    let pending = "";
    const queue = [];
    let playing = false;
    const stale = () => generation !== this._streamGeneration;

    const pump = async () => {
      playing = true;
      while (queue.length && !stale()) {
        const { text, urlPromise } = queue.shift();
        const url = await urlPromise;
        if (!url || stale()) continue;
        try {
          const ended = await this._startClip(url, text);
          await ended;
        } catch (err) {
          console.warn("[TTS] Stream clip failed:", err);
        }
      }
      playing = false;
    };

    const enqueue = (segment) => {
      const text = norm(segment);
      if (!text || stale()) return;
      const urlPromise = this.backend.tts(text, this._ttsOptions(voiceOpts)).catch((err) => {
        console.warn("[TTS] Stream synthesis failed:", err);
        return null;
      });
      queue.push({ text, urlPromise });
      if (!playing) pump();
    };

    // Cut complete sentences off `pending`; the first clip may be short so
    // speech starts quickly, later ones are batched to fewer requests.
    const drain = () => {
      for (;;) {
        const minLen = spokenText ? 60 : 20;
        boundary.lastIndex = 0;
        let cut = -1;
        let m;
        while ((m = boundary.exec(pending))) {
          const end = m.index + m[0].length;
          if (end >= minLen) { cut = end; break; }
        }
        if (cut < 0) return;
        const segment = pending.slice(0, cut);
        pending = pending.slice(cut);
        spokenText += segment;
        enqueue(segment);
      }
    };

    return {
      push: (preview) => {
        if (stale()) return;
        const full = String(preview || "");
        const consumed = spokenText.length + pending.length;
        if (full.length > consumed && full.startsWith(spokenText + pending)) {
          pending += full.slice(consumed);
          drain();
        }
      },
      finish: (finalText, finalOpts = {}) => {
        if (stale()) return;
        if (finalOpts.emotion != null || finalOpts.intensity != null) voiceOpts = finalOpts;
        const finalNorm = norm(finalText);
        const spokenNorm = norm(spokenText);
        if (finalNorm.startsWith(spokenNorm)) {
          enqueue(finalNorm.slice(spokenNorm.length));
        } else if (!spokenNorm) {
          enqueue(finalNorm);
        }
        // Otherwise the cleaned final text diverged from what was already
        // spoken; skip the tail rather than repeat or garble it.
        pending = "";
      },
      cancel: () => {
        if (!stale()) this.stop();
      },
    };
  }
}
