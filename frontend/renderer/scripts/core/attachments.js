// Extracted from dashboard.js (improvement #7). Loaded as an ES module.
// Registry for screenshots/recordings attached to chat messages.

// -----------------------------------------------------------------------------
// MULTIMODAL ATTACHMENT REGISTRY
// -----------------------------------------------------------------------------

window.SARAH_MULTIMODAL = window.SARAH_MULTIMODAL || {};

export function registerAttachment(kind, payload) {
  const id = `att_${Date.now()}_${Math.random().toString(16).slice(2)}`;
  window.SARAH_MULTIMODAL[id] = { kind, payload, ts: Date.now() };
  return id;
}

export function openAttachment(id) {
  const att = window.SARAH_MULTIMODAL[id];
  if (!att) return;

  if (att.kind === "screenshot" && att.payload) {
    if (typeof window.openScreenshotPreview === "function") {
      window.openScreenshotPreview(att.payload);
    }
  }

  if (att.kind === "recording" && att.payload?.lastFrame) {
    if (typeof window.openScreenshotPreview === "function") {
      window.openScreenshotPreview(att.payload.lastFrame);
    }
  }
}
