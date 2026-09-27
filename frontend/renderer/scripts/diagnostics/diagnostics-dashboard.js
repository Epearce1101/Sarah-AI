import { METRIC_BY_ID, PANEL_BY_ID, PANEL_DEFINITIONS, PANEL_PRESETS, TIME_FILTERS, getPresetOrder } from "./diagnostics-registry.js";
import { DiagnosticsStore, loadDiagnosticsSnapshot, saveDiagnosticsLayout } from "./diagnostics-store.js";

const safeId = (id) => id.replace(/[^a-z0-9_-]/gi, "_");

// Issue #27: time filter chips (1h/24h/7d/30d) drive how much history
// the charts render. "custom" and unknown filters fall through to "no
// truncation" so the user sees the full retained window.
const TIME_RANGE_MS = {
  "1h": 60 * 60 * 1000,
  "24h": 24 * 60 * 60 * 1000,
  "7d": 7 * 24 * 60 * 60 * 1000,
  "30d": 30 * 24 * 60 * 60 * 1000,
};

function filterHistoryByTime(history, timeFilter) {
  if (!Array.isArray(history) || !history.length) return [];
  const range = TIME_RANGE_MS[timeFilter];
  if (!range) return history;
  const cutoff = Date.now() - range;
  const filtered = history.filter((p) => Number(p.t) >= cutoff);
  return filtered.length ? filtered : history.slice(-1);
}

function formatValue(def, value) {
  if (def?.format === "bool") return value ? "active" : "off";
  if (def?.format === "text") return value == null || value === "" ? "unknown" : String(value);
  if (def?.format === "currency") return `$${Number(value || 0).toFixed(3)}`;
  if (def?.format === "percent") return `${Math.round(Number(value || 0))}%`;
  if (def?.format === "ratio") return Number(value || 0).toFixed(2);
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value ?? "0");
  if (numeric >= 1000) return Math.round(numeric).toLocaleString();
  return Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(1);
}

function downloadText(filename, text, type = "text/plain") {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export class SarahDiagnosticsDashboard {
  constructor({ root, backend, apiBase, getLlmStatus } = {}) {
    this.root = root;
    this.backend = backend;
    this.apiBase = apiBase;
    this.getLlmStatus = getLlmStatus;
    this.store = new DiagnosticsStore(loadDiagnosticsSnapshot());
    this.metricEls = new Map();
    this.panelEls = new Map();
    this.unsubscribe = null;
    this.mounted = false;
    this.opened = false;
    this.draggedPanelId = null;
    this.lastOpenMs = 0;
    this.collectorStartTimer = null;
  }

  mount() {
    if (!this.root || this.mounted) return;
    this.root.innerHTML = this.buildShell();
    this.root.classList.add("hidden");
    this.root.setAttribute("aria-hidden", "true");
    this.bindShellEvents();
    this.renderPanels();
    this.unsubscribe = this.store.subscribe((snapshot, patch) => this.applySnapshot(snapshot, patch));
    this.mounted = true;
  }

  buildShell() {
    return `
      <div class="diagnostics-backdrop" data-diag-close="1"></div>
      <section class="diagnostics-window diagnostics-ops-console" role="dialog" aria-modal="true" aria-labelledby="diagnostics-title">
        <header class="diagnostics-window-header diagnostics-ops-header">
          <div>
            <div id="diagnostics-title" class="diagnostics-title">Sarah Ops Console</div>
            <div class="diagnostics-subtitle">cached first paint | live telemetry | modular panels</div>
          </div>
          <div class="diagnostics-header-actions">
            <span class="diagnostics-summary-badge" data-diag-alert-summary>normal</span>
            <button class="diagnostics-mini-btn" data-diag-action="copy-summary" type="button">Copy</button>
            <button class="diagnostics-mini-btn" data-diag-action="export-csv" type="button">CSV</button>
            <button class="diagnostics-mini-btn" data-diag-action="export-json" type="button">JSON</button>
            <button class="diagnostics-mini-btn" data-diag-action="screenshot" type="button">Shot</button>
            <button id="diagnostics-close" class="diagnostics-close-btn" data-diag-close="1" type="button">X</button>
          </div>
        </header>
        <div class="diagnostics-toolbar">
          <label>Preset <select class="diagnostics-select" data-diag-preset>${Object.keys(PANEL_PRESETS).map((id) => `<option value="${id}">${id.replace(/_/g, " ")}</option>`).join("")}</select></label>
          <div class="diagnostics-filter-row">${TIME_FILTERS.map((filter) => `<button class="diagnostics-chip" data-diag-time="${filter.id}" type="button">${filter.label}</button>`).join("")}</div>
          <button class="diagnostics-mini-btn" data-diag-action="compact" type="button">Compact</button>
          <button class="diagnostics-mini-btn" data-diag-action="debug" type="button">Debug</button>
          <button class="diagnostics-mini-btn" data-diag-action="save-layout" type="button">Save Layout</button>
          <button class="diagnostics-mini-btn" data-diag-action="reset-layout" type="button">Reset</button>
        </div>
        <div class="diagnostics-alert-strip" data-diag-alerts><span class="diagnostics-alert-normal">Telemetry online</span></div>
        <div class="diagnostics-grid-stack" id="sidebar-diagnostics" data-diag-grid></div>
      </section>`;
  }

  bindShellEvents() {
    this.root.addEventListener("click", (event) => {
      if (event.target.closest("[data-diag-close]")) return this.close();
      const action = event.target.closest("[data-diag-action]")?.dataset.diagAction;
      if (action) return this.handleAction(action);
      const time = event.target.closest("[data-diag-time]")?.dataset.diagTime;
      if (time) return this.setTimeFilter(time);
      const collapse = event.target.closest("[data-panel-collapse]")?.dataset.panelCollapse;
      if (collapse) return this.togglePanelState(collapse, "collapsed");
      const float = event.target.closest("[data-panel-float]")?.dataset.panelFloat;
      if (float) return this.togglePanelState(float, "floating");
    });
    this.root.querySelector("[data-diag-preset]")?.addEventListener("change", (event) => {
      this.store.setPreset(event.target.value);
      this.renderPanels();
      this.applySnapshot(this.store.getSnapshot(), { full: true });
    });
  }

  renderPanels() {
    const grid = this.root.querySelector("[data-diag-grid]");
    if (!grid) return;
    this.metricEls.clear();
    this.panelEls.clear();
    const layout = this.store.getSnapshot().layout;
    const order = layout.order?.length ? layout.order : getPresetOrder(layout.preset);
    grid.replaceChildren(...order.map((panelId) => this.createPanel(panelId)).filter(Boolean));
  }

  createPanel(panelId) {
    const panel = PANEL_BY_ID[panelId];
    if (!panel) return null;
    const article = document.createElement("article");
    const layout = this.store.getSnapshot().layout || {};
    article.className = `diagnostic-card diagnostics-panel accent-${panel.accent}`;
    if (layout.collapsed?.[panel.id]) article.classList.add("is-collapsed");
    if (layout.floating?.[panel.id]) article.classList.add("is-floating");
    article.dataset.panelId = panel.id;
    article.draggable = true;
    article.style.resize = "both";
    article.innerHTML = `
      <div class="diagnostic-card-title diagnostics-panel-title" draggable="true">
        <span>${panel.title}</span>
        <span class="diagnostic-state" data-panel-state="${panel.id}">live</span>
        <button class="diagnostics-panel-btn" data-panel-collapse="${panel.id}" type="button">Collapse</button>
        <button class="diagnostics-panel-btn" data-panel-float="${panel.id}" type="button">Float</button>
      </div>
      <canvas class="diagnostics-chart" width="340" height="82" data-chart-panel="${panel.id}"></canvas>
      <div class="diagnostic-grid diagnostics-metric-grid">${panel.metrics.map((metricId) => this.createMetricRow(metricId)).join("")}</div>`;
    article.addEventListener("dragstart", (event) => { this.draggedPanelId = panel.id; event.dataTransfer.effectAllowed = "move"; });
    article.addEventListener("dragover", (event) => event.preventDefault());
    article.addEventListener("drop", (event) => { event.preventDefault(); this.reorderPanel(this.draggedPanelId, panel.id); });
    this.panelEls.set(panel.id, article);
    return article;
  }

  createMetricRow(metricId) {
    const def = METRIC_BY_ID[metricId];
    if (!def) return "";
    return `<div class="diagnostic-row" data-metric-row="${metricId}"><span>${def.label}</span><b id="diag-metric-${safeId(metricId)}" data-metric-value="${metricId}">--</b></div>`;
  }

  open() {
    if (!this.root) return;
    if (!this.mounted) this.mount();
    const started = performance.now();
    this.root.classList.remove("hidden");
    this.root.setAttribute("aria-hidden", "false");
    this.opened = true;
    this.applySnapshot(this.store.getSnapshot(), { full: true });
    if (this.collectorStartTimer) clearTimeout(this.collectorStartTimer);
    this.collectorStartTimer = setTimeout(() => {
      if (!this.opened) return;
      this.store.startCollectors({ apiBase: this.apiBase, getLlmStatus: this.getLlmStatus, getWakeDiagnostics: () => this.backend?.getWakeDiagnostics?.() });
    }, 0);
    this.lastOpenMs = performance.now() - started;
    this.root.dataset.lastOpenMs = this.lastOpenMs.toFixed(2);
  }

  close() {
    if (!this.root) return;
    this.root.classList.add("hidden");
    this.root.setAttribute("aria-hidden", "true");
    this.opened = false;
    if (this.collectorStartTimer) clearTimeout(this.collectorStartTimer);
    this.collectorStartTimer = null;
    this.store.stopCollectors();
  }

  patchModelStatus(status) { this.store.patchModelStatus(status || {}); }

  applySnapshot(snapshot, patch = {}) {
    if (!this.opened && !patch.full) return;
    const changed = patch.full ? Object.keys(snapshot.metrics) : patch.changed || [];
    for (const metricId of changed) this.updateMetric(metricId, snapshot.metrics[metricId]?.value);
    this.updateHeader(snapshot);
    if (patch.full) this.syncTimeFilterChips(snapshot.timeFilter || "1h");
    const panels = patch.full ? PANEL_DEFINITIONS.map((p) => p.id) : [...new Set(changed.map((id) => METRIC_BY_ID[id]?.panel).filter(Boolean))];
    this.drawCharts(snapshot, panels);
  }

  syncTimeFilterChips(activeFilter) {
    this.root.querySelectorAll("[data-diag-time]").forEach((button) => button.classList.toggle("active", button.dataset.diagTime === activeFilter));
  }

  updateMetric(metricId, value) {
    const def = METRIC_BY_ID[metricId];
    if (!def) return;
    let el = this.metricEls.get(metricId);
    if (!el) { el = this.root.querySelector(`[data-metric-value="${metricId}"]`); if (el) this.metricEls.set(metricId, el); }
    if (el) el.textContent = formatValue(def, value);
  }

  updateHeader(snapshot) {
    const badge = this.root.querySelector("[data-diag-alert-summary]");
    if (!badge) return;
    const critical = Number(snapshot.metrics["alerts.critical"]?.value || 0);
    const warning = Number(snapshot.metrics["alerts.warning"]?.value || 0);
    badge.className = "diagnostics-summary-badge";
    if (critical > 0) { badge.textContent = `${critical} critical`; badge.classList.add("is-critical"); }
    else if (warning > 0) { badge.textContent = `${warning} warning`; badge.classList.add("is-warning"); }
    else { badge.textContent = "normal"; badge.classList.add("is-normal"); }
    const strip = this.root.querySelector("[data-diag-alerts]");
    if (strip) {
      const stale = Date.now() - Number(snapshot.updatedAt || 0) > 5000;
      if (critical > 0) strip.innerHTML = `<span class="diagnostics-alert-critical">Critical alert active</span>`;
      else if (warning > 0) strip.innerHTML = `<span class="diagnostics-alert-warning">Warning telemetry active</span>`;
      else if (stale) strip.innerHTML = `<span class="diagnostics-alert-warning">Stale data</span>`;
      else strip.innerHTML = `<span class="diagnostics-alert-normal">Telemetry online</span>`;
    }
  }

  drawCharts(snapshot, panelIds) {
    for (const panelId of panelIds) {
      const canvas = this.root.querySelector(`[data-chart-panel="${panelId}"]`);
      const panel = PANEL_BY_ID[panelId];
      if (!canvas || !panel) continue;
      this.drawChart(canvas, panel, snapshot);
    }
  }

  drawChart(canvas, panel, snapshot) {
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const width = canvas.width, height = canvas.height;
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "rgba(2, 7, 12, 0.45)";
    ctx.fillRect(0, 0, width, height);
    const colors = ["#4dd8ff", "#55f2a3", "#ffbc4a", "#ff4f7a", "#f457d7", "#8b5cf6"];
    const timeFilter = snapshot.timeFilter || "1h";
    const series = panel.metrics.slice(0, 6).map((metricId, idx) => ({
      metricId,
      color: colors[idx % colors.length],
      points: filterHistoryByTime(snapshot.metrics[metricId]?.history || [], timeFilter).map((p) => Number(p.v) || 0),
    }));
    if (panel.visual === "donut") return this.drawDonut(ctx, width, height, series);
    if (panel.visual === "histogram") return this.drawHistogram(ctx, width, height, series[0]);
    if (panel.visual === "heatmap") return this.drawHeatmap(ctx, width, height, snapshot.distributions.heatmap || []);
    this.drawSparkStack(ctx, width, height, series);
  }

  drawSparkStack(ctx, width, height, series) {
    const max = Math.max(1, ...series.flatMap((s) => s.points));
    series.forEach((s, idx) => {
      ctx.strokeStyle = s.color;
      ctx.lineWidth = idx === 0 ? 2 : 1;
      ctx.beginPath();
      const points = s.points.length ? s.points : [0];
      points.forEach((value, i) => {
        const x = (i / Math.max(1, points.length - 1)) * width;
        const y = height - (value / max) * (height - 10) - 5;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
    });
  }
  drawDonut(ctx, width, height, series) {
    const values = series.map((s) => Math.max(1, s.points.at(-1) || 1));
    const total = values.reduce((a, b) => a + b, 0);
    let angle = -Math.PI / 2;
    values.forEach((value, idx) => {
      const next = angle + (value / total) * Math.PI * 2;
      ctx.beginPath(); ctx.strokeStyle = series[idx].color; ctx.lineWidth = 12; ctx.arc(width / 2, height / 2, Math.min(width, height) / 3, angle, next); ctx.stroke(); angle = next;
    });
  }
  drawHistogram(ctx, width, height, series) {
    const points = series?.points?.slice(-24) || [0];
    const max = Math.max(1, ...points), barW = width / points.length;
    points.forEach((value, idx) => { const h = (value / max) * (height - 8); ctx.fillStyle = idx % 3 === 0 ? "#4dd8ff" : "#8b5cf6"; ctx.fillRect(idx * barW + 1, height - h, Math.max(1, barW - 2), h); });
  }
  drawHeatmap(ctx, width, height, values) {
    const cells = values.length ? values : Array.from({ length: 24 }, (_, i) => i % 5);
    const max = Math.max(1, ...cells), cellW = width / cells.length;
    cells.forEach((value, idx) => { ctx.fillStyle = `rgba(77, 216, 255, ${0.16 + (value / max) * 0.76})`; ctx.fillRect(idx * cellW + 1, 8, Math.max(1, cellW - 2), height - 16); });
  }

  reorderPanel(fromId, toId) {
    if (!fromId || !toId || fromId === toId) return;
    const layout = this.store.getSnapshot().layout;
    const order = [...layout.order];
    const from = order.indexOf(fromId), to = order.indexOf(toId);
    if (from < 0 || to < 0) return;
    order.splice(to, 0, order.splice(from, 1)[0]);
    this.store.patchLayout({ order });
    this.renderPanels();
    this.applySnapshot(this.store.getSnapshot(), { full: true });
  }

  togglePanelState(panelId, key) {
    const layout = this.store.getSnapshot().layout;
    const next = { ...(layout[key] || {}) };
    next[panelId] = !next[panelId];
    this.store.patchLayout({ [key]: next });
    const panel = this.panelEls.get(panelId) || this.root.querySelector(`[data-panel-id="${panelId}"]`);
    if (panel) panel.classList.toggle(`is-${key}`, !!next[panelId]);
  }
  setTimeFilter(timeFilter) {
    this.store.setTimeFilter(timeFilter);
    this.syncTimeFilterChips(timeFilter);
  }

  async handleAction(action) {
    if (action === "export-csv") return downloadText("sarah-diagnostics.csv", this.store.exportCsv(), "text/csv");
    if (action === "export-json") return downloadText("sarah-diagnostics.json", this.store.exportJson(), "application/json");
    if (action === "copy-summary") return navigator.clipboard?.writeText(this.store.metricsSummary());
    if (action === "compact") return this.root.querySelector(".diagnostics-window")?.classList.toggle("compact-mode");
    if (action === "debug") return this.root.querySelector(".diagnostics-window")?.classList.toggle("debug-mode");
    if (action === "save-layout") return saveDiagnosticsLayout(this.store.getSnapshot().layout);
    if (action === "reset-layout") { this.store.patchLayout({ preset: "full_telemetry", order: getPresetOrder("full_telemetry"), collapsed: {}, floating: {} }); this.renderPanels(); return this.applySnapshot(this.store.getSnapshot(), { full: true }); }
    if (action === "screenshot" && window.html2canvas) {
      const canvas = await window.html2canvas(this.root.querySelector(".diagnostics-window"));
      return canvas.toBlob((blob) => { const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = "sarah-diagnostics.png"; link.click(); URL.revokeObjectURL(url); });
    }
  }
}
