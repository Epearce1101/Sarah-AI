const path = require("path");
const { pathToFileURL } = require("url");

const ROOT = path.resolve(__dirname, "..");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function main() {
  const storage = new Map();
  global.window = {
    localStorage: {
      getItem: (key) => storage.get(key) || null,
      setItem: (key, value) => storage.set(key, String(value)),
    },
  };
  global.performance = { now: () => Date.now() };
  global.requestAnimationFrame = (fn) => setTimeout(() => fn(Date.now()), 0);
  global.cancelAnimationFrame = clearTimeout;

  const registry = await import(pathToFileURL(path.join(ROOT, "renderer", "scripts", "diagnostics", "diagnostics-registry.js")).href);
  const storeModule = await import(pathToFileURL(path.join(ROOT, "renderer", "scripts", "diagnostics", "diagnostics-store.js")).href);

  assert(registry.REQUIRED_METRIC_IDS.length >= 70, "expected broad required metric coverage");
  assert(registry.PANEL_DEFINITIONS.length >= 10, "expected modular panel definitions");
  assert(registry.PANEL_PRESETS.full_telemetry.length >= 10, "full telemetry preset is incomplete");
  for (const id of registry.REQUIRED_METRIC_IDS) {
    assert(registry.METRIC_BY_ID[id], `metric id is not registered: ${id}`);
  }

  const snapshot = storeModule.createDiagnosticsSnapshot();
  assert(snapshot.metrics["ai.context_utilization_pct"], "snapshot missing context metric");
  assert(snapshot.metrics["extra.latency_histogram"], "snapshot missing latency histogram metric");
  assert(snapshot.layout.order.length >= 10, "snapshot missing full layout order");

  const store = new storeModule.DiagnosticsStore(snapshot);
  let subscriberPatch = null;
  store.subscribe((_, patch) => { subscriberPatch = patch; });
  store.patchMetrics({
    "ai.context_utilization_pct": 42,
    "network.backend_response_ms": 123,
    "alerts.critical": 1,
  });
  store.patchTelemetry({
    metrics: { "system.cpu_pct": 12, "model.top_model": "telemetry-model" },
    distributions: { latencyBuckets: [1, 2, 3, 4, 5, 6, 7] },
    alerts: [{ level: "warning", message: "unit" }],
  });
  await new Promise((resolve) => setTimeout(resolve, 30));

  const next = store.getSnapshot();
  assert(next.metrics["ai.context_utilization_pct"].value === 42, "metric patch did not persist");
  assert(next.metrics["network.backend_response_ms"].history.length > 0, "metric history was not appended");
  assert(next.metrics["system.cpu_pct"].value === 12, "telemetry metric patch did not persist");
  assert(next.metrics["model.top_model"].value === "telemetry-model", "telemetry text patch did not persist");
  assert(next.distributions.latencyBuckets[3] === 4, "telemetry distributions did not merge");
  assert(next.alerts[0].level === "warning", "telemetry alerts did not merge");
  assert(subscriberPatch && subscriberPatch.changed.includes("alerts.critical"), "subscriber did not receive incremental patch");

  store.setPreset("voice_debug");
  assert(store.getSnapshot().layout.order.includes("voice_wake"), "preset did not update layout order");
  assert(store.exportCsv().includes("ai.context_utilization_pct"), "CSV export missing metric");
  assert(JSON.parse(store.exportJson()).metrics["alerts.critical"].value === 1, "JSON export missing patched data");
  assert(store.metricsSummary().includes("Context: 42%"), "summary did not include patched context");

  console.log(JSON.stringify({ ok: true, metrics: registry.REQUIRED_METRIC_IDS.length, panels: registry.PANEL_DEFINITIONS.length }, null, 2));
}

main().catch((err) => {
  console.error(err && err.stack ? err.stack : err);
  process.exitCode = 1;
});
