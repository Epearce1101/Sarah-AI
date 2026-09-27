import { DIAGNOSTICS_VERSION, METRIC_DEFINITIONS, METRIC_BY_ID, getPresetOrder } from "./diagnostics-registry.js";

export const SNAPSHOT_STORAGE_KEY = "sarah:diagnostics:snapshot:v1";
export const LAYOUT_STORAGE_KEY = "sarah:diagnostics:layout:v1";
const HISTORY_LIMIT = 120;
const now = () => Date.now();

function storageGet(key) { try { return window.localStorage.getItem(key); } catch (_) { return null; } }
function scheduleUiPatch(callback) {
  let fired = false;
  const run = () => {
    if (fired) return;
    fired = true;
    callback();
  };
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(run);
  setTimeout(run, 0);
}
function storageSet(key, value) { try { window.localStorage.setItem(key, value); } catch (_) {} }
function seedHistory(value, count = 18) {
  const base = Number(value) || 0;
  const start = now() - count * 60000;
  return Array.from({ length: count }, (_, idx) => ({ t: start + idx * 60000, v: Math.max(0, base + Math.sin(idx / 2) * Math.max(1, base * 0.08)) }));
}

export function loadDiagnosticsLayout() {
  const fallback = { preset: "full_telemetry", compact: false, debug: false, order: getPresetOrder("full_telemetry"), collapsed: {}, floating: {} };
  try {
    const parsed = JSON.parse(storageGet(LAYOUT_STORAGE_KEY) || "null");
    return parsed && Array.isArray(parsed.order) ? { ...fallback, ...parsed } : fallback;
  } catch (_) { return fallback; }
}
export function saveDiagnosticsLayout(layout) { storageSet(LAYOUT_STORAGE_KEY, JSON.stringify(layout)); }

export function createDiagnosticsSnapshot() {
  const metrics = {};
  for (const def of METRIC_DEFINITIONS) {
    metrics[def.id] = { value: def.defaultValue, updatedAt: now(), history: seedHistory(typeof def.defaultValue === "number" ? def.defaultValue : 0) };
  }
  return {
    version: DIAGNOSTICS_VERSION,
    updatedAt: now(),
    timeFilter: "1h",
    layout: loadDiagnosticsLayout(),
    alerts: [],
    distributions: { modelUsage: { "nvidia/nemotron-3-ultra-550b-a55b:free": 1 }, sentiment: { positive: 0, neutral: 1, negative: 0 }, wakeReasons: {}, latencyBuckets: [0, 0, 0, 0, 0, 0, 0], heatmap: Array.from({ length: 24 }, () => 0) },
    metrics,
  };
}

export function loadDiagnosticsSnapshot() {
  const base = createDiagnosticsSnapshot();
  try {
    const parsed = JSON.parse(storageGet(SNAPSHOT_STORAGE_KEY) || "null");
    if (!parsed || parsed.version !== DIAGNOSTICS_VERSION || !parsed.metrics) return base;
    return { ...base, ...parsed, layout: loadDiagnosticsLayout(), metrics: { ...base.metrics, ...parsed.metrics } };
  } catch (_) { return base; }
}

function appendHistory(metric, value, timestamp) {
  if (typeof value !== "number" || !Number.isFinite(value)) return;
  metric.history = Array.isArray(metric.history) ? metric.history : [];
  metric.history.push({ t: timestamp, v: value });
  if (metric.history.length > HISTORY_LIMIT) metric.history.splice(0, metric.history.length - HISTORY_LIMIT);
}
function metricValue(snapshot, id, fallback = 0) { return snapshot.metrics[id]?.value ?? fallback; }

export class DiagnosticsStore {
  constructor(snapshot = loadDiagnosticsSnapshot()) {
    this.snapshot = snapshot;
    this.listeners = new Set();
    this.flushQueued = false;
    this.pendingChanged = new Set();
    this.collectorTimer = null;
    this.fpsHandle = null;
    this.ws = null;
    this.wsReady = false;
    this.lastFrame = 0;
    this.frameSamples = [];
    this.droppedFrames = 0;
    this.lastWakeEventKey = "";
  }

  subscribe(listener) {
    this.listeners.add(listener);
    listener(this.snapshot, { full: true, changed: Object.keys(this.snapshot.metrics) });
    return () => this.listeners.delete(listener);
  }
  getSnapshot() { return this.snapshot; }
  saveSnapshot() { storageSet(SNAPSHOT_STORAGE_KEY, JSON.stringify({ ...this.snapshot, updatedAt: now() })); }

  patchMetrics(values = {}, options = {}) {
    const timestamp = options.timestamp || now();
    for (const [id, value] of Object.entries(values)) {
      if (!METRIC_BY_ID[id]) continue;
      const existing = this.snapshot.metrics[id] || { value: METRIC_BY_ID[id].defaultValue, history: [] };
      existing.value = value;
      existing.updatedAt = timestamp;
      appendHistory(existing, Number(value), timestamp);
      this.snapshot.metrics[id] = existing;
      this.pendingChanged.add(id);
    }
    this.snapshot.updatedAt = timestamp;
    this.queueFlush();
  }

  patchTelemetry(payload = {}) {
    if (payload.distributions && typeof payload.distributions === "object") {
      this.snapshot.distributions = { ...this.snapshot.distributions, ...payload.distributions };
    }
    if (Array.isArray(payload.alerts)) {
      this.snapshot.alerts = payload.alerts;
    }
    if (payload.metrics && typeof payload.metrics === "object") {
      this.patchMetrics(payload.metrics, { timestamp: payload.updated_at ? Number(payload.updated_at) * 1000 : now() });
    } else {
      this.queueFlush({ full: true });
    }
  }

  patchLayout(layoutPatch = {}) {
    this.snapshot.layout = { ...this.snapshot.layout, ...layoutPatch };
    saveDiagnosticsLayout(this.snapshot.layout);
    this.queueFlush({ full: true });
  }
  setPreset(preset) { this.patchLayout({ preset, order: getPresetOrder(preset) }); }
  setTimeFilter(timeFilter) { this.snapshot.timeFilter = timeFilter; this.queueFlush({ full: true }); }

  patchModelStatus(status = {}) {
    const used = Number(status.tokens_used || 0);
    const budget = Number(status.token_budget || status.context_window_tokens || 0);
    const contextPct = budget > 0 ? Math.min(100, Math.round((used / budget) * 100)) : 0;
    const model = status.model_label || status.model_name || status.local_model || status.provider || "unknown";
    const output = Number(status.output_tokens || metricValue(this.snapshot, "ai.output_tokens", 0));
    this.snapshot.distributions.modelUsage[model] = (this.snapshot.distributions.modelUsage[model] || 0) + 1;
    this.patchMetrics({
      "ai.input_tokens": used,
      "ai.context_utilization_pct": contextPct,
      "model.top_model": model,
      "extra.token_efficiency_ratio": used > 0 ? Number((output / Math.max(1, used)).toFixed(2)) : metricValue(this.snapshot, "extra.token_efficiency_ratio", 0),
    });
  }

  patchWakeDiagnostics(data = {}, latencyMs = null) {
    const confidence = typeof data.last_confidence === "number" ? Math.round(data.last_confidence * 100) : metricValue(this.snapshot, "voice.wake_confidence_avg", 0);
    const reason = data.wake_reason || data.reason || "unknown";
    const eventKey = `${data.updated_at || ""}:${data.event_pending || ""}:${reason}`;
    const woke = (data.event_pending || data.current_state === "awake") && eventKey !== this.lastWakeEventKey;
    if (woke) this.lastWakeEventKey = eventKey;
    if (reason && reason !== "no_match") this.snapshot.distributions.wakeReasons[reason] = (this.snapshot.distributions.wakeReasons[reason] || 0) + 1;
    this.patchMetrics({
      "voice.wake_trigger_count": woke ? metricValue(this.snapshot, "voice.wake_trigger_count", 0) + 1 : metricValue(this.snapshot, "voice.wake_trigger_count", 0),
      "voice.wake_reason_breakdown": reason,
      "voice.wake_confidence_avg": confidence,
      "network.backend_response_ms": latencyMs == null ? metricValue(this.snapshot, "network.backend_response_ms", 0) : Math.round(latencyMs),
      "network.websocket_latency_ms": latencyMs == null ? metricValue(this.snapshot, "network.websocket_latency_ms", 0) : Math.round(latencyMs),
      "errors.api_error_rate": data.ok === false ? 1 : 0,
    });
  }

  patchRuntimeMetrics(fps) {
    const memory = performance?.memory;
    const ramPct = memory?.usedJSHeapSize && memory?.jsHeapSizeLimit ? Math.round((memory.usedJSHeapSize / memory.jsHeapSizeLimit) * 100) : metricValue(this.snapshot, "system.ram_pct", 0);
    this.patchMetrics({ "system.renderer_fps": Math.round(fps), "system.dropped_frames": this.droppedFrames, "system.ram_pct": ramPct, "extra.avatar_fps_jitter": Number(this.calculateJitter().toFixed(2)) });
  }
  calculateJitter() {
    if (this.frameSamples.length < 4) return 0;
    const avg = this.frameSamples.reduce((a, b) => a + b, 0) / this.frameSamples.length;
    return Math.sqrt(this.frameSamples.reduce((sum, sample) => sum + Math.pow(sample - avg, 2), 0) / this.frameSamples.length);
  }

  startCollectors({ getWakeDiagnostics, getLlmStatus, apiBase } = {}) {
    this.stopCollectors();
    this.startFpsCollector();
    this.tryWebSocket(apiBase);
    this.collectorTimer = setInterval(async () => {
      if (typeof getLlmStatus === "function") this.patchModelStatus(getLlmStatus() || {});
      if (typeof getWakeDiagnostics === "function") {
        const started = performance.now();
        const wake = await getWakeDiagnostics();
        this.patchWakeDiagnostics(wake || {}, performance.now() - started);
      }
      if (!this.wsReady && apiBase && typeof fetch === "function") {
        const started = performance.now();
        try {
          const res = await fetch(`${apiBase}/api/diagnostics/telemetry`, { cache: "no-store" });
          if (res.ok) {
            this.patchTelemetry(await res.json());
            this.patchMetrics({ "network.backend_response_ms": Math.round(performance.now() - started) });
          } else {
            this.patchMetrics({ "network.request_failure_rate": 1 });
          }
        } catch (_) {
          this.patchMetrics({ "network.request_failure_rate": 1 });
        }
      }
      this.saveSnapshot();
    }, 1000);
  }
  stopCollectors() {
    if (this.collectorTimer) clearInterval(this.collectorTimer);
    this.collectorTimer = null;
    if (this.fpsHandle) cancelAnimationFrame(this.fpsHandle);
    this.fpsHandle = null;
    if (this.ws) this.ws.close();
    this.ws = null;
    this.wsReady = false;
  }
  startFpsCollector() {
    let frames = 0;
    let lastSecond = performance.now();
    const tick = (ts) => {
      if (this.lastFrame) {
        const delta = ts - this.lastFrame;
        this.frameSamples.push(delta);
        if (this.frameSamples.length > 60) this.frameSamples.shift();
        if (delta > 40) this.droppedFrames += 1;
      }
      this.lastFrame = ts;
      frames += 1;
      if (ts - lastSecond >= 1000) {
        this.patchRuntimeMetrics((frames * 1000) / (ts - lastSecond));
        frames = 0;
        lastSecond = ts;
      }
      this.fpsHandle = requestAnimationFrame(tick);
    };
    this.fpsHandle = requestAnimationFrame(tick);
  }
  tryWebSocket(apiBase) {
    if (!apiBase || typeof WebSocket === "undefined") return;
    try {
      const started = performance.now();
      this.ws = new WebSocket(apiBase.replace(/^http/, "ws") + "/ws/telemetry");
      this.ws.onopen = () => { this.wsReady = true; this.patchMetrics({ "network.websocket_latency_ms": Math.round(performance.now() - started) }); };
      this.ws.onmessage = (event) => { try { this.patchTelemetry(JSON.parse(event.data)); } catch (_) {} };
      this.ws.onclose = () => { this.wsReady = false; this.patchMetrics({ "network.reconnect_count": metricValue(this.snapshot, "network.reconnect_count", 0) + 1 }); };
      this.ws.onerror = () => { this.wsReady = false; this.patchMetrics({ "network.request_failure_rate": 1 }); };
    } catch (_) { this.ws = null; }
  }

  queueFlush(extra = {}) {
    if (extra.full) this.pendingFull = true;
    if (this.flushQueued) return;
    this.flushQueued = true;
    scheduleUiPatch(() => {
      this.flushQueued = false;
      const patch = { full: !!this.pendingFull, changed: [...this.pendingChanged] };
      this.pendingFull = false;
      this.pendingChanged.clear();
      for (const listener of this.listeners) listener(this.snapshot, patch);
    });
  }
  exportCsv() {
    const lines = ["metric,label,value,unit,updated_at"];
    for (const def of METRIC_DEFINITIONS) {
      const metric = this.snapshot.metrics[def.id];
      lines.push([def.id, def.label, metric?.value ?? "", def.unit || "", metric?.updatedAt || ""].map((value) => JSON.stringify(value)).join(","));
    }
    return lines.join("\n");
  }
  exportJson() { return JSON.stringify(this.snapshot, null, 2); }
  metricsSummary() {
    return [
      `Context: ${metricValue(this.snapshot, "ai.context_utilization_pct", 0)}%`,
      `Renderer FPS: ${metricValue(this.snapshot, "system.renderer_fps", 0)}`,
      `Wake confidence: ${metricValue(this.snapshot, "voice.wake_confidence_avg", 0)}%`,
      `Backend response: ${metricValue(this.snapshot, "network.backend_response_ms", 0)}ms`,
      `Warnings/Critical: ${metricValue(this.snapshot, "alerts.warning", 0)} / ${metricValue(this.snapshot, "alerts.critical", 0)}`,
    ].join("\n");
  }
}
