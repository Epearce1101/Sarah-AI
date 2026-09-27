const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const read = (rel) => fs.readFileSync(path.join(root, rel), "utf8");

const indexHtml = read("renderer/index.html");
const dashboardJs = read("renderer/dashboard.js");
const registryJs = read("renderer/scripts/diagnostics/diagnostics-registry.js");
const storeJs = read("renderer/scripts/diagnostics/diagnostics-store.js");
const opsJs = read("renderer/scripts/diagnostics/diagnostics-dashboard.js");
const stylesCss = read("renderer/styles/styles.css");
const componentsCss = read("renderer/styles/a1-components.css");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

assert(indexHtml.includes('id="btn-diagnostics"'), "missing diagnostics sidebar button");
assert(
  indexHtml.includes('id="diagnostics-dashboard" class="diagnostics-modal hidden"'),
  "diagnostics dashboard host should be hidden by default"
);
assert(!indexHtml.includes('diag-wake-transcript'), "old static diagnostics card should not be embedded in HTML");

// Issue #5: the diagnostics dashboard is lazy-loaded via dynamic import() on
// first open (not a static top-level import) so boot doesn't pay its parse/init
// cost. These assertions track that contract.
assert(
  dashboardJs.includes('import("./scripts/diagnostics/diagnostics-dashboard.js")'),
  "dashboard should lazy-load the diagnostics module via dynamic import()"
);
assert(dashboardJs.includes('_ensureDiagnosticsDashboard'), "dashboard should gate diagnostics behind _ensureDiagnosticsDashboard");
assert(dashboardJs.includes('new mod.SarahDiagnosticsDashboard'), "dashboard module is not instantiated");
assert(dashboardJs.includes('diag.open()'), "open handler should delegate to the modular dashboard");
assert(dashboardJs.includes('this.diagnostics.close()'), "close handler should delegate to the modular dashboard");
assert(!dashboardJs.includes('this._startDiagnosticsPanel();'), "diagnostics must not auto-start from constructor");

[
  "REQUIRED_METRIC_IDS",
  "PANEL_DEFINITIONS",
  "PANEL_PRESETS",
  "full_telemetry",
  "ai.context_utilization_pct",
  "ai.time_to_first_token_ms",
  "model.usage_distribution",
  "chat.activity_heatmap",
  "voice.wake_reason_breakdown",
  "system.renderer_fps",
  "network.websocket_latency_ms",
  "errors.exception_categories",
  "extra.error_spike_5m",
  "extra.affinity_trend",
  "extra.latency_histogram",
].forEach((needle) => assert(registryJs.includes(needle), `missing registry item: ${needle}`));

[
  "loadDiagnosticsSnapshot",
  "createDiagnosticsSnapshot",
  "patchMetrics",
  "patchLayout",
  "startCollectors",
  "tryWebSocket",
  "patchTelemetry",
  "/api/diagnostics/telemetry",
  "exportCsv",
  "metricsSummary",
].forEach((needle) => assert(storeJs.includes(needle), `missing store feature: ${needle}`));

[
  "cached first paint",
  "data-diag-grid",
  "draggable = true",
  "data-panel-collapse",
  "data-panel-float",
  "export-csv",
  "export-json",
  "copy-summary",
  "html2canvas",
  "setTimeout(() =>",
].forEach((needle) => assert(opsJs.includes(needle), `missing dashboard feature: ${needle}`));

assert(stylesCss.includes("Diagnostics Ops Console"), "missing diagnostics layout CSS");
assert(componentsCss.includes("Diagnostics neon operations console"), "missing diagnostics neon component CSS");

console.log(JSON.stringify({ ok: true, checked: "modular-diagnostics-static" }));
