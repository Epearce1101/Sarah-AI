// Modular Sarah Live2D avatar configuration.
(function () {
  const CONFIG = {
    version: "modular-live2d-v1",
    expressionBlendMs: 260,
    defaultIntensity: 0.35,
    affinity: {
      min: 0,
      max: 1,
      neutral: 0.55,
      high: 0.75,
      low: 0.25,
      storageKey: "sarah.avatar.affinity",
    },
    expressions: {
      neutral: {
        live2d: null,
        params: { mouthForm: 0, mouthOpen: 0, browLY: 0, browRY: 0, headZ: 0, eyeY: 0 },
        motion: { headTilt: 0, bodyLean: 0, sway: 1, blink: 1, warmth: 0.4 },
      },
      happy: {
        live2d: null,
        params: { mouthForm: 0.62, mouthOpen: 0.12, browLY: 0.1, browRY: 0.1, headY: 2.5, bodyY: 2.5 },
        motion: { headTilt: -2, bodyLean: 2, sway: 1.25, blink: 1.2, warmth: 0.8 },
      },
      sad: {
        live2d: "sad",
        params: { mouthForm: -0.48, mouthOpen: 0.03, browLY: 0.32, browRY: 0.32, headY: -3.5, eyeY: 0.62, bodyX: -2.5 },
        motion: { headTilt: 2, bodyLean: -2, sway: 0.55, blink: 0.62, warmth: 0.2 },
      },
      angry: {
        live2d: "angry",
        params: { mouthForm: -0.44, mouthOpen: 0.18, browLY: -0.58, browRY: -0.58, headY: 1.5, bodyX: 2 },
        motion: { headTilt: 1, bodyLean: 2, sway: 0.85, blink: 1.1, warmth: 0.12 },
      },
      embarrassed: {
        live2d: "shy",
        params: { mouthForm: 0.32, mouthOpen: 0.04, browLY: 0.18, browRY: 0.1, headZ: -7, eyeY: -0.32, bodyX: -3 },
        motion: { headTilt: -8, bodyLean: -2, sway: 0.9, blink: 1.35, warmth: 0.95 },
      },
      surprised: {
        live2d: null,
        params: { mouthOpen: 0.7, mouthForm: -0.05, browLY: 0.28, browRY: 0.28, headY: 4.5, bodyY: -3 },
        motion: { headTilt: 0, bodyLean: 3, sway: 1.15, blink: 1.4, warmth: 0.5 },
      },
      loving: {
        live2d: "shy",
        params: { mouthForm: 0.48, mouthOpen: 0.06, browLY: 0.12, browRY: 0.12, headZ: -6, eyeY: -0.18, bodyX: -2.5 },
        motion: { headTilt: -6, bodyLean: 3, sway: 1.35, blink: 1.25, warmth: 1 },
      },
      confused: {
        live2d: null,
        params: { mouthForm: -0.08, mouthOpen: 0.05, browLY: 0.26, browRY: -0.18, headZ: -5, headY: 1.2 },
        motion: { headTilt: -5, bodyLean: 0, sway: 0.85, blink: 0.95, warmth: 0.35 },
      },
      smug: {
        live2d: null,
        params: { mouthForm: 0.42, mouthOpen: 0.03, browLY: -0.1, browRY: 0.16, headZ: 5, eyeY: -0.1, bodyX: 2 },
        motion: { headTilt: 5, bodyLean: 2, sway: 0.95, blink: 0.85, warmth: 0.65 },
      },
      tired: {
        live2d: "sad",
        params: { mouthForm: -0.22, mouthOpen: 0.02, browLY: 0.18, browRY: 0.18, headY: -4, eyeY: 0.55, bodyX: -3.5 },
        motion: { headTilt: 2, bodyLean: -4, sway: 0.42, blink: 0.5, warmth: 0.15 },
      },
    },
    aliases: {
      affectionate: "loving",
      love: "loving",
      flirty: "loving",
      shy: "embarrassed",
      low_energy: "tired",
      sleepy: "tired",
      frustrated: "angry",
      annoyed: "angry",
      excited: "happy",
    },
    toneToEmotion: {
      playful: "happy",
      serious: "neutral",
      teasing: "smug",
      affectionate: "loving",
      sad: "sad",
      excited: "happy",
    },
    sentiment: {
      positive: ["thanks", "thank you", "good", "great", "nice", "love", "perfect", "amazing", "beautiful", "proud", "happy", "cute", "sweet"],
      negative: ["bad", "wrong", "hate", "annoying", "broken", "stupid", "terrible", "awful", "mad", "angry", "upset", "frustrated", "frustrating"],
      affection: ["love you", "miss you", "good girl", "sweetheart", "dear", "baby", "beautiful", "cute"],
      surprise: ["wow", "whoa", "omg", "surprise", "unexpected"],
      confusion: ["confused", "what", "why", "how come", "huh", "unclear"],
      tired: ["tired", "sleepy", "exhausted", "drained"],
    },
    motion: {
      // Lisette's hair-sway rig only exposes Param_Angle_Rotation15-18; the
      // ganyu-era 19-26 IDs don't exist on this model (they silently no-op).
      maxHairParams: 4,
      hairParamIds: [
        "Param_Angle_Rotation15", "Param_Angle_Rotation16", "Param_Angle_Rotation17", "Param_Angle_Rotation18",
      ],
      blinkFlickChance: 0.002,
      microGestureChance: 0.004,
    },
    // Issue #32: body-rig gesture recipes targeting Lisette's actual
    // parameter IDs (ParamArmRMove / ParamArmLMove2 / ParamHandLMove /
    // ParamHandRMove / ParamLegLStep / ParamLegRStep / ParamLowerArmLMove
    // / ParamLowerArmRMove / ParamFingerMoveL/R / ParamFingerRelaxL/R /
    // ParamJump / ParamCrouch / ParamWalking / ParamWalkToggle plus the
    // multi-stage ParamBodyAngleZ2/Z3/Y2). easeParameter no-ops on missing
    // ids, so trying these on a different rig won't crash — only Lisette
    // animates them fully.
    // Gesture recipes use Lisette's NATIVE parameter ranges (verified from the
    // model's CDI): arms/hands/fingers ParamFingerMove ±10, ParamFingerRelax /
    // ParamLeg*Step 0..10, ParamCrouch 0..20, ParamJump -5..10, head angles
    // ParamAngle* ±30, body angles ParamBodyAngle* ±10, mouth/brow/cheek 0..1
    // or ±1. Values are absolute writes (not 0..1 fractions), so they must be
    // scaled to the param's real range or the limb barely moves. Each param is
    // written once — no semantic-alias duplicates that would last-write-win and
    // cancel the intended pose.
    gestures: {
      wave: {
        durationMs: 1400,
        params: [
          { id: "ParamArmRMove", value: -8.5 },
          { id: "ParamLowerArmRMove", value: 6 },
          { id: "ParamHandRMove", value: 8 },
          { id: "ParamFingerRelaxR", value: 6 },
          { id: "ParamBodyAngleZ", value: 4 },
        ],
      },
      arms_up: {
        durationMs: 1100,
        params: [
          { id: "ParamArmLMove2", value: 9 },
          { id: "ParamArmRMove", value: -9 },
          { id: "ParamLowerArmLMove", value: 5 },
          { id: "ParamLowerArmRMove", value: 5 },
          { id: "ParamHandLMove", value: 5 },
          { id: "ParamHandRMove", value: 5 },
          { id: "ParamFingerRelaxL", value: 8 },
          { id: "ParamFingerRelaxR", value: 8 },
          { id: "ParamBodyAngleY", value: -4 },
          { id: "ParamJump", value: 5 },
        ],
      },
      thinking_pose: {
        durationMs: 1800,
        params: [
          { id: "ParamArmRMove", value: -5 },
          { id: "ParamLowerArmRMove", value: 8.5 },
          { id: "ParamHandRMove", value: 6 },
          { id: "ParamFingerMoveR", value: 5 },
          { id: "ParamAngleZ", value: -10 },
        ],
      },
      // (spin handled as a multi-phase keyframed gesture in
      // avatar-motion-controller.js — 2D rigs can't truly rotate 360°,
      // so it reads better as a brisk twist-and-snap-back sequence.)
      shrug: {
        durationMs: 950,
        params: [
          { id: "ParamArmLMove2", value: 4 },
          { id: "ParamArmRMove", value: -4 },
          { id: "ParamLowerArmLMove", value: 5 },
          { id: "ParamLowerArmRMove", value: 5 },
          { id: "ParamFingerRelaxL", value: 4 },
          { id: "ParamFingerRelaxR", value: 4 },
          { id: "ParamBrowLY", value: 0.3 },
          { id: "ParamBrowRY", value: 0.3 },
        ],
      },
      point: {
        durationMs: 1100,
        params: [
          { id: "ParamArmRMove", value: -6 },
          { id: "ParamLowerArmRMove", value: 2 },
          { id: "ParamHandRMove", value: -3 },
          { id: "ParamFingerFixed", value: 0.8 },
          { id: "ParamBodyAngleX", value: 4 },
        ],
      },
      hand_to_chest: {
        durationMs: 1300,
        params: [
          { id: "ParamArmRMove", value: -3 },
          { id: "ParamLowerArmRMove", value: 9 },
          { id: "ParamHandRMove", value: 4 },
          { id: "ParamFingerRelaxR", value: 5 },
          { id: "ParamBodyAngleZ", value: 3 },
        ],
      },
      lean_in: {
        durationMs: 1100,
        params: [
          { id: "ParamBodyAngleX", value: 7 },
          { id: "ParamAngleX", value: 6 },
        ],
      },
      step_back: {
        durationMs: 1200,
        params: [
          { id: "ParamBodyAngleX", value: -7 },
          { id: "ParamAngleX", value: -5 },
          { id: "ParamLegLStep", value: 4 },
        ],
      },
      nod: {
        durationMs: 700,
        params: [{ id: "ParamAngleY", value: -16 }],
      },
      shake_head: {
        durationMs: 800,
        params: [{ id: "ParamAngleX", value: 16 }],
      },
      jump: {
        durationMs: 950,
        params: [
          { id: "ParamJump", value: 8 },
          { id: "ParamArmLMove2", value: 5 },
          { id: "ParamArmRMove", value: -5 },
        ],
      },
      crouch: {
        durationMs: 1100,
        params: [{ id: "ParamCrouch", value: 14 }, { id: "ParamBodyAngleY", value: -6 }],
      },
      blush: {
        durationMs: 1500,
        params: [
          { id: "ParamCheek", value: 1 },
          { id: "ParamAngleZ", value: -8 },
        ],
      },
    },
    // Self-description Sarah injects into the system prompt so the LLM
    // knows what the avatar looks like and what gestures it can request.
    selfDescription: {
      appearance: "Lisette — adult-female anime character with long flowing hair, large expressive eyes, an ornate dress, and full body rig (arms, hands, legs, fingers)",
      modelId: "lisette",
      rigCapabilities: ["face", "head_tilt", "body_lean", "arms", "hands", "fingers", "legs", "jump", "crouch", "walk"],
    },
  };

  window.SARAH_AVATAR_CONFIG = Object.freeze(CONFIG);
})();
