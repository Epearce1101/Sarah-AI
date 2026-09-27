// Extracted from dashboard.js (improvement #7). Loaded as an ES module.
// API endpoints, perf markers and timestamp helpers shared by the renderer.

// -----------------------------------------------------------------------------
// API CONFIG
// -----------------------------------------------------------------------------

export const API_PORT = window.PY_PORT || 8907;
export const API_BASE = `http://127.0.0.1:${API_PORT}`;

export const ENDPOINTS = {
  chat: `${API_BASE}/api/chat`,
  tts: `${API_BASE}/api/tts`,
  stt: `${API_BASE}/api/stt`,
  llmMode: `${API_BASE}/api/llm_mode`,
  contextInfo: `${API_BASE}/api/context_info`,
  avatarContextWindow: `${API_BASE}/api/avatar/context_window`,
  health: `${API_BASE}/api/health`,
  wake: `${API_BASE}/api/wake`,
  wakeDiagnostics: `${API_BASE}/api/wake/diagnostics`,
  conversations: `${API_BASE}/api/conversations`,
  memories: `${API_BASE}/api/memories`,
  pinnedMemories: `${API_BASE}/api/memories/pinned`,
  projects: `${API_BASE}/api/projects`,
  skills: `${API_BASE}/api/skills`,
};

export const sleep = (ms) => new Promise((res) => setTimeout(res, ms));

export function sarahPerfStart(label) {
  if (!window.SARAH_PERF) return null;
  const start = performance.now();
  console.log(`[PERF] ${label}.start ${start.toFixed(1)}ms`);
  return start;
}

export function sarahPerfEnd(label, start) {
  if (!window.SARAH_PERF || start == null) return;
  const elapsed = performance.now() - start;
  console.log(`[PERF] ${label}.done ${elapsed.toFixed(1)}ms`);
}

// SQLite CURRENT_TIMESTAMP / Python utcnow().isoformat() are UTC but carry no
// zone marker, and `new Date("2026-01-01 12:00:00")` parses that as *local*
// time — shifting every displayed timestamp by the UTC offset. Tag bare
// timestamps as UTC before parsing.
export function parseDbTimestamp(value) {
  if (value instanceof Date) return value;
  const text = String(value ?? "").trim();
  if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(text)) {
    return new Date(`${text.replace(" ", "T")}Z`);
  }
  return new Date(text);
}

// Format a DB timestamp in the machine's own timezone (the name predates
// that; it used to hardcode America/Denver).
export function formatMountainTime(dateString, includeTime = true) {
  if (!dateString) return "Never";

  const date = parseDbTimestamp(dateString);
  const options = {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  };

  if (includeTime) {
    options.hour = "2-digit";
    options.minute = "2-digit";
    options.second = "2-digit";
    options.hour12 = true;
  }

  return new Intl.DateTimeFormat("en-US", options).format(date);
}
