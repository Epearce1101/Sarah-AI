// ============================================================================
// SARAH V10 — FULL PRELOAD BRIDGE
// Safe IPC bridge for:
//  - Chat + State
//  - Screenshot capture
//  - OCR
//  - AI Vision Description
//  - Multi-frame Recording Summarization
//  - UI events
// Sandbox-safe & fully compatible with your updated main.js
// ============================================================================

const { contextBridge, ipcRenderer } = require("electron");

// Backend base URL (dynamic port via launcher env, falls back to 8907)
const BACKEND_PORT = process.env.SARAH_PY_PORT || "8907";
const BACKEND_BASE = `http://127.0.0.1:${BACKEND_PORT}`;
const PERF_ENABLED = process.env.SARAH_PERF === "1";

// Expose port to the renderer so dashboard.js can build API_BASE dynamically
// (dashboard.js reads `window.PY_PORT || 8907`).
contextBridge.exposeInMainWorld("PY_PORT", BACKEND_PORT);
contextBridge.exposeInMainWorld("SARAH_PERF", PERF_ENABLED);

/**
 * Safe fetch wrapper (browser or Node fallback)
 */
const safeFetch = (...args) => {
  if (typeof fetch !== "undefined") return fetch(...args);
  return import("node-fetch").then(({ default: nodeFetch }) =>
    nodeFetch(...args)
  );
};

// ============================================================================
// BACKEND HELPERS (Chat + State)
// ============================================================================
async function callBackendChat(text) {
  const res = await safeFetch(`${BACKEND_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: text,
      from_creator: true,
    }),
  });

  if (!res.ok) throw new Error(`Backend error: ${res.status}`);
  return await res.json();
}

async function callBackendState() {
  try {
    const res = await safeFetch(`${BACKEND_BASE}/api/state`, {
      method: "GET",
      headers: { "Content-Type": "application/json" },
    });
    if (!res.ok) throw new Error(`Backend error: ${res.status}`);
    return await res.json();
  } catch (err) {
    console.error("[PRELOAD] Error fetching Sarah state:", err);
    return null;
  }
}

// ============================================================================
// CHAT + STATE API (window.sarahAPI)
// ============================================================================
contextBridge.exposeInMainWorld("sarahAPI", {
  sendMessage: async (text) => {
    try {
      return await callBackendChat(text);
    } catch (err) {
      console.error("[PRELOAD] Chat error:", err);
      return {
        reply:
          "Creator, I couldn't reach my backend brain. Please ensure the server is running.",
        emotion: "concerned",
        emotion_intensity: 0.8,
        affinity_to_creator: 1.0,
      };
    }
  },

  fetchState: async () => {
    return await callBackendState();
  },

  // UI actions
  newChat: () => ipcRenderer.send("ui:new-chat"),
  openLibrary: () => ipcRenderer.send("ui:open-library"),
  openProjects: () => ipcRenderer.send("ui:open-projects"),
  openScreenCapture: () => ipcRenderer.send("ui:open-screen-capture"),

  toggleVoice: (enabled) => ipcRenderer.send("ui:toggle-voice", enabled),
});

// ============================================================================
// SARAH VISION BRIDGE (Screenshot + Describe + OCR + Multi-frame Summary)
// Makes these available in window.sarahVision
// ============================================================================
contextBridge.exposeInMainWorld("sarahVision", {
  // Screenshot → PNG buffer
  captureScreen: () => ipcRenderer.invoke("capture-screen"),

  // desktopCapturer id of the primary screen (continuous watching).
  screenSourceId: () => ipcRenderer.invoke("screen-source-id"),
  // Where that screen is, in physical pixels.
  screenSourceDisplay: () => ipcRenderer.invoke("screen-source-display"),

  // Save image to disk (optional)
  saveScreenshot: (buffer) => ipcRenderer.invoke("save-screenshot", buffer),

  // AI Vision: Describe Screenshot
  describeImage: (buffer) => ipcRenderer.invoke("describe-image", buffer),

  // OCR
  ocrImage: (buffer) => ipcRenderer.invoke("ocr-image", buffer),

  // Multi-frame video summary
  summarizeVideoFrames: (frames) =>
    ipcRenderer.invoke("summarize-video-frames", frames),

  // Clipboard access (bypasses web security)
  readClipboard: () => ipcRenderer.invoke("read-clipboard"),
  writeClipboard: (text) => ipcRenderer.invoke("write-clipboard", text),
  readClipboardImage: () => ipcRenderer.invoke("read-clipboard-image"),
});

// ============================================================================
// ELECTRON UTILITY BRIDGE (Simple message receiver)
// ============================================================================
// App window / background mode (tray).
contextBridge.exposeInMainWorld("sarahApp", {
  getPrefs: () => ipcRenderer.invoke("app-prefs-get"),
  setPrefs: (patch) => ipcRenderer.invoke("app-prefs-set", patch),
  onWindowState: (callback) => ipcRenderer.on("sarah:window-state", (_event, state) => callback(state)),
  // You circled something on screen (Ctrl+Alt+Space): { image: dataURL crop, rect: physical px }.
  onPointerQuestion: (callback) => ipcRenderer.on("sarah:pointer-question", (_event, q) => callback(q)),
  setupChrome: () => ipcRenderer.invoke("setup-chrome-bridge"),
  listExtraAnimations: () => ipcRenderer.invoke("list-extra-animations"),
  setPetMode: (on) => ipcRenderer.invoke("pet-mode", Boolean(on)),
  petDrag: (phase, x, y) => ipcRenderer.send("pet-drag", { phase, x, y }),
  petHover: (over) => ipcRenderer.send("pet-hover", Boolean(over)),
  petScale: (factor) => ipcRenderer.send("pet-scale", Number(factor)),
  petStepAside: (dir) => ipcRenderer.send("pet-step-aside", Number(dir)),
  petPerch: (payload) => ipcRenderer.invoke("pet-perch", payload),
  petHome: () => ipcRenderer.send("pet-home"),
  systemIdleSeconds: () => ipcRenderer.invoke("system-idle"),
  quit: () => ipcRenderer.invoke("quit-app"),
  petReady: () => ipcRenderer.send("pet-ready"),
  screenPointToClient: (x, y) => ipcRenderer.invoke("screen-point-to-client", { x, y }),
  // Screen overlay: marks (physical screen px) and the pet speech bubble (screen DIPs).
  overlayMark: (payload) => ipcRenderer.invoke("overlay-mark", payload),
  overlayBubble: (payload) => ipcRenderer.invoke("overlay-bubble", payload),
});

contextBridge.exposeInMainWorld("electron", {
  receive: (channel, callback) => {
    ipcRenderer.on(channel, (_event, ...args) => callback(...args));
  },
});

// ============================================================================
// LEGACY BRIDGE (Backwards compatible for older dashboard functions)
// ============================================================================
contextBridge.exposeInMainWorld("electronAPI", {
  captureScreen: () => ipcRenderer.invoke("capture-screen"),
  saveScreenshot: (buffer) => ipcRenderer.invoke("save-screenshot", buffer),
  describeImage: (buffer) => ipcRenderer.invoke("describe-image", buffer),
  ocrImage: (buffer) => ipcRenderer.invoke("ocr-image", buffer),
});

// ============================================================================
// FILE DIALOG BRIDGE (Native file/folder selection)
// ============================================================================
contextBridge.exposeInMainWorld("sarahFiles", {
  // Open folder dialog - returns { canceled: bool, files: [{name, path, content, size}] }
  selectFolder: () => ipcRenderer.invoke("show-open-dialog-folder"),

  // Open file dialog - returns { canceled: bool, files: [{name, path, content, size}] }
  selectFiles: (multiple = true) => ipcRenderer.invoke("show-open-dialog-files", multiple),

  // Issue #31: launch VS Code at an absolute file path.
  openInVSCode: (absolutePath) => ipcRenderer.invoke("open-in-vscode", absolutePath),
});
