// Procedural Live2D motion layers driven by emotion and affinity.
(function () {
  function clamp(value, min = 0, max = 1, fallback = 0) {
    const n = Number(value);
    if (!Number.isFinite(n)) return fallback;
    return Math.max(min, Math.min(max, n));
  }

  function easeMapped(key, value, speed = 0.18) {
    window.SARAH_LIVE2D?.easeMappedParameter?.(key, value, speed);
  }

  function easeId(id, value, speed = 0.18) {
    window.SARAH_LIVE2D?.easeParameter?.(id, value, speed);
  }

  // Direct (non-eased) writes — used inside the `beforeModelUpdate` hook
  // for gesture playback. easeParameter blends from the motion's
  // freshly-written value, so it can't reach the target while motion is
  // running every frame. The gesture's weight (fade-in/fade-out) provides
  // the apparent easing curve already, so we just write `value * weight`.
  function setId(id, value) {
    window.SARAH_LIVE2D?.setParameter?.(id, value, 1);
  }
  function setMapped(key, value) {
    window.SARAH_LIVE2D?.setMappedParameter?.(key, value, 1);
  }

  class SarahAvatarMotionController {
    constructor(engine, config = window.SARAH_AVATAR_CONFIG) {
      this.engine = engine;
      this.config = config;
      this.running = false;
      this.raf = null;
      this.lastTime = 0;
      this.phase = { breath: 0, idle: 0, hair: 0, heart: 0 };
      this.cursor = { x: 0, y: 0, active: false, lastMove: 0 };
      this.nextMicroAt = performance.now() + 1600;
      this.container = null;
      // Gesture-active timestamp (perf-now ms). While set in the future,
      // the tick loop's body/head sway is suppressed so a triggered gesture
      // can hold its pose without being overwritten frame-by-frame.
      this.gestureLockUntil = 0;
      // Active gestures driven per-frame from tick(). A single easeParam
      // call only moves ~22% toward target before Lisette's idle motion
      // overwrites the param next frame; gestures must be re-applied every
      // frame for the hold window to actually become visible.
      this.activeGestures = [];
    }

    attach(container = document.getElementById("avatar-container")) {
      if (!container || this.container === container) return;
      this.container = container;
      container.addEventListener("mousemove", (ev) => {
        const r = container.getBoundingClientRect();
        this.cursor.x = ((ev.clientX - r.left) / Math.max(1, r.width) - 0.5) * 2;
        this.cursor.y = ((ev.clientY - r.top) / Math.max(1, r.height) - 0.5) * 2;
        this.cursor.active = true;
        this.cursor.lastMove = performance.now();
      });
      container.addEventListener("mouseleave", () => {
        this.cursor.active = false;
      });
    }

    start() {
      if (this.running) return;
      this.running = true;
      this.lastTime = performance.now();
      this.attach();
      const tick = (now) => {
        if (!this.running) return;
        const dt = Math.min(0.05, Math.max(0.001, (now - this.lastTime) / 1000));
        this.lastTime = now;
        this.tick(dt, now);
        this.raf = requestAnimationFrame(tick);
      };
      this.raf = requestAnimationFrame(tick);
    }

    stop() {
      this.running = false;
      if (this.raf) cancelAnimationFrame(this.raf);
      this.raf = null;
    }

    triggerGesture(name, amount = 1) {
      const strength = clamp(amount, 0, 1, 0.5);

      // Issue #32 follow-up: Lisette ships full multi-param motion files
      // (.motion3.json) for hello/happy/sad/angry/shy/jump/run. A timeline
      // animation looks far better than a single-param ease — e.g. "spin"
      // can't be a single ParamBodyAngleY twist, it needs a multi-keyframe
      // animation. So when a gesture name maps to a registered motion
      // group, play the file; fall back to the param recipe only when
      // there's no motion file (point, lean_in, nod, etc.).
      const motionMap = {
        wave: "Hello",
        jump: "Jump",
        laugh: "Happy",
        distant: "Sad",
        blush: "Shy",
      };
      const motionGroup = motionMap[name];
      if (motionGroup && window.SARAH_LIVE2D?.playMotion) {
        window.SARAH_LIVE2D.playMotion(motionGroup, 0, 3);
        if (name === "blush") window.SARAH_LIVE2D.setExpression?.("shy");
        if (name === "laugh") {
          easeMapped("bodyX", 4 * strength, 0.32);
          setTimeout(() => easeMapped("bodyX", 0, 0.2), 260);
        }
        return true;
      }

      if (name === "surprise") {
        const now = performance.now();
        this.activeGestures.push({
          name: "surprise",
          params: [
            { id: "bodyY", value: -6 * strength },
            { id: "mouthOpen", value: 0.7 * strength },
          ],
          startedAt: now,
          hold: 600,
          fadeIn: 80,
          fadeOut: 320,
        });
        this.gestureLockUntil = now + 1100;
        return true;
      }

      // 2D rigs can't truly rotate 360°, so a "spin" reads as a brisk
      // twist-one-way → twist-the-other → snap back. The visible axis is
      // YAW: ParamAngleX + ParamBodyAngleX, plus roll to shift weight.
      //
      // The applier is last-write-wins, so overlapping phases fight. Queue
      // both phases now, but future-start phase 2 so it cannot be lost to
      // timer ordering and cannot write until phase 1 has fully faded out.
      if (name === "spin") {
        const now0 = performance.now();
        const amp = 0.9 + strength * 0.25;
        const buildPhase = (name, sign, amount = 1) => ({
          name,
          params: [
            { id: "ParamAngleX", value: 30 * sign * amp * amount },
            { id: "ParamAngleY", value: 10 * sign * amp * amount },
            { id: "ParamAngleZ", value: 16 * sign * amp * amount },
            { id: "ParamBodyAngleX", value: 20 * sign * amp * amount },
            { id: "ParamBodyAngleY", value: 13 * sign * amp * amount },
            { id: "ParamBodyAngleY2", value: 13 * sign * amp * amount },
            { id: "ParamBodyAngleZ", value: 16 * sign * amp * amount },
            { id: "ParamBodyAngleZ2", value: 16 * sign * amp * amount },
            { id: "ParamBodyAngleZ3", value: 10 * sign * amp * amount },
            // ParamLeg*Step range is 0..10 (no negative) — keep both planted at
            // a mid stance during the twirl; ParamWalking range is 0..16.
            { id: "ParamLegRStep", value: 5 * amp * amount },
            { id: "ParamLegLStep", value: 5 * amp * amount },
            { id: "ParamWalking", value: 14 * amp * amount },
            { id: "ParamWalkToggle", value: 1 },
          ],
          hold: 390,
          fadeIn: 130,
          fadeOut: 170,
        });

        const phaseGap = 55;
        const windup = {
          ...buildPhase("spin_windup", -1, 0.45),
          startedAt: now0,
          hold: 210,
          fadeIn: 90,
          fadeOut: 90,
        };
        const windupTotal = windup.hold + windup.fadeOut;
        const phase1 = {
          ...buildPhase("spin_right", 1, 1),
          startedAt: now0 + windupTotal + phaseGap,
        };
        const phase1Total = phase1.hold + phase1.fadeOut;
        const phase2 = {
          ...buildPhase("spin_left", -1, 1),
          startedAt: phase1.startedAt + phase1Total + phaseGap,
        };
        const fullDuration = phase2.startedAt + phase1Total - now0;
        const poseFadeOut = 180;
        const pose = {
          name: "spin_pose",
          params: [
            { id: "ParamArmRMove", value: -0.85 * amp },
            { id: "ParamArmLMove2", value: 0.85 * amp },
            { id: "ParamLowerArmRMove", value: 0.45 * amp },
            { id: "ParamLowerArmLMove", value: 0.45 * amp },
            { id: "ParamHandRMove", value: 0.55 * amp },
            { id: "ParamHandLMove", value: 0.55 * amp },
            { id: "ParamFingerRelaxR", value: 0.8 * amp },
            { id: "ParamFingerRelaxL", value: 0.8 * amp },
            { id: "ParamJump", value: 0.2 * amp },
            { id: "ParamWalkToggle", value: 1 },
          ],
          startedAt: now0,
          hold: fullDuration - poseFadeOut,
          fadeIn: 120,
          fadeOut: poseFadeOut,
        };
        this.activeGestures.push(pose);
        this.activeGestures.push(windup);
        this.activeGestures.push(phase1);
        this.activeGestures.push(phase2);

        this.gestureLockUntil = phase2.startedAt + phase1Total + 200;
        console.log(
          "[Sarah/Gesture] trigger spin (in-rig twirl) — phase1Total=",
          phase1Total
        );
        return true;
      }

      return this.triggerBodyGesture(name, strength);
    }

    // Issue #32: dispatch named body-gesture recipes from config.gestures.
    // Each recipe is a list of {id, value} that we ease in, hold for the
    // recipe duration, then ease back to 0. easeParameter no-ops for
    // unknown ids, so unrigged params are safely skipped on the current
    // ganyu face-rig — drop in a body-rigged model and the same call
    // animates the new limbs without code changes.
    triggerBodyGesture(name, strength = 0.7) {
      const recipe = this.config?.gestures?.[name];
      if (!recipe) return false;
      const amp = clamp(strength, 0, 1, 0.7);
      const hold = Math.max(300, Number(recipe.durationMs || 900));
      const fadeIn = 180;
      const fadeOut = 320;
      const now = performance.now();
      const params = (recipe.params || []).map((s) => ({
        id: s.id,
        value: Number(s.value || 0) * amp,
      }));
      this.activeGestures.push({
        name,
        params,
        startedAt: now,
        hold,
        fadeIn,
        fadeOut,
      });
      this.gestureLockUntil = now + hold + fadeOut + 100;
      console.log("[Sarah/Gesture] trigger", name, "params=", params.length, "hold=", hold);
      return true;
    }

    // Per-frame applier — called from tick(). For each active gesture,
    // compute a 0..1 weight based on time-in-gesture (fade-in → hold →
    // fade-out) and ease every param toward `value * weight`. This wins
    // over Lisette's idle motion because the user-side param write happens
    // every frame, after motion update.
    applyActiveGestures(now) {
      if (!this.activeGestures.length) return;
      const remaining = [];
      for (const g of this.activeGestures) {
        const t = now - g.startedAt;
        // Future-start entries let multi-phase gestures stay in one frame-applied queue.
        if (t <= 0) {
          remaining.push(g);
          continue;
        }
        const total = g.hold + g.fadeOut;
        let weight;
        if (t < g.fadeIn) weight = t / g.fadeIn;
        else if (t < g.hold) weight = 1;
        else if (t < total) weight = Math.max(0, 1 - (t - g.hold) / g.fadeOut);
        else weight = 0;

        if (weight > 0) {
          for (const step of g.params) {
            const target = step.value * weight;
            if (step.id?.startsWith("Param")) setId(step.id, target);
            else setMapped(step.id, target);
          }
          remaining.push(g);
        }
      }
      this.activeGestures = remaining;
    }

    applyMoodLighting(state) {
      const el = this.container || document.getElementById("avatar-container");
      if (!el) return;
      el.classList.remove("avatar-mood-happy", "avatar-mood-sad", "avatar-mood-angry", "avatar-mood-loving", "avatar-mood-tired");
      if (state.emotion === "happy" || state.emotion === "surprised") el.classList.add("avatar-mood-happy");
      if (state.emotion === "sad" || state.affinityBand === "low") el.classList.add("avatar-mood-sad");
      if (state.emotion === "angry") el.classList.add("avatar-mood-angry");
      if (state.emotion === "loving" || state.emotion === "embarrassed") el.classList.add("avatar-mood-loving");
      if (state.emotion === "tired") el.classList.add("avatar-mood-tired");
    }

    tick(dt, now) {
      this.engine?.decay?.(dt);
      const state = this.engine?.getState?.() || { emotion: "neutral", intensity: 0.15, affinity: 0.55, expression: {} };
      const recipe = state.expression || {};
      const motion = recipe.motion || {};
      const intensity = clamp(state.intensity, 0, 1, 0.2);
      const affinity = clamp(state.affinity, 0, 1, 0.55);
      const warm = clamp(motion.warmth ?? 0.4, 0, 1, 0.4);
      const lowAffinity = state.affinityBand === "low";
      const highAffinity = state.affinityBand === "high";

      this.phase.breath += dt * (0.65 + intensity * 0.75 + (highAffinity ? 0.2 : 0) - (lowAffinity ? 0.18 : 0));
      this.phase.idle += dt * (0.32 + intensity * 0.24) * (motion.sway || 1);
      this.phase.hair += dt * (0.75 + intensity * 0.85);
      this.phase.heart += dt * (0.8 + intensity * 1.2 + warm * 0.5);

      const breath = (Math.sin(this.phase.breath * Math.PI * 2) + 1) * 0.5;
      easeMapped("breath", breath * (0.58 + intensity * 0.35), 0.12);

      // Active gestures are NOT applied here — they're applied from the
      // model's `beforeModelUpdate` event in live2d-sarah.js, which fires
      // after Lisette's motion update has run. Writing gesture params here
      // (in the standalone RAF loop, before the motion update) gets the
      // user write stomped by the idle hand-fiddle motion overriding
      // ParamArmRMove / ParamHandRMove every frame.

      const gestureActive = now < this.gestureLockUntil;

      if (!gestureActive) {
        const swayAmp = (lowAffinity ? 1.2 : 2.2 + affinity * 1.8) * (motion.sway || 1);
        easeMapped("bodyZ", Math.sin(this.phase.idle) * swayAmp + (motion.bodyLean || 0) * intensity, 0.12);
        easeMapped("bodyY", Math.sin(this.phase.idle * 0.72) * swayAmp * 0.6, 0.12);
        easeMapped("headZ", (motion.headTilt || 0) * intensity + Math.sin(this.phase.idle * 0.55) * 1.3, 0.12);
      }

      const cursorRecent = this.cursor.active && now - this.cursor.lastMove < 2600;
      const eyeContact = lowAffinity ? 0.28 : highAffinity ? 0.95 : 0.72;
      if (cursorRecent && !gestureActive) {
        easeMapped("eyeX", this.cursor.x * eyeContact, 0.16);
        easeMapped("eyeY", -this.cursor.y * eyeContact * 0.55, 0.16);
        easeMapped("headX", this.cursor.x * 5 * eyeContact, 0.12);
        easeMapped("headY", -this.cursor.y * 4 * eyeContact, 0.12);
      }

      if (!gestureActive) {
        const params = recipe.params || {};
        for (const [key, value] of Object.entries(params)) {
          const scaled = key === "mouthOpen" ? value * (0.5 + intensity * 0.7) : value * Math.max(0.35, intensity);
          easeMapped(key, scaled, 0.16);
        }
      }

      const hairScale = intensity * (state.emotion === "angry" || state.emotion === "surprised" ? 1.1 : 0.55);
      const hairIds = (this.config?.motion?.hairParamIds || []).slice(0, this.config?.motion?.maxHairParams || 8);
      hairIds.forEach((id, idx) => {
        const wave = Math.sin(this.phase.hair + idx * 0.7) * hairScale * (idx % 2 === 0 ? 1 : -1);
        easeId(id, wave, 0.08);
      });

      if (intensity > 0.72 && (state.emotion === "loving" || state.emotion === "embarrassed" || state.emotion === "happy")) {
        const beat = Math.max(0, Math.sin(this.phase.heart * Math.PI * 2));
        easeMapped("bodyY", beat * 2.2 * intensity, 0.1);
      }

      if (now >= this.nextMicroAt) {
        this.runMicroGesture(state);
        this.nextMicroAt = now + 1400 + Math.random() * 3200;
      }

      this.applyMoodLighting(state);
    }

    runMicroGesture(state) {
      const intensity = clamp(state.intensity, 0, 1, 0.25);
      if (state.affinityBand === "low") {
        if (Math.random() < 0.35) this.triggerGesture("distant", 0.45);
        return;
      }
      if (state.emotion === "happy" && Math.random() < 0.45) this.triggerGesture("laugh", intensity);
      if ((state.emotion === "loving" || state.emotion === "embarrassed") && Math.random() < 0.55) this.triggerGesture("blush", intensity);
      if (state.emotion === "surprised" && Math.random() < 0.45) this.triggerGesture("surprise", intensity);
    }
  }

  window.SarahAvatarMotionController = SarahAvatarMotionController;
})();
