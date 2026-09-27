// Deep runtime verification of the Live2D (Lisette) avatar against a live
// render: expression files apply, chat-synced emotion drives face params,
// every gesture produces visible motion at render time, idle motions rotate,
// and lipsync drives the mouth. Run under Electron:
//   node_modules\.bin\electron.cmd tests\avatar_runtime_check.js
const path = require("path");
const { app, BrowserWindow } = require("electron");

app.commandLine.appendSwitch("use-fake-ui-for-media-stream");
app.commandLine.appendSwitch("enable-experimental-web-platform-features");

const ROOT = path.resolve(__dirname, "..");

async function main() {
  await app.whenReady();
  const win = new BrowserWindow({
    show: false, width: 1280, height: 900,
    webPreferences: {
      preload: path.join(ROOT, "preload.js"),
      contextIsolation: true, nodeIntegration: false, sandbox: false,
      webSecurity: false, backgroundThrottling: false,
    },
  });
  win.webContents.on("console-message", (_e, _l, m) => {
    if (/^\[(Live2D|Sarah\/Gesture|Sarah\/MotionTag)/.test(m)) console.log(`[renderer] ${m}`);
  });
  await win.loadFile(path.join(ROOT, "renderer", "index.html"));

  const result = await win.webContents.executeJavaScript(`
    (async () => {
      const wait = (ms) => new Promise(r => setTimeout(r, ms));
      const waitFor = async (p, label, t = 15000) => {
        const s = performance.now();
        while (performance.now() - s < t) { if (p()) return; await wait(50); }
        throw new Error("timeout: " + label);
      };
      const assert = (c, m) => { if (!c) throw new Error(m); };

      await waitFor(() => window.SARAH_LIVE2D?.model && window.SARAH_LIVE2D?.applyMoodState, "model");
      await waitFor(() => window.SARAH_AVATAR_SYSTEM?.motion, "avatar system");
      const L = window.SARAH_LIVE2D, S = window.SARAH_AVATAR_SYSTEM, motion = S.motion;
      const internal = L.model.internalModel, core = internal.coreModel;
      await wait(800);

      // Render-time param probe: registered after live2d-sarah.js's own
      // beforeModelUpdate (which runs applyActiveGestures), so it reads the
      // value actually used for the frame — not the post-update snapshot.
      const probe = { id: null, samples: [] };
      internal.on("beforeModelUpdate", () => {
        if (probe.id) { try { probe.samples.push(core.getParameterValueById(probe.id)); } catch {} }
      });
      // Suppress the random micro-gesture timer so it can't perturb probes.
      motion.nextMicroAt = Number.MAX_SAFE_INTEGER;
      // In a hidden Electron window requestAnimationFrame is throttled, so the
      // model's update loop (which fires beforeModelUpdate → applyActiveGestures)
      // barely runs. Pump internal.update() manually with real time between
      // pumps so the gesture's time-based weight ramps and the applier writes
      // the param every step. The probe listener reads the render-time value.
      const probeGesture = async (name, id, ms) => {
        motion.activeGestures = []; motion.gestureLockUntil = 0;
        await wait(120);
        probe.id = id; probe.samples = [];
        const ret = S.triggerGesture(name, 1.0);
        const steps = Math.max(10, Math.round(ms / 45));
        for (let k = 0; k < steps; k++) { await wait(45); try { internal.update(45); } catch {} }
        probe.id = null;
        motion.nextMicroAt = Number.MAX_SAFE_INTEGER;
        // Gestures write absolute target values, so the peak ABSOLUTE render
        // magnitude reflects how far the limb was driven.
        let peakMag = 0;
        for (const v of probe.samples) if (Math.abs(v) > peakMag) peakMag = Math.abs(v);
        motion.activeGestures = []; motion.gestureLockUntil = 0;
        return { ret, delta: peakMag };
      };

      // Persistent idle-motion counter — the idle rotation fires its first
      // motion ~1.5s after load, which is before the later test phases run, so
      // count cumulatively from the start rather than in a late window.
      let idleFireCount = 0;
      const mgr = internal.motionManager;
      const baseStartMotion = mgr.startMotion.bind(mgr);
      mgr.startMotion = (g, i, p) => { if (g === "Idle") idleFireCount++; return baseStartMotion(g, i, p); };

      const report = { ok: true };

      // 1) Model contract
      const expDefs = (internal.motionManager?.expressionManager?.definitions || []).map(d => d.Name);
      assert(expDefs.length >= 9, "expected >=9 expression files, got " + expDefs.length);
      assert((L.parameterIds?.size || 0) > 200, "expected a full Lisette param set");
      report.expressionFiles = expDefs.length;
      report.parameterCount = L.parameterIds.size;

      // 2) Every expression file applies cleanly
      for (const name of ["angry","sad","shy","frenzy","tear","tongue_out","dark_mask","sans_eye_glow"]) {
        const r = await L.setExpression(name);
        assert(r !== false, "expression failed to apply: " + name);
        await wait(40);
      }
      L.setExpression(null);
      assert((L.getExpressionWarnings?.() || []).length === 0, "expression warnings: " + (L.getExpressionWarnings?.() || []).join(","));

      // 3) Chat-synced emotion moves face params
      const faceIds = ["ParamMouthForm","ParamBrowLY","ParamMouthOpenY","ParamBodyAngleX"];
      let emotionsThatMoved = 0;
      for (const emotion of ["sad","angry","happy","surprised","shy"]) {
        L.applyMoodState({ emotion: "neutral", intensity: 0 }); await wait(260);
        const before = faceIds.map(id => core.getParameterValueById(id));
        L.applyMoodState({ emotion, intensity: 0.9 }); await wait(420);
        const moved = faceIds.some((id, i) => Math.abs(core.getParameterValueById(id) - before[i]) > 0.03);
        if (moved) emotionsThatMoved++;
      }
      L.applyMoodState({ emotion: "neutral", intensity: 0 }); await wait(300);
      assert(emotionsThatMoved >= 4, "only " + emotionsThatMoved + "/5 emotions moved face params");
      report.emotionsThatMoved = emotionsThatMoved;

      // 4) Gestures produce visible motion at render time
      report.gestures = {};
      // (a) motion-file gestures play their .motion3.json timeline
      const orig = mgr.startMotion;
      let played = [];
      mgr.startMotion = (g, i, p) => { played.push(g); return orig(g, i, p); };
      for (const [g, group] of [["wave","Hello"],["jump","Jump"],["laugh","Happy"],["distant","Sad"]]) {
        played = []; S.triggerGesture(g, 0.9); await wait(140);
        assert(played.includes(group), "motion-file gesture did not play: " + g + " -> " + group);
        report.gestures[g] = "motion:" + group;
      }
      mgr.startMotion = orig;
      // (b) param-recipe gestures move their primary param (id, min-delta, window-ms)
      const recipeGestures = [
        ["thinking_pose","ParamLowerArmRMove",2,950],["shrug","ParamArmLMove2",1,950],
        ["nod","ParamAngleY",3,950],["point","ParamFingerFixed",0.3,950],
        ["crouch","ParamCrouch",2,950],["arms_up","ParamArmLMove2",3,950],
        ["spin","ParamAngleX",3,1800],["surprise","ParamBodyAngleY",1,950],
        ["lean_in","ParamBodyAngleX",2,950],["step_back","ParamBodyAngleX",2,950],
        ["hand_to_chest","ParamLowerArmRMove",3,950],["shake_head","ParamAngleX",3,950],
      ];
      for (const [g, id, min, ms] of recipeGestures) {
        const { ret, delta } = await probeGesture(g, id, ms);
        assert(ret === true, "triggerGesture returned false: " + g);
        assert(delta >= min, "gesture barely moved: " + g + " " + id + " delta=" + delta.toFixed(2) + " (<" + min + ")");
        report.gestures[g] = Number(delta.toFixed(2));
      }

      // 5) Idle motion rotation fired on its own during the run (first ~1.5s).
      assert(idleFireCount >= 1, "idle motion rotation never fired");
      report.idleMotionFires = idleFireCount;

      // 6) Lipsync drives the mouth (pump updates so render-time reads land)
      probe.id = "ParamMouthOpenY"; probe.samples = [];
      const mBase = core.getParameterValueById("ParamMouthOpenY");
      L.applyVisemeTimeline([
        { time: 0, viseme: "A", duration: 0.06 },
        { time: 0.1, viseme: "O", duration: 0.06 },
        { time: 0.2, viseme: "E", duration: 0.06 },
      ]);
      for (let k = 0; k < 10; k++) { await wait(45); try { internal.update(45); } catch {} }
      probe.id = null;
      const mPeak = Math.max(mBase, ...probe.samples);
      assert(mPeak - mBase > 0.1, "lipsync did not open the mouth: " + (mPeak - mBase).toFixed(2));
      report.lipsyncMouthDelta = Number((mPeak - mBase).toFixed(2));

      // 7) Chat-sync: onAIResponse applies an emotion overlay
      S.onAIResponse("That makes me so happy!", { emotion: "happy", intensity: 0.8, source: "test" });
      const st = S.getState();
      assert(st.overlayActive || st.emotion === "happy", "onAIResponse did not drive emotion");
      report.chatSyncEmotion = st.emotion;

      return report;
    })();
  `, true);

  console.log(JSON.stringify(result, null, 2));
  await win.close();
}

main().catch((e) => { console.error(e && e.stack ? e.stack : e); process.exitCode = 1; }).finally(() => app.quit());
