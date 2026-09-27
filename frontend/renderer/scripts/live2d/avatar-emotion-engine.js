// Sentiment, tone, affinity, and expression state for Sarah's modular avatar.
(function () {
  function clamp(value, min = 0, max = 1, fallback = 0) {
    const n = Number(value);
    if (!Number.isFinite(n)) return fallback;
    return Math.max(min, Math.min(max, n));
  }

  function countMatches(text, list) {
    const lower = String(text || "").toLowerCase();
    let score = 0;
    for (const phrase of list || []) {
      if (lower.includes(phrase)) score += phrase.includes(" ") ? 2 : 1;
    }
    return score;
  }

  function lerp(from, to, t) {
    return from + (to - from) * t;
  }

  function smoothstep(t) {
    const x = clamp(t, 0, 1, 0);
    return x * x * (3 - 2 * x);
  }

  function nowMs() {
    return typeof performance !== "undefined" && typeof performance.now === "function"
      ? performance.now()
      : Date.now();
  }

  const EMOTION_PROFILE = {
    neutral: { valence: 0, arousal: 0.12 },
    happy: { valence: 0.72, arousal: 0.58 },
    sad: { valence: -0.64, arousal: 0.28 },
    angry: { valence: -0.78, arousal: 0.82 },
    embarrassed: { valence: 0.28, arousal: 0.72 },
    surprised: { valence: 0.18, arousal: 0.92 },
    loving: { valence: 0.9, arousal: 0.5 },
    confused: { valence: -0.16, arousal: 0.48 },
    smug: { valence: 0.38, arousal: 0.42 },
    tired: { valence: -0.34, arousal: 0.12 },
  };

  class SarahAvatarEmotionEngine {
    constructor(config = window.SARAH_AVATAR_CONFIG) {
      this.config = config;
      this.expressions = { ...(config?.expressions || {}) };
      this.listeners = new Set();
      this.inertia = null;
      this.inertiaFrame = null;
      this.state = {
        emotion: "neutral",
        baseEmotion: "neutral",
        displayEmotion: "neutral",
        baselineDisplayEmotion: "neutral",
        tone: "neutral",
        intensity: 0.15,
        baselineIntensity: 0.15,
        baseWarmth: 0.45,
        sentiment: 0,
        positiveRatio: 0,
        negativeRatio: 0,
        neutralRatio: 1,
        affinity: config?.affinity?.neutral || 0.55,
        volatility: 0,
        responsiveness: 0.5,
        baselineSource: "init",
        lastSource: "init",
        lastUpdated: nowMs(),
        history: [],
        contextMessageCount: 0,
        inertiaActive: false,
        inertiaProgress: 1,
        inertiaTargetEmotion: "neutral",
        inertiaAnticipationEmotion: null,
        inertiaDurationMs: 0,
        overlayActive: false,
        overlayEmotion: null,
        overlayIntensity: 0,
        overlayAlpha: 0,
        overlayProgress: 1,
        overlayDurationMs: 0,
        overlayElapsedMs: 0,
        overlayStartedAt: 0,
        overlaySource: null,
        overlaySentiment: 0,
      };
    }

    onChange(fn) {
      if (typeof fn !== "function") return () => {};
      this.listeners.add(fn);
      return () => this.listeners.delete(fn);
    }

    emit() {
      const snapshot = this.getState();
      for (const listener of this.listeners) {
        try { listener(snapshot); } catch (err) { console.warn("[AvatarEmotion] listener failed", err); }
      }
    }

    normalizeEmotion(emotion) {
      const raw = String(emotion || "neutral").toLowerCase().replace(/-/g, "_");
      return this.config?.aliases?.[raw] || raw;
    }

    registerExpression(name, recipe) {
      const key = this.normalizeEmotion(name);
      if (!key || !recipe || typeof recipe !== "object") return false;
      this.expressions[key] = { ...(this.expressions[key] || {}), ...recipe };
      return true;
    }

    getExpressionRecipe(emotion = this.state.emotion) {
      return this.expressions[this.normalizeEmotion(emotion)] || this.expressions.neutral;
    }

    analyzeSentiment(text) {
      const lex = this.config?.sentiment || {};
      const positive = countMatches(text, lex.positive);
      const negative = countMatches(text, lex.negative);
      const affection = countMatches(text, lex.affection);
      const surprise = countMatches(text, lex.surprise);
      const confusion = countMatches(text, lex.confusion);
      const tired = countMatches(text, lex.tired);
      const punctuationBoost = (String(text || "").match(/[!?]/g) || []).length;
      const sentiment = clamp((positive + affection * 1.5 - negative * 1.4) / 8, -1, 1, 0);
      let emotion = "neutral";
      let intensity = 0.2;

      if (affection > 0) {
        emotion = "loving";
        intensity = 0.62 + affection * 0.08;
      } else if (negative > positive + 1) {
        emotion = "sad";
        intensity = 0.48 + negative * 0.07;
      } else if (positive > negative) {
        emotion = "happy";
        intensity = 0.42 + positive * 0.06;
      }

      if (surprise > 0 || punctuationBoost >= 2) {
        emotion = positive >= negative ? "surprised" : emotion;
        intensity += 0.12;
      }
      if (confusion > 0) {
        emotion = "confused";
        intensity += 0.08;
      }
      if (tired > 0) {
        emotion = "tired";
        intensity += 0.1;
      }

      return {
        emotion: this.normalizeEmotion(emotion),
        intensity: clamp(intensity, 0.05, 1, 0.2),
        sentiment,
        affinityDelta: clamp((positive + affection * 2 - negative * 1.5) * 0.012, -0.05, 0.06, 0),
        tags: { positive, negative, affection, surprise, confusion, tired },
      };
    }

    scoreContextMessage(message) {
      const role = message?.role || "unknown";
      const content = String(message?.content || "");
      if (!content.trim() || role === "system") {
        return { role, sentiment: 0, positive: 0, negative: 0, affection: 0, engagement: 0 };
      }
      const analysis = this.analyzeSentiment(content);
      const tags = analysis.tags || {};
      const engagement = role === "user" ? Math.min(1, Math.max(0.1, content.length / 220)) : 0;
      return {
        role,
        sentiment: analysis.sentiment,
        positive: tags.positive || 0,
        negative: tags.negative || 0,
        affection: tags.affection || 0,
        engagement,
      };
    }

    computeBaselineFromContext(messages = [], options = {}) {
      const windowSize = Math.max(3, Number(options.windowSize || 40));
      const recentSize = Math.max(3, Number(options.recentSize || 5));
      const usable = (Array.isArray(messages) ? messages : [])
        .filter((msg) => msg && msg.role !== "system" && String(msg.content || "").trim())
        .slice(-windowSize);

      if (!usable.length) {
        return {
          baseEmotion: "neutral",
          baseWarmth: 0.45,
          affinityScore: this.config?.affinity?.neutral || 0.55,
          volatility: 0,
          responsiveness: 0.5,
          sentimentAverage: 0,
          positiveRatio: 0,
          negativeRatio: 0,
          neutralRatio: 1,
          contextMessageCount: 0,
        };
      }

      const scored = usable.map((msg) => this.scoreContextMessage(msg));
      const userScores = scored.filter((item) => item.role === "user");
      const sentimentAverage = scored.reduce((sum, item) => sum + item.sentiment, 0) / scored.length;
      const positiveCount = scored.filter((item) => item.sentiment > 0.08).length;
      const negativeCount = scored.filter((item) => item.sentiment < -0.08).length;
      const neutralCount = Math.max(0, scored.length - positiveCount - negativeCount);
      const positiveRatio = positiveCount / scored.length;
      const negativeRatio = negativeCount / scored.length;
      const neutralRatio = neutralCount / scored.length;

      const compliments = userScores.reduce((sum, item) => sum + item.positive + item.affection * 1.8, 0);
      const negativity = userScores.reduce((sum, item) => sum + item.negative, 0);
      const engagement = userScores.reduce((sum, item) => sum + item.engagement, 0) / Math.max(1, userScores.length);
      const affinityScore = clamp(
        0.5 + sentimentAverage * 0.28 + compliments * 0.025 - negativity * 0.035 + engagement * 0.12,
        0,
        1,
        0.5
      );

      const recent = scored.slice(-recentSize);
      const recentAverage = recent.reduce((sum, item) => sum + item.sentiment, 0) / Math.max(1, recent.length);
      const volatility = clamp(Math.abs(recentAverage - sentimentAverage), 0, 1, 0);
      const responsiveness = clamp(0.38 + volatility * 0.55 + affinityScore * 0.16 - negativeRatio * 0.18, 0.18, 1, 0.5);

      let baseEmotion = "neutral";
      if (positiveRatio > 0.6 && affinityScore > 0.5) baseEmotion = "happy";
      if (affinityScore > 0.7 && positiveRatio >= negativeRatio) baseEmotion = "loving";
      if (negativeRatio > 0.45 && negativeRatio > positiveRatio) baseEmotion = "tired";
      if (sentimentAverage < -0.35) baseEmotion = "sad";
      if (volatility > 0.38 && recentAverage > sentimentAverage) baseEmotion = "surprised";
      if (neutralRatio > 0.7 && Math.abs(sentimentAverage) < 0.12) baseEmotion = "neutral";

      const baseWarmth = clamp(0.36 + affinityScore * 0.52 + positiveRatio * 0.18 - negativeRatio * 0.24, 0.08, 1, 0.45);

      return {
        baseEmotion: this.normalizeEmotion(baseEmotion),
        baseWarmth,
        affinityScore,
        volatility,
        responsiveness,
        sentimentAverage,
        positiveRatio,
        negativeRatio,
        neutralRatio,
        contextMessageCount: usable.length,
      };
    }

    getEmotionDistance(fromEmotion, toEmotion) {
      const from = EMOTION_PROFILE[this.normalizeEmotion(fromEmotion)] || EMOTION_PROFILE.neutral;
      const to = EMOTION_PROFILE[this.normalizeEmotion(toEmotion)] || EMOTION_PROFILE.neutral;
      const valence = from.valence - to.valence;
      const arousal = from.arousal - to.arousal;
      return clamp(Math.sqrt(valence * valence + arousal * arousal) / 1.75, 0, 1, 0);
    }

    baselineToState(baseline, options = {}) {
      const reactionBoost = clamp(baseline.volatility * 0.9, 0, 0.45, 0);
      const baselineIntensity = clamp(0.18 + baseline.baseWarmth * 0.24 + reactionBoost, 0.12, 0.8, 0.25);

      return {
        baseEmotion: baseline.baseEmotion,
        emotion: baseline.baseEmotion,
        displayEmotion: baseline.baseEmotion,
        intensity: baselineIntensity,
        baseWarmth: baseline.baseWarmth,
        sentiment: baseline.sentimentAverage,
        positiveRatio: baseline.positiveRatio,
        negativeRatio: baseline.negativeRatio,
        neutralRatio: baseline.neutralRatio,
        affinity: baseline.affinityScore,
        volatility: baseline.volatility,
        responsiveness: baseline.responsiveness,
        contextMessageCount: baseline.contextMessageCount,
        lastSource: options.source || "context-window",
      };
    }

    extractTransitionState() {
      return {
        baseEmotion: this.state.baseEmotion,
        emotion: this.state.baselineDisplayEmotion || this.state.baseEmotion,
        displayEmotion: this.state.baselineDisplayEmotion || this.state.baseEmotion,
        intensity: this.state.baselineIntensity,
        baseWarmth: this.state.baseWarmth,
        sentiment: this.state.sentiment,
        positiveRatio: this.state.positiveRatio,
        negativeRatio: this.state.negativeRatio,
        neutralRatio: this.state.neutralRatio,
        affinity: this.state.affinity,
        volatility: this.state.volatility,
        responsiveness: this.state.responsiveness,
        contextMessageCount: this.state.contextMessageCount,
        lastSource: this.state.baselineSource || this.state.lastSource,
      };
    }

    resolveVisibleState(timestamp = nowMs()) {
      const baselineEmotion = this.state.baselineDisplayEmotion || this.state.baseEmotion || "neutral";
      const baselineIntensity = clamp(this.state.baselineIntensity, 0, 1, 0.15);
      const overlayActive = Boolean(this.state.overlayActive && this.state.overlayEmotion && this.state.overlayAlpha > 0.02);

      if (overlayActive) {
        const alpha = clamp(this.state.overlayAlpha, 0, 1, 0);
        this.state.emotion = alpha > 0.14 ? this.state.overlayEmotion : baselineEmotion;
        this.state.displayEmotion = this.state.emotion;
        this.state.intensity = clamp(
          lerp(baselineIntensity, Math.max(baselineIntensity, this.state.overlayIntensity), alpha),
          0,
          1,
          baselineIntensity
        );
        this.state.lastSource = this.state.overlaySource || this.state.lastSource;
      } else {
        this.state.emotion = baselineEmotion;
        this.state.displayEmotion = baselineEmotion;
        this.state.intensity = baselineIntensity;
        this.state.lastSource = this.state.baselineSource || this.state.lastSource;
      }
      this.state.lastUpdated = timestamp;
    }

    applyBaselineState(snapshot, shouldEmit = true) {
      this.state.baseEmotion = snapshot.baseEmotion;
      this.state.baselineDisplayEmotion = snapshot.displayEmotion || snapshot.emotion || snapshot.baseEmotion;
      this.state.baselineIntensity = clamp(snapshot.intensity, 0, 1, 0.25);
      this.state.baseWarmth = clamp(snapshot.baseWarmth, 0, 1, 0.45);
      this.state.sentiment = clamp(snapshot.sentiment, -1, 1, 0);
      this.state.positiveRatio = clamp(snapshot.positiveRatio, 0, 1, 0);
      this.state.negativeRatio = clamp(snapshot.negativeRatio, 0, 1, 0);
      this.state.neutralRatio = clamp(snapshot.neutralRatio, 0, 1, 1);
      this.state.affinity = clamp(snapshot.affinity, 0, 1, this.config?.affinity?.neutral || 0.55);
      this.state.volatility = clamp(snapshot.volatility, 0, 1, 0);
      this.state.responsiveness = clamp(snapshot.responsiveness, 0, 1, 0.5);
      this.state.contextMessageCount = Math.max(0, Number(snapshot.contextMessageCount || 0));
      this.state.baselineSource = snapshot.lastSource || "context-window";
      this.state.inertiaActive = false;
      this.state.inertiaProgress = 1;
      this.state.inertiaTargetEmotion = snapshot.baseEmotion;
      this.state.inertiaAnticipationEmotion = null;
      this.state.inertiaDurationMs = 0;
      this.resolveVisibleState();
      if (shouldEmit) this.emit();
      return this.getState();
    }

    cancelInertia() {
      if (this.inertiaFrame && typeof cancelAnimationFrame === "function") {
        cancelAnimationFrame(this.inertiaFrame);
      }
      this.inertiaFrame = null;
      this.inertia = null;
      this.state.inertiaActive = false;
      this.state.inertiaProgress = 1;
      this.state.inertiaAnticipationEmotion = null;
      this.state.inertiaDurationMs = 0;
    }

    calculateInertiaMetrics(from, target) {
      const emotionShift = this.getEmotionDistance(from.emotion || from.baseEmotion, target.baseEmotion);
      const sentimentShift = Math.abs((target.sentiment || 0) - (from.sentiment || 0)) * 0.5;
      const warmthShift = Math.abs((target.baseWarmth || 0) - (from.baseWarmth || 0));
      const affinityShift = Math.abs((target.affinity || 0) - (from.affinity || 0));
      const intensityShift = Math.abs((target.intensity || 0) - (from.intensity || 0));
      const distance = clamp(Math.max(emotionShift, sentimentShift, warmthShift, affinityShift, intensityShift), 0, 1, 0);
      const responsiveness = clamp(target.responsiveness, 0, 1, 0.5);
      const speedFactor = 1.12 - responsiveness * 0.24;
      const durationMs = Math.round(clamp((300 + distance * 430 + target.volatility * 260) * speedFactor, 300, 800, 460));
      const overshoot = distance > 0.42 || target.volatility > 0.32
        ? clamp(0.035 + distance * 0.08 + target.volatility * 0.06, 0, 0.14, 0)
        : 0;
      return { distance, durationMs, overshoot };
    }

    pickAnticipationEmotion(from, target, distance) {
      if (distance < 0.38 && target.volatility < 0.32) return null;
      const targetEmotion = this.normalizeEmotion(target.baseEmotion);
      const fromEmotion = this.normalizeEmotion(from.emotion || from.baseEmotion);
      if (targetEmotion === fromEmotion) return null;
      if (targetEmotion === "happy" || targetEmotion === "loving" || targetEmotion === "embarrassed") return "surprised";
      if (targetEmotion === "sad" || targetEmotion === "tired") return "confused";
      if (targetEmotion === "angry") return "surprised";
      if (targetEmotion === "smug") return "confused";
      if (targetEmotion === "neutral" && (fromEmotion === "sad" || fromEmotion === "angry" || fromEmotion === "tired")) return "confused";
      return "surprised";
    }

    dampedSpring(t, overshoot = 0) {
      const x = clamp(t, 0, 1, 0);
      if (x >= 1) return 1;
      const zeta = overshoot > 0 ? 0.58 : 0.86;
      const omega = overshoot > 0 ? 10.5 : 8.5;
      const root = Math.sqrt(Math.max(0.001, 1 - zeta * zeta));
      const damped = omega * root;
      const value = 1 - Math.exp(-zeta * omega * x) * (Math.cos(damped * x) + (zeta / root) * Math.sin(damped * x));
      return clamp(value, 0, 1 + overshoot, 0);
    }

    interpolateBaselineState(from, target, spring, visualEmotion) {
      return {
        baseEmotion: target.baseEmotion,
        emotion: visualEmotion || target.baseEmotion,
        displayEmotion: visualEmotion || target.baseEmotion,
        intensity: clamp(lerp(from.intensity, target.intensity, spring), 0, 1, 0.25),
        baseWarmth: clamp(lerp(from.baseWarmth, target.baseWarmth, spring), 0, 1, 0.45),
        sentiment: clamp(lerp(from.sentiment, target.sentiment, spring), -1, 1, 0),
        positiveRatio: clamp(lerp(from.positiveRatio, target.positiveRatio, spring), 0, 1, 0),
        negativeRatio: clamp(lerp(from.negativeRatio, target.negativeRatio, spring), 0, 1, 0),
        neutralRatio: clamp(lerp(from.neutralRatio, target.neutralRatio, spring), 0, 1, 1),
        affinity: clamp(lerp(from.affinity, target.affinity, spring), 0, 1, this.config?.affinity?.neutral || 0.55),
        volatility: clamp(lerp(from.volatility, target.volatility, spring), 0, 1, 0),
        responsiveness: clamp(lerp(from.responsiveness, target.responsiveness, spring), 0, 1, 0.5),
        contextMessageCount: target.contextMessageCount,
        lastSource: target.lastSource,
      };
    }

    startContextInertiaTransition(target, options = {}) {
      const from = this.extractTransitionState();
      this.cancelInertia();

      if (options.immediate || typeof requestAnimationFrame !== "function") {
        return this.applyBaselineState(target, true);
      }

      const metrics = this.calculateInertiaMetrics(from, target);
      if (metrics.distance < 0.025) {
        return this.applyBaselineState(target, true);
      }

      const anticipationEmotion = this.pickAnticipationEmotion(from, target, metrics.distance);
      const start = nowMs();
      const anticipationShare = anticipationEmotion ? 0.2 : 0;
      this.inertia = {
        from,
        target,
        start,
        durationMs: metrics.durationMs,
        overshoot: metrics.overshoot,
        anticipationEmotion,
      };

      this.state.baseEmotion = target.baseEmotion;
      this.state.contextMessageCount = target.contextMessageCount;
      this.state.baselineSource = target.lastSource;
      this.state.lastUpdated = start;
      this.state.inertiaActive = true;
      this.state.inertiaProgress = 0;
      this.state.inertiaTargetEmotion = target.baseEmotion;
      this.state.inertiaAnticipationEmotion = anticipationEmotion;
      this.state.inertiaDurationMs = metrics.durationMs;
      this.resolveVisibleState(start);
      this.emit();

      const step = (timestamp) => {
        if (!this.inertia) return;
        const active = this.inertia;
        const rawProgress = clamp((timestamp - active.start) / active.durationMs, 0, 1, 0);
        let visualEmotion = target.baseEmotion;
        let springProgress = rawProgress;

        if (active.anticipationEmotion && rawProgress < anticipationShare) {
          const anticipationProgress = clamp(rawProgress / anticipationShare, 0, 1, 0);
          visualEmotion = active.anticipationEmotion;
          springProgress = anticipationProgress * 0.16;
        } else if (active.anticipationEmotion) {
          springProgress = clamp((rawProgress - anticipationShare) / (1 - anticipationShare), 0, 1, 0);
          const eased = this.dampedSpring(springProgress, active.overshoot);
          visualEmotion = eased < 0.52 ? active.anticipationEmotion : target.baseEmotion;
          springProgress = eased;
        } else {
          springProgress = this.dampedSpring(springProgress, active.overshoot);
        }

        const next = this.interpolateBaselineState(active.from, active.target, springProgress, visualEmotion);
        this.state.baseEmotion = next.baseEmotion;
        this.state.baselineDisplayEmotion = next.displayEmotion || next.emotion || next.baseEmotion;
        this.state.baselineIntensity = next.intensity;
        this.state.baseWarmth = next.baseWarmth;
        this.state.sentiment = next.sentiment;
        this.state.positiveRatio = next.positiveRatio;
        this.state.negativeRatio = next.negativeRatio;
        this.state.neutralRatio = next.neutralRatio;
        this.state.affinity = next.affinity;
        this.state.volatility = next.volatility;
        this.state.responsiveness = next.responsiveness;
        this.state.contextMessageCount = next.contextMessageCount;
        this.state.baselineSource = next.lastSource;
        this.state.inertiaActive = rawProgress < 1;
        this.state.inertiaProgress = rawProgress;
        this.state.inertiaTargetEmotion = active.target.baseEmotion;
        this.state.inertiaAnticipationEmotion = active.anticipationEmotion;
        this.state.inertiaDurationMs = active.durationMs;
        this.resolveVisibleState(timestamp);
        this.emit();

        if (rawProgress >= 1) {
          this.inertiaFrame = null;
          this.inertia = null;
          this.applyBaselineState(active.target, true);
          return;
        }
        this.inertiaFrame = requestAnimationFrame(step);
      };

      this.inertiaFrame = requestAnimationFrame(step);
      return this.getState();
    }

    updateFromContext(messages = [], options = {}) {
      const baseline = this.computeBaselineFromContext(messages, options);
      const target = this.baselineToState(baseline, options);
      return this.startContextInertiaTransition(target, options);
    }

    updateFromContextImmediate(messages = [], options = {}) {
      return this.updateFromContext(messages, { ...options, immediate: true });
    }

    analyzeTone(text) {
      const lower = String(text || "").toLowerCase();
      let tone = "serious";
      let confidence = 0.35;
      if (/\b(lol|haha|hehe|fun|play|joke|silly)\b/.test(lower)) { tone = "playful"; confidence = 0.7; }
      if (/\b(tease|teasing|smirk|you know|come on)\b/.test(lower)) { tone = "teasing"; confidence = 0.7; }
      if (/\b(love|sweet|dear|proud|care|miss you|good girl)\b/.test(lower)) { tone = "affectionate"; confidence = 0.78; }
      if (/\b(sorry|sad|hurt|lonely|tired|hard day)\b/.test(lower)) { tone = "sad"; confidence = 0.66; }
      if ((lower.match(/!/g) || []).length >= 2 || /\b(excited|amazing|awesome|perfect)\b/.test(lower)) { tone = "excited"; confidence = 0.75; }
      return { tone, confidence, emotion: this.config?.toneToEmotion?.[tone] || "neutral" };
    }

    calculateOverlayDuration(emotion, intensity, meta = {}) {
      const explicit = Number(meta.overlayDurationMs ?? meta.durationMs);
      if (Number.isFinite(explicit)) return Math.round(clamp(explicit, 1000, 3000, 1800));
      const shift = this.getEmotionDistance(this.state.baselineDisplayEmotion || this.state.baseEmotion, emotion);
      return Math.round(clamp(1000 + intensity * 850 + shift * 850, 1000, 3000, 1800));
    }

    setEmotion(emotion, intensity = this.config?.defaultIntensity || 0.35, meta = {}) {
      const normalized = this.normalizeEmotion(emotion);
      const nextIntensity = clamp(intensity, 0, 1, 0.25);
      const durationMs = this.calculateOverlayDuration(normalized, nextIntensity, meta);
      const timestamp = nowMs();
      this.state.overlayActive = true;
      this.state.overlayEmotion = normalized;
      this.state.overlayIntensity = nextIntensity;
      this.state.overlayAlpha = 1;
      this.state.overlayProgress = 0;
      this.state.overlayDurationMs = durationMs;
      this.state.overlayElapsedMs = 0;
      this.state.overlayStartedAt = timestamp;
      this.state.overlaySource = meta.source || "manual";
      this.state.overlaySentiment = typeof meta.sentiment === "number" ? clamp(meta.sentiment, -1, 1, 0) : this.state.overlaySentiment;
      this.state.tone = meta.tone || this.state.tone || "neutral";
      this.resolveVisibleState(timestamp);
      this.state.history.push({
        emotion: normalized,
        intensity: nextIntensity,
        source: this.state.overlaySource,
        overlay: true,
        durationMs,
        ts: Date.now(),
      });
      if (this.state.history.length > 40) this.state.history.shift();
      this.emit();
      return this.getState();
    }

    clearOverlay(shouldEmit = true) {
      this.state.overlayActive = false;
      this.state.overlayEmotion = null;
      this.state.overlayIntensity = 0;
      this.state.overlayAlpha = 0;
      this.state.overlayProgress = 1;
      this.state.overlayDurationMs = 0;
      this.state.overlayElapsedMs = 0;
      this.state.overlayStartedAt = 0;
      this.state.overlaySource = null;
      this.resolveVisibleState();
      if (shouldEmit) this.emit();
      return this.getState();
    }

    applyAffinityDelta(delta, shouldEmit = true) {
      // Affinity is derived from the active AI context window. This method is
      // retained for compatibility but deliberately does not persist or mutate it.
      if (shouldEmit) this.emit();
      return this.state.affinity;
    }

    applyUserMessage(text) {
      const result = this.analyzeSentiment(text);
      return this.setEmotion(result.emotion, result.intensity, {
        source: "user-message",
        sentiment: result.sentiment,
        affinityDelta: result.affinityDelta,
      });
    }

    applyAIResponse(text, meta = {}) {
      const tone = this.analyzeTone(text);
      const sentiment = this.analyzeSentiment(text);
      const emotion = meta.emotion ? this.normalizeEmotion(meta.emotion) : tone.emotion;
      const intensity = clamp(meta.intensity ?? Math.max(sentiment.intensity * 0.82, tone.confidence * 0.7), 0.1, 1, 0.35);
      return this.setEmotion(emotion, intensity, {
        source: meta.source || "ai-response",
        tone: tone.tone,
        sentiment: sentiment.sentiment,
        affinityDelta: sentiment.affinityDelta * 0.35,
      });
    }

    syncBackendMood(mood = {}) {
      const emotion = this.normalizeEmotion(mood.emotion || "neutral");
      return this.setEmotion(emotion, mood.intensity ?? this.state.intensity, { source: "backend-mood" });
    }

    decay(dtSeconds = 0.016) {
      // Baseline has no timer-based drift. Only the short-lived reaction
      // overlay fades here so instant chat reactions return to context state.
      if (this.state.overlayActive) {
        const duration = Math.max(1, Number(this.state.overlayDurationMs || 0));
        const wallElapsed = this.state.overlayStartedAt ? nowMs() - this.state.overlayStartedAt : 0;
        const tickElapsed = this.state.overlayElapsedMs + Math.max(0, dtSeconds) * 1000;
        this.state.overlayElapsedMs = Math.min(duration, Math.max(wallElapsed, tickElapsed));
        const progress = clamp(this.state.overlayElapsedMs / duration, 0, 1, 0);
        const holdShare = 0.16;
        const fadeProgress = progress <= holdShare ? 0 : (progress - holdShare) / (1 - holdShare);
        this.state.overlayProgress = progress;
        this.state.overlayAlpha = progress >= 1 ? 0 : 1 - smoothstep(fadeProgress);

        if (progress >= 1 || this.state.overlayAlpha <= 0.02) {
          return this.clearOverlay(true);
        }
        this.resolveVisibleState();
        this.emit();
      }
      return this.getState();
    }

    getAffinityBand() {
      if (this.state.affinity >= (this.config?.affinity?.high || 0.75)) return "high";
      if (this.state.affinity <= (this.config?.affinity?.low || 0.25)) return "low";
      return "normal";
    }

    getState() {
      return {
        ...this.state,
        affinityBand: this.getAffinityBand(),
        expression: this.getExpressionRecipe(this.state.emotion),
      };
    }
  }

  window.SarahAvatarEmotionEngine = SarahAvatarEmotionEngine;
})();
