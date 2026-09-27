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
    window.SARAH_AVATAR_DIRECTOR?.speechCancel?.();
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
    // The 3D director tracks speech itself (speechStart/End); an idle hint
    // here would knock it out of "thinking" when a new reply starts.
    if (!window.SARAH_AVATAR_DIRECTOR) {
      window.SARAH_LIVE2D?.setAvatarMode?.("idle", { source: "tts-stop" });
    }
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

  // Route the clip through an analyser and hand it to whichever avatar is
  // active: the 3D director gets the analyser (lip sync + speech beats) and
  // the clip's stage-direction cues, timed to this clip's playback; the
  // Live2D rig gets a voice level per frame.
  async _attachAvatarAudio(audio, text = "", cues = []) {
    const director = window.SARAH_AVATAR_DIRECTOR;
    const live2d = window.SARAH_LIVE2D?.setVoiceLevel && !director;
    const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
    let analyser = null;

    if ((director || live2d) && AudioContextCtor) {
      try {
        this.audioContext = this.audioContext || new AudioContextCtor();
        if (this.audioContext.state === "suspended") {
          await this.audioContext.resume();
        }
        const source = this.audioContext.createMediaElementSource(audio);
        analyser = this.audioContext.createAnalyser();
        analyser.fftSize = 1024;
        analyser.smoothingTimeConstant = 0.5;
        source.connect(analyser);
        analyser.connect(this.audioContext.destination);
        this.lipSyncSource = source;
        this.lipSyncAnalyser = analyser;
      } catch (err) {
        console.warn("[TTS] audio analyser unavailable:", err);
        analyser = null;
      }
    }

    if (director) {
      audio.addEventListener("play", () => {
        director.speechStart({ analyser, text, cues, duration: audio.duration });
      }, { once: true });
      const end = () => director.speechEnd();
      audio.addEventListener("ended", end, { once: true });
      audio.addEventListener("error", end, { once: true });
      return;
    }
    if (live2d && analyser) this._startLive2DLipSync(audio, text, analyser);
  }

  _startLive2DLipSync(audio, text, analyser) {
    try {
      const data = new Uint8Array(analyser.fftSize);

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
  async _startClip(url, text, cues = []) {
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
    await this._attachAvatarAudio(audio, text, cues);
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

  // opts.cues: stage directions ({type, value, at}) with `at` offsets in text.
  async speak(text, opts = {}) {
    if (!this.voiceEnabled) return;
    this.stop();

    const t0 = performance.now();
    try {
      const url = await this.backend.tts(text, this._ttsOptions(opts));
      if (window.B6_TIMING) console.log(`[TIMING] stage=tts.response_complete ms=${Math.round(performance.now() - t0)} text_len=${text.length}`);
      await this._startClip(url, text, opts.cues || []);
    } catch (err) {
      console.warn("TTS failed:", err);
      this._ttsPlaying = false;
      this._ttsMuteUntil = Date.now() + this._ttsTailMs;
      this._performNow(opts.cues);
    }
  }

  // Speech failed: still act out the directions so the body isn't lost.
  _performNow(cues) {
    const director = window.SARAH_AVATAR_DIRECTOR;
    if (director && cues?.length) director.performSequence(cues);
  }

  // Speak a reply while it is still being written. push(preview, cues) takes
  // the whole cleaned text so far plus its stage-direction cues (offsets in
  // that text); each finished sentence is synthesized as soon as it appears
  // (the next one while the current one plays), clips play in order, and a
  // clip's cues fire as its audio reaches them. finish(finalText, voiceOpts,
  // finalCues) speaks whatever is left. Returns null when voice is off. Any
  // stop() (new reply, voice toggled) cancels it.
  createStream(opts = {}) {
    if (!this.voiceEnabled) return null;
    this.stop();
    const generation = this._streamGeneration;
    const norm = (s) => String(s || "").replace(/\s+/g, " ").trim();
    const boundary = /[.!?…]+["')\]]*(?=\s)|\n{2,}/g;
    let voiceOpts = opts;
    let spokenText = "";
    let pending = "";
    let cues = [];
    const queue = [];
    let playing = false;
    const stale = () => generation !== this._streamGeneration;

    // Cues inside [start, end) of the preview, rebased onto the spoken text.
    const cuesBetween = (start, end, segment, text) => {
      const lead = segment.length - segment.trimStart().length;
      return cues
        .filter((c) => c.at >= start && c.at < end)
        .map((c) => ({ ...c, at: Math.max(0, Math.min(text.length, c.at - start - lead)) }));
    };

    let last = null; // most recent clip; trailing cues wait for it to end

    const pump = async () => {
      playing = true;
      while (queue.length && !stale()) {
        const item = queue.shift();
        const url = await item.urlPromise;
        if (stale()) continue;
        if (!url) {
          this._performNow(item.cues);
        } else {
          try {
            const ended = await this._startClip(url, item.text, item.cues);
            await ended;
          } catch (err) {
            console.warn("[TTS] Stream clip failed:", err);
            this._performNow(item.cues);
          }
        }
        item.done = true;
        if (!stale()) this._performNow(item.after);
      }
      playing = false;
    };

    const enqueue = (segment, clipCues = []) => {
      const text = norm(segment);
      if (stale()) return;
      if (!text) {
        // Directions after the last words (e.g. a closing <gesture>) happen
        // once that sentence has been spoken, not straight away.
        if (last && !last.done) last.after.push(...clipCues);
        else this._performNow(clipCues);
        return;
      }
      const urlPromise = this.backend.tts(text, this._ttsOptions(voiceOpts)).catch((err) => {
        console.warn("[TTS] Stream synthesis failed:", err);
        return null;
      });
      last = { text, urlPromise, cues: clipCues, after: [], done: false };
      queue.push(last);
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
        const start = spokenText.length;
        pending = pending.slice(cut);
        spokenText += segment;
        enqueue(segment, cuesBetween(start, spokenText.length, segment, norm(segment)));
      }
    };

    return {
      push: (preview, previewCues = null) => {
        if (stale()) return;
        if (Array.isArray(previewCues)) cues = previewCues;
        const full = String(preview || "");
        const consumed = spokenText.length + pending.length;
        if (full.length > consumed && full.startsWith(spokenText + pending)) {
          pending += full.slice(consumed);
          drain();
        }
      },
      finish: (finalText, finalOpts = {}, finalCues = null) => {
        if (stale()) return;
        if (finalOpts.emotion != null || finalOpts.intensity != null) voiceOpts = finalOpts;
        if (Array.isArray(finalCues)) cues = finalCues;
        const finalNorm = norm(finalText);
        const spokenNorm = norm(spokenText);
        const start = spokenText.length;
        if (finalNorm.startsWith(spokenNorm)) {
          const rest = finalNorm.slice(spokenNorm.length);
          enqueue(rest, cuesBetween(start, Infinity, rest, norm(rest)));
        } else if (!spokenNorm) {
          enqueue(finalNorm, cuesBetween(0, Infinity, finalNorm, finalNorm));
        } else {
          // The cleaned final text diverged from what was already spoken;
          // skip the tail rather than repeat or garble it, but still act.
          this._performNow(cues.filter((c) => c.at >= start));
        }
        pending = "";
      },
      cancel: () => {
        if (!stale()) this.stop();
      },
    };
  }
}
