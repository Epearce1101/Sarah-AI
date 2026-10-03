// Bridge for the see-through screen overlay (renderer/overlay.html): marks and
// the pet-mode speech bubble arrive from the main process.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("sarahOverlay", {
  onMarks: (cb) => ipcRenderer.on("overlay:marks", (_e, payload) => cb(payload)),
  onClear: (cb) => ipcRenderer.on("overlay:clear", () => cb()),
  onBubble: (cb) => ipcRenderer.on("overlay:bubble", (_e, payload) => cb(payload)),
});
