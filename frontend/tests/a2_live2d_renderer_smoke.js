const path = require("path");
const { app, BrowserWindow } = require("electron");

app.commandLine.appendSwitch("use-fake-ui-for-media-stream");
app.commandLine.appendSwitch("enable-experimental-web-platform-features");

const ROOT = path.resolve(__dirname, "..");

async function main() {
  await app.whenReady();

  const win = new BrowserWindow({
    show: false,
    width: 1280,
    height: 900,
    webPreferences: {
      preload: path.join(ROOT, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      webSecurity: false,
      backgroundThrottling: false,
    },
  });

  win.webContents.on("console-message", (_event, _level, message) => {
    if (/^\[(Live2D|SARAH|Smoke)/.test(message)) {
      console.log(`[renderer] ${message}`);
    }
  });

  await win.loadFile(path.join(ROOT, "renderer", "index.html"));

  const result = await win.webContents.executeJavaScript(`
    (async () => {
      const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const runtimeErrors = [];
      const recordRuntimeError = (value) => {
        const message = value?.message || value?.reason?.message || String(value || "unknown renderer error");
        runtimeErrors.push(message);
      };
      window.addEventListener("error", recordRuntimeError);
      window.addEventListener("unhandledrejection", recordRuntimeError);
      const waitFor = async (predicate, label, timeoutMs = 12000) => {
        const start = performance.now();
        while (performance.now() - start < timeoutMs) {
          if (predicate()) return;
          await wait(50);
        }
        throw new Error("Timed out waiting for " + label);
      };

      await waitFor(
        () => window.SARAH_LIVE2D?.model && window.SARAH_LIVE2D?.applyMoodState,
        "Live2D model"
      );
      await waitFor(
        () => window.SARAH_AVATAR_SYSTEM?.getDiagnostics,
        "modular avatar system"
      );
      await wait(1300);

      const states = [
        "neutral",
        "happy",
        "excited",
        "affectionate",
        "shy",
        "confused",
        "surprised",
        "angry",
        "frustrated",
        "sad",
        "thinking",
        "listening",
        "speaking",
        "low_energy",
      ];

      for (const emotion of states) {
        window.SARAH_LIVE2D.applyMoodState(
          { emotion, intensity: emotion === "neutral" ? 0 : 0.8, affinity: 0.9 },
          {
            emotion,
            intensity_multiplier: 0.8,
            affinity_multiplier: 0.9,
            parameters: {
              brow_angle: emotion === "angry" ? -0.4 : 0.1,
              mouth_smile: emotion === "sad" ? -0.3 : 0.25,
              body_tilt: 0.1,
            },
          }
        );
        await wait(80);
      }

      for (const mode of ["thinking", "listening", "speaking", "idle"]) {
        const result = window.SARAH_LIVE2D.setAvatarMode?.(mode, { intensity: 0.65 });
        if (!result?.ok) throw new Error("setAvatarMode failed for " + mode);
        await wait(80);
      }

      const visemeOk = window.SARAH_LIVE2D.applyVisemeTimeline?.([
        { time: 0, viseme: "A", duration: 0.05 },
        { time: 0.07, viseme: "E", duration: 0.05 },
        { time: 0.14, viseme: "O", duration: 0.05 },
        { time: 0.21, viseme: "closed", duration: 0.05 },
      ]);
      if (!visemeOk) throw new Error("applyVisemeTimeline returned false");
      await wait(420);

      const missing = window.SARAH_LIVE2D.getMissingParamWarnings?.() || [];
      const expressionWarnings = window.SARAH_LIVE2D.getExpressionWarnings?.() || [];
      const rig = window.SARAH_LIVE2D.getRigDiagnostics?.();
      if (missing.length) {
        throw new Error("Missing Live2D params during smoke: " + missing.join(", "));
      }
      if (expressionWarnings.length) {
        throw new Error("Expression warnings during smoke: " + expressionWarnings.join(", "));
      }
      if (!rig || rig.visemeFramesApplied < 4) {
        throw new Error("Viseme diagnostics did not record playback");
      }

      window.SARAH_AVATAR_SYSTEM.updateFromContext([
        { role: "user", content: "good girl, this is perfect" },
        { role: "assistant", content: "That makes me happy. I love helping with this." },
        { role: "user", content: "thank you, this is great" },
      ], { source: "renderer-smoke-context", immediate: true });
      window.SARAH_AVATAR_SYSTEM.onUserMessage("good girl, this is perfect");
      window.SARAH_AVATAR_SYSTEM.onAIResponse("I love that. This is exciting!", {
        source: "renderer-smoke",
      });
      const customOk = window.SARAH_AVATAR_SYSTEM.registerExpression("curious_smoke", {
        params: { browLY: 0.2 },
        motion: { headTilt: -4, sway: 0.9 },
      });
      const avatar = window.SARAH_AVATAR_SYSTEM.getDiagnostics();
      if (!customOk || !avatar.expressions.includes("curious_smoke")) {
        throw new Error("Modular avatar custom expression registration failed");
      }
      if (avatar.state.contextMessageCount < 3 || !["happy", "loving"].includes(avatar.state.baseEmotion)) {
        throw new Error("Modular avatar baseline did not derive from context window");
      }
      if (avatar.state.affinity <= 0.5) {
        throw new Error("Modular avatar context-derived affinity did not respond to positive context");
      }
      if (!avatar.state.overlayActive || avatar.state.overlayDurationMs < 1000 || avatar.state.overlayDurationMs > 3000) {
        throw new Error("Modular avatar transient overlay did not activate with 1-3s fade window");
      }
      // In a hidden window requestAnimationFrame is throttled, so the motion
      // controller's tick (which calls emotion.decay) barely runs. Pump decay
      // directly — it advances on wall-clock elapsed — so the transient overlay
      // deterministically fades back to baseline.
      const fadeDeadline = performance.now() + avatar.state.overlayDurationMs + 500;
      while (performance.now() < fadeDeadline) {
        window.SARAH_AVATAR_SYSTEM.emotion.decay(0.05);
        await wait(50);
      }
      const settledAvatar = window.SARAH_AVATAR_SYSTEM.getDiagnostics();
      if (settledAvatar.state.overlayActive) {
        throw new Error("Modular avatar transient overlay did not fade back to baseline");
      }
      if (settledAvatar.state.emotion !== settledAvatar.state.baselineDisplayEmotion) {
        throw new Error("Modular avatar did not return visible emotion to inertial baseline");
      }
      if (runtimeErrors.length) {
        throw new Error("Renderer runtime errors during Live2D smoke: " + runtimeErrors.join(" | "));
      }

      return {
        states,
        parameterCount: window.SARAH_LIVE2D.parameterIds?.size || 0,
        rig,
        avatar,
      };
    })();
  `, true);

  console.log(JSON.stringify({ ok: true, ...result }, null, 2));
  await win.close();
}

main()
  .catch((err) => {
    console.error(err && err.stack ? err.stack : err);
    process.exitCode = 1;
  })
  .finally(() => app.quit());
