const path = require("path");
const { app, BrowserWindow } = require("electron");

app.commandLine.appendSwitch("use-fake-ui-for-media-stream");

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

  await win.loadFile(path.join(ROOT, "renderer", "index.html"));

  const result = await win.webContents.executeJavaScript(`
    (async () => {
      const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const waitFor = async (predicate, label, timeoutMs = 7000) => {
        const start = performance.now();
        while (performance.now() - start < timeoutMs) {
          if (predicate()) return;
          await wait(25);
        }
        throw new Error("Timed out waiting for " + label);
      };
      const assert = (condition, message) => {
        if (!condition) throw new Error(message);
      };

      await waitFor(() => window.SARAH_UI, "Sarah UI");
      const ui = window.SARAH_UI;
      const dashboard = document.getElementById("diagnostics-dashboard");
      assert(dashboard.classList.contains("hidden"), "diagnostics dashboard should start hidden");
      // Issue #5: the modular dashboard is lazy-loaded — not built until first open.
      assert(!ui.diagnostics, "diagnostics module should not be built before first open");
      assert(!ui.diagnosticsPollTimer, "legacy diagnostics polling should not run while hidden");

      // First open lazily imports + builds + mounts the modular dashboard.
      document.getElementById("btn-diagnostics").click();
      await waitFor(() => ui.diagnostics && !dashboard.classList.contains("hidden"), "diagnostics dashboard open");
      assert(ui.diagnostics.opened, "modular diagnostics state did not open");
      assert(!ui.diagnosticsPollTimer, "legacy diagnostics polling should stay disabled");

      // Close, then re-open to measure the warm path (module already cached) —
      // the fast cached-data open the lazy-load design guarantees.
      document.getElementById("diagnostics-close").click();
      await waitFor(() => dashboard.classList.contains("hidden"), "diagnostics dashboard close (warmup)");
      const openStart = performance.now();
      document.getElementById("btn-diagnostics").click();
      await waitFor(() => !dashboard.classList.contains("hidden"), "diagnostics dashboard reopen");
      const openElapsed = performance.now() - openStart;
      // openElapsed is wall-clock through this test's 25ms poll loop + async
      // scheduling, so it only gets a loose sanity bound. The dashboard's own
      // synchronous render time (dataset.lastOpenMs) is the real perf gate.
      assert(openElapsed < 1500, "warm diagnostics open unreasonably slow: " + openElapsed.toFixed(2));
      assert(ui.diagnostics.opened, "modular diagnostics state did not reopen");
      const lastOpenMs = Number(dashboard.dataset.lastOpenMs || 999);
      assert(lastOpenMs < 32, "dashboard render path exceeded first-frame target: " + lastOpenMs.toFixed(2));
      assert(!ui.diagnosticsPollTimer, "legacy diagnostics polling should stay disabled");

      const panels = dashboard.querySelectorAll(".diagnostics-panel");
      assert(panels.length >= 8, "expected modular diagnostic panels");
      assert(dashboard.querySelector("[data-panel-collapse]"), "missing collapse control");
      assert(dashboard.querySelector("[data-panel-float]"), "missing floating control");
      assert(dashboard.querySelector('[data-diag-action="export-json"]'), "missing JSON export");
      assert(dashboard.querySelector('[data-diag-action="export-csv"]'), "missing CSV export");
      assert(dashboard.querySelector('[data-diag-time="24h"]'), "missing time filters");

      if (ui.diagnostics.collectorStartTimer) clearTimeout(ui.diagnostics.collectorStartTimer);
      ui.diagnostics.collectorStartTimer = null;
      ui.diagnostics.store.stopCollectors();
      await wait(80);
      ui.diagnostics.store.patchMetrics({
        "ai.context_utilization_pct": 25,
        "system.renderer_fps": 60,
        "voice.wake_confidence_avg": 52,
        "alerts.warning": 1,
      });
      ui.diagnostics.applySnapshot(ui.diagnostics.store.getSnapshot(), { full: true });

      assert(dashboard.querySelector('[data-metric-value="ai.context_utilization_pct"]').textContent === "25%", "context metric not patched");
      assert(dashboard.querySelector('[data-metric-value="system.renderer_fps"]').textContent === "60", "renderer FPS metric not patched");
      assert(dashboard.querySelector('[data-diag-alert-summary]').textContent.includes("warning"), "warning alert badge not updated");

      const firstPanel = dashboard.querySelector(".diagnostics-panel");
      const firstCollapse = firstPanel.querySelector("[data-panel-collapse]");
      const wasCollapsed = firstPanel.classList.contains("is-collapsed");
      firstCollapse.click();
      await wait(40);
      assert(firstPanel.classList.contains("is-collapsed") !== wasCollapsed, "panel collapse toggle did not apply");

      document.getElementById("diagnostics-close").click();
      await waitFor(() => dashboard.classList.contains("hidden"), "diagnostics dashboard close");
      assert(!ui.diagnostics.opened, "modular diagnostics state did not close");

      return { ok: true, openElapsed: Number(openElapsed.toFixed(2)), lastOpenMs, panels: panels.length };
    })();
  `, true);

  console.log(JSON.stringify(result, null, 2));
  await win.close();
}

main()
  .catch((err) => {
    console.error(err && err.stack ? err.stack : err);
    process.exitCode = 1;
  })
  .finally(() => app.quit());
