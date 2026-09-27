// Public modular avatar system facade.
(function () {
  class SarahAvatarSystem {
    constructor(config = window.SARAH_AVATAR_CONFIG) {
      this.config = config;
      this.emotion = new window.SarahAvatarEmotionEngine(config);
      this.motion = new window.SarahAvatarMotionController(this.emotion, config);
      this.ai = new window.SarahAvatarAISyncController(this.emotion, this.motion);
      this.started = false;
    }

    start() {
      if (this.started) return this;
      this.started = true;
      this.motion.start();
      this.emotion.onChange((state) => {
        window.dispatchEvent(new CustomEvent("sarah-avatar-state", { detail: state }));
      });
      return this;
    }

    stop() {
      this.motion.stop();
      this.started = false;
    }

    registerExpression(name, recipe) {
      return this.emotion.registerExpression(name, recipe);
    }

    onUserMessage(text) {
      return this.ai.onUserMessage(text);
    }

    onAIResponse(text, meta = {}) {
      return this.ai.onAIResponse(text, meta);
    }

    // Issue #33: lets dashboard.js dispatch motion tags Sarah emits in
    // her replies (e.g. <motion>wave</motion>) directly into the rig.
    triggerGesture(name, amount = 0.8) {
      if (window.SARAH_3D_TEST?.visible) {
        return window.SARAH_3D_TEST.triggerGesture?.(name, amount) || false;
      }
      return this.motion?.triggerGesture?.(name, amount) || false;
    }

    listGestures() {
      const built = ["spin", "laugh", "blush", "surprise", "distant"];
      const recipes = Object.keys(this.config?.gestures || {});
      return [...built, ...recipes];
    }

    syncBackendMood(mood, avatarParams) {
      return this.ai.syncBackendMood(mood, avatarParams);
    }

    updateFromContext(messages, options = {}) {
      return this.emotion.updateFromContext(messages, options);
    }

    buildVisemeTimeline(text, options = {}) {
      return window.SARAH_AVATAR_LIPSYNC?.buildTimeline?.(text, options) || [];
    }

    getState() {
      return this.emotion.getState();
    }

    getDiagnostics() {
      return {
        version: this.config?.version,
        started: this.started,
        state: this.getState(),
        live2d: window.SARAH_LIVE2D?.getRigDiagnostics?.() || null,
        expressions: Object.keys(this.emotion.expressions),
      };
    }
  }

  window.SarahAvatarSystem = SarahAvatarSystem;
  window.SARAH_AVATAR_SYSTEM = window.SARAH_AVATAR_SYSTEM || new SarahAvatarSystem();

  const boot = () => window.SARAH_AVATAR_SYSTEM?.start?.();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
