// Chat and AI-response synchronization for Sarah's modular avatar.
(function () {
  class SarahAvatarAISyncController {
    constructor(engine, motion) {
      this.engine = engine;
      this.motion = motion;
      this.lastUserAt = 0;
      this.lastAIAt = 0;
    }

    onUserMessage(text) {
      this.lastUserAt = performance.now();
      const state = this.engine.applyUserMessage(text);
      window.SARAH_LIVE2D?.setAvatarMode?.("thinking", { source: "avatar-ai-sync", intensity: Math.max(0.35, state.intensity) });
      if (state.emotion === "loving" || state.emotion === "embarrassed") this.motion?.triggerGesture?.("blush", state.intensity);
      if (state.emotion === "surprised") this.motion?.triggerGesture?.("surprise", state.intensity);
      return state;
    }

    onAIResponse(text, meta = {}) {
      this.lastAIAt = performance.now();
      const state = this.engine.applyAIResponse(text, meta);
      const profile = window.SARAH_AVATAR_LIPSYNC?.estimateSpeechProfile?.(text) || { emphasisCount: 0, sentenceCount: 1 };

      window.SARAH_LIVE2D?.applyMoodState?.(
        { emotion: state.emotion, intensity: state.intensity, affinity: state.affinity },
        { source: "avatar-ai-sync", tone: state.tone }
      );

      if (state.tone === "playful" || profile.emphasisCount > 1) this.motion?.triggerGesture?.("laugh", Math.min(1, state.intensity + 0.15));
      if (state.tone === "affectionate") this.motion?.triggerGesture?.("blush", Math.min(1, state.intensity + 0.1));
      if (state.tone === "sad") this.motion?.triggerGesture?.("distant", state.intensity);

      const releaseMs = Math.min(5500, Math.max(900, profile.estimatedSeconds * 450));
      setTimeout(() => {
        if (performance.now() - this.lastAIAt >= releaseMs - 80) {
          window.SARAH_LIVE2D?.setAvatarMode?.("idle", { source: "avatar-ai-sync-release" });
        }
      }, releaseMs);
      return state;
    }

    syncBackendMood(mood, avatarParams) {
      const state = this.engine.syncBackendMood(mood || {});
      if (window.SARAH_LIVE2D?.applyMoodState) {
        window.SARAH_LIVE2D.applyMoodState({ ...(mood || {}), emotion: state.emotion }, avatarParams || null);
      }
      return state;
    }
  }

  window.SarahAvatarAISyncController = SarahAvatarAISyncController;
})();
