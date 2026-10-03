// ===========================================================
// SARAH V10 — FULL MULTIMODAL MAIN PROCESS
// Screenshot + OCR + Describe + Video Recording + Multi-frame Summary
// ===========================================================

const {
  app,
  BrowserWindow,
  session,
  desktopCapturer,
  ipcMain,
  dialog,
  clipboard,
  Menu,
  Tray,
  globalShortcut,
} = require("electron");

const path = require("path");
const fs = require("fs");
const fsp = fs.promises;
const { execFile } = require("child_process");
const { promisify } = require("util");
const Tesseract = require("tesseract.js");

const execFileAsync = promisify(execFile);

const PERF_ENABLED = process.env.SARAH_PERF === "1";
const PERF_LOG_PATH =
  process.env.SARAH_PERF_LOG ||
  path.resolve(__dirname, "..", "Backend", "SARAH_DRIVE", "logs", "frontend_perf.log");

function recordPerfMessage(message) {
  if (!PERF_ENABLED) return;

  const line = `${new Date().toISOString()} ${message}\n`;
  fsp
    .mkdir(path.dirname(PERF_LOG_PATH), { recursive: true })
    .then(() => fsp.appendFile(PERF_LOG_PATH, line, "utf8"))
    .catch((err) => console.warn("[PERF] Could not write frontend perf log:", err.message));
}

// ---------------------------------------------------------------------------
// FETCH FALLBACK FOR NODE < 18
// ---------------------------------------------------------------------------
let fetchFn = global.fetch;
if (!fetchFn) {
  fetchFn = (...args) =>
    import("node-fetch").then(({ default: fetch }) => fetch(...args));
}

// ---------------------------------------------------------------------------
// BACKEND BASE URL (dynamic port via launcher env)
// ---------------------------------------------------------------------------
const BACKEND_PORT = process.env.SARAH_PY_PORT || "8907";
const BACKEND_BASE = `http://127.0.0.1:${BACKEND_PORT}`;

// Per-run secret from the launcher; the backend rejects /api calls without it.
// Attached here (and to the session in installBackendAuth) so no renderer
// fetch needs to know about it.
const BACKEND_TOKEN = process.env.SARAH_API_TOKEN || "";
const BACKEND_TOKEN_HEADER = "X-Sarah-Token";

function backendHeaders(extra = {}) {
  return BACKEND_TOKEN ? { ...extra, [BACKEND_TOKEN_HEADER]: BACKEND_TOKEN } : extra;
}

function installBackendAuth(sess) {
  if (!BACKEND_TOKEN) return;
  const urls = [
    `http://127.0.0.1:${BACKEND_PORT}/*`,
    `http://localhost:${BACKEND_PORT}/*`,
    // Live voice runs over a WebSocket; its upgrade request needs the token too.
    `ws://127.0.0.1:${BACKEND_PORT}/*`,
    `ws://localhost:${BACKEND_PORT}/*`,
  ];
  sess.webRequest.onBeforeSendHeaders({ urls }, (details, callback) => {
    details.requestHeaders[BACKEND_TOKEN_HEADER] = BACKEND_TOKEN;
    callback({ requestHeaders: details.requestHeaders });
  });
}

// ---------------------------------------------------------------------------
// CHROMIUM FLAGS
// ---------------------------------------------------------------------------
app.commandLine.appendSwitch("enable-speech-dispatcher");
app.commandLine.appendSwitch("enable-speech-api");
app.commandLine.appendSwitch("enable-experimental-web-platform-features");
app.commandLine.appendSwitch("use-fake-ui-for-media-stream");
app.commandLine.appendSwitch("enable-usermedia-screen-capturing");

// ---------------------------------------------------------------------------
// MICROPHONE + MEDIA + CLIPBOARD PERMISSIONS
// ---------------------------------------------------------------------------
// One handler for every session. A second, narrower handler used to be set on
// `web-contents-created`, which replaced this one on the same default session
// and silently denied clipboard-read.
const GRANTED_PERMISSIONS = new Set([
  "microphone",
  "media",
  "clipboard-read",
  "clipboard-sanitized-write",
]);

function installPermissionHandler(sess) {
  sess.setPermissionRequestHandler((_wc, permission, cb) => {
    const granted = GRANTED_PERMISSIONS.has(permission);
    if (granted) console.log("[Electron] Permission granted:", permission);
    cb(granted);
  });
}

app.on("session-created", (sess) => {
  installPermissionHandler(sess);
  installBackendAuth(sess);
});

let mainWindow = null;

// ---------------------------------------------------------------------------
// BACKGROUND MODE: closing the window hides Sarah to the tray; she keeps
// running (her own time) until "Quit Sarah" in the tray menu. Camera, screen
// and mic are released while she's in the tray (the renderer does that on
// "sarah:window-state"). SARAH_BACKGROUND=0 turns it off for this run.
// ---------------------------------------------------------------------------
const PREFS_PATH = path.join(app.getPath("userData"), "sarah-app-prefs.json");
let appPrefs = { background: true };
try { appPrefs = { ...appPrefs, ...JSON.parse(fs.readFileSync(PREFS_PATH, "utf8")) }; } catch {}
let tray = null;
let quitting = false;
let trayHintShown = false;

function backgroundEnabled() {
  return process.env.SARAH_BACKGROUND !== "0" && appPrefs.background !== false;
}

function saveAppPrefs() {
  try { fs.writeFileSync(PREFS_PATH, JSON.stringify(appPrefs)); } catch (err) {
    console.warn("[Tray] could not save prefs:", err.message);
  }
}

function tellBackendTray(hidden) {
  fetchFn(`${BACKEND_BASE}/api/agency/background?hidden=${hidden}`, {
    method: "POST", headers: backendHeaders(),
  }).catch(() => {});
}

function setWindowState(inTray) {
  mainWindow?.webContents.send("sarah:window-state", { tray: inTray });
  tellBackendTray(inTray);
}

function hideToTray() {
  if (!mainWindow) return;
  mainWindow.hide();
  if (!petWindow) clearOverlays(); // nothing of hers stays on screen while she's away
  setWindowState(true);
  if (!trayHintShown && tray) {
    trayHintShown = true;
    tray.displayBalloon?.({
      title: "Sarah is still here",
      content: "She keeps working in the background. Right-click this icon to quit.",
      iconType: "info",
    });
  }
}

function showWindow() {
  if (!mainWindow) { createWindow(); return; }
  if (mainWindow.isMinimized()) mainWindow.restore();
  const wasHidden = !mainWindow.isVisible();
  mainWindow.show();
  mainWindow.focus();
  if (wasHidden) setWindowState(false);
}

function quitSarah() {
  quitting = true;
  app.quit();
}

// ---------------------------------------------------------------------------
// DESKTOP PET MODE: Sarah alone in a small see-through window that stays on
// top of games and apps (talk by voice). It's the same app in a compact
// layout (?pet=1); the main window hides and hands over camera/mic/voice
// meanwhile (like the tray, but she isn't "away").
// ---------------------------------------------------------------------------
let petWindow = null;
let petPerched = false;   // standing on the window you're using (not her own spot)
let petHome = null;       // where she was before perching
let petGlide = null;

function enterPetMode() {
  if (petWindow) { petWindow.show(); return; }
  const { screen } = require("electron");
  const area = screen.getPrimaryDisplay().workArea;
  // Small by default (about a quarter of her usual panel); your own size and
  // spot are remembered once you resize or move her.
  const saved = appPrefs.petBounds || {};
  const width = Math.max(120, saved.width || 170);
  const height = Math.max(200, saved.height || 290);
  const onScreen = saved.x != null && screen.getAllDisplays().some((d) => {
    const b = d.workArea;
    return saved.x >= b.x - 50 && saved.x < b.x + b.width - 50 && saved.y >= b.y - 50 && saved.y < b.y + b.height - 50;
  });
  petWindow = new BrowserWindow({
    width, height,
    x: onScreen ? saved.x : area.x + area.width - width - 16,
    y: onScreen ? saved.y : area.y + area.height - height,
    frame: false, transparent: true, backgroundColor: "#00000000", hasShadow: false,
    alwaysOnTop: true, skipTaskbar: true, resizable: true, minWidth: 120, minHeight: 200,
    icon: path.join(__dirname, "renderer", "assets", "sarah.ico"),
    // Keeps her render loop running before the window is first shown.
    webPreferences: { ...webPreferences(), backgroundThrottling: false },
    show: false, // shown once she's drawn in it (see revealPet), so there's no empty gap
  });
  petWindow.setAlwaysOnTop(true, "floating");
  // Clicks pass through the see-through space around her; the renderer
  // (which still gets mouse moves) says when the mouse is over her body.
  // Forwarding exists on Windows and macOS only; elsewhere the whole window
  // keeps taking the mouse as before.
  if (process.platform === "win32" || process.platform === "darwin") {
    petWindow.setIgnoreMouseEvents(true, { forward: true });
  }
  let saveTimer = null;
  const rememberBounds = () => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      if (!petWindow) return;
      const b = petWindow.getBounds();
      // Perching never moves her own spot, but a new size is still hers.
      appPrefs.petBounds = petPerched ? { ...(appPrefs.petBounds || {}), width: b.width, height: b.height } : b;
      saveAppPrefs();
    }, 500);
  };
  petWindow.on("resize", rememberBounds);
  petWindow.on("move", rememberBounds);
  petWindow.loadFile(path.join(__dirname, "renderer", "index.html"), { query: { pet: "1" } });
  petWindow.on("closed", () => {
    petWindow = null;
    clearOverlays(); // her bubble goes with her
    if (!quitting) showMainAfterPet();
  });
  // The main window hands over her senses now but stays on screen until the
  // pet window has her loaded.
  if (mainWindow) mainWindow.webContents.send("sarah:window-state", { tray: true, pet: true });
  setTimeout(revealPet, 6000); // in case "pet-ready" never comes
}

function revealPet() {
  if (!petWindow || petWindow.isVisible()) return;
  petWindow.show();
  if (mainWindow) mainWindow.hide();
}

ipcMain.on("pet-ready", (event) => {
  if (petWindow && event.sender === petWindow.webContents) revealPet();
});

function showMainAfterPet() {
  if (!mainWindow) { createWindow(); return; }
  mainWindow.show();
  mainWindow.focus();
  mainWindow.webContents.send("sarah:window-state", { tray: false, pet: true });
}

function leavePetMode() {
  if (petWindow) petWindow.close();  // "closed" brings the main window back
  else showWindow();
}

// Dragging her body moves the pet window (the renderer sends screen points).
let petDrag = null;
ipcMain.on("pet-drag", (_event, { phase, x, y } = {}) => {
  if (!petWindow) return;
  if (phase === "start") {
    clearInterval(petGlide);
    petPerched = false; // where you put her is her spot
    petDrag = { from: petWindow.getBounds(), x, y };
  } else if (phase === "move" && petDrag) {
    const { from } = petDrag;
    // setBounds with the size kept: setPosition alone lets a transparent
    // window creep in size on scaled displays.
    petWindow.setBounds({ x: Math.round(from.x + x - petDrag.x), y: Math.round(from.y + y - petDrag.y), width: from.width, height: from.height });
  } else {
    petDrag = null;
  }
});

// Glide the pet window to (x, y) in DIPs (perching, stepping aside, home).
function glidePet(x, y, ms = 380) {
  if (!petWindow || petWindow.isDestroyed()) return;
  const from = petWindow.getBounds();
  const started = Date.now();
  clearInterval(petGlide);
  petGlide = setInterval(() => {
    if (!petWindow || petWindow.isDestroyed()) return clearInterval(petGlide);
    const k = Math.min(1, (Date.now() - started) / ms);
    const e = k < 0.5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2;
    petWindow.setBounds({ x: Math.round(from.x + (x - from.x) * e), y: Math.round(from.y + (y - from.y) * e), width: from.width, height: from.height });
    if (k >= 1) clearInterval(petGlide);
  }, 16);
}

// How long since any keyboard/mouse input on the whole PC (her naps).
ipcMain.handle("system-idle", () => {
  const { powerMonitor } = require("electron");
  return powerMonitor.getSystemIdleTime();
});

// Your mouse lingers beside her: she steps out of the way (dir -1 left, 1 right).
ipcMain.on("pet-step-aside", (_event, dir) => {
  if (!petWindow || petWindow.isDestroyed()) return;
  const { screen } = require("electron");
  const b = petWindow.getBounds();
  const area = screen.getDisplayMatching(b).workArea;
  let x = b.x + Math.sign(dir || 1) * Math.round(b.width * 0.9);
  if (x < area.x || x + b.width > area.x + area.width) x = b.x - Math.sign(dir || 1) * Math.round(b.width * 0.9);
  glidePet(Math.max(area.x, Math.min(area.x + area.width - b.width, x)), b.y);
});

// Stand on top of the window you're using: rect in physical px; feetX/feetY
// are where her feet are inside the pet window (DIPs).
ipcMain.handle("pet-perch", (_event, { rect = [], feetX = 0, feetY = 0 } = {}) => {
  if (!petWindow || petWindow.isDestroyed()) return { ok: false };
  const { screen } = require("electron");
  const [rx, ry, rw, rh] = rect;
  if (![rx, ry, rw, rh, feetX, feetY].every(Number.isFinite)) return { ok: false };
  const r = process.platform === "win32" ? screen.screenToDipRect(null, { x: rx, y: ry, width: rw, height: rh }) : { x: rx, y: ry, width: rw, height: rh };
  const area = screen.getDisplayMatching(r).workArea;
  const b = petWindow.getBounds();
  const y = Math.round(r.y - feetY);
  if (y < area.y || r.width < b.width) return { ok: false, reason: "no room on top of it" };
  // Toward the right, clear of the window's own buttons.
  const x = Math.max(area.x, Math.min(area.x + area.width - b.width, Math.round(r.x + r.width - 190 - feetX)));
  // Her spot by where her feet were, so a resize while perched still lands right.
  if (!petPerched) petHome = { cx: b.x + b.width / 2, bottom: b.y + b.height };
  petPerched = true;
  if (Math.abs(b.x - x) > 2 || Math.abs(b.y - y) > 2) glidePet(x, y);
  return { ok: true };
});

ipcMain.on("pet-home", () => {
  if (petPerched && petHome && petWindow && !petWindow.isDestroyed()) {
    const b = petWindow.getBounds();
    glidePet(Math.round(petHome.cx - b.width / 2), Math.round(petHome.bottom - b.height));
  }
  petPerched = false;
});

// A full-screen game or presentation in front: she fades back (and comes
// back when it's gone).
ipcMain.on("pet-dim", (_event, on) => {
  if (!petWindow || petWindow.isDestroyed()) return;
  petWindow.setOpacity(on ? 0.35 : 1);
});

ipcMain.on("pet-hover", (_event, over) => {
  if (!petWindow || petWindow.isDestroyed()) return;
  if (process.platform !== "win32" && process.platform !== "darwin") return;
  petWindow.setIgnoreMouseEvents(!over, { forward: true });
});

// Mouse wheel over her: grow or shrink her, feet staying where they are.
ipcMain.on("pet-scale", (_event, factor) => {
  if (!petWindow || petWindow.isDestroyed() || !Number.isFinite(factor)) return;
  const b = petWindow.getBounds();
  const width = Math.round(Math.max(120, Math.min(900, b.width * factor)));
  const height = Math.max(200, Math.round((b.height * width) / b.width));
  petWindow.setBounds({ x: Math.round(b.x + (b.width - width) / 2), y: b.y + b.height - height, width, height });
});

// Right-click on the pet: close Sarah completely (same as "Quit Sarah").
ipcMain.handle("quit-app", () => quitSarah());

ipcMain.handle("pet-mode", (_event, on) => {
  if (on) enterPetMode(); else leavePetMode();
  return { pet: Boolean(on) };
});

// A point on the screen in physical pixels (from the backend's locate) ->
// the asking window's own coordinates, so pet-mode Sarah can point at it.
ipcMain.handle("screen-point-to-client", (event, { x, y } = {}) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (!win || !Number.isFinite(x) || !Number.isFinite(y)) return null;
  const { screen } = require("electron");
  const dip = process.platform === "win32" ? screen.screenToDipPoint({ x, y }) : { x, y };
  const area = win.getContentBounds();
  return { x: dip.x - area.x, y: dip.y - area.y };
});

function setupTray() {
  if (tray) return;
  tray = new Tray(path.join(__dirname, "renderer", "assets", "sarah.ico"));
  tray.setToolTip("Sarah");
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "Open Sarah", click: () => (petWindow ? leavePetMode() : showWindow()) },
    { label: "Desktop pet mode", click: () => (petWindow ? leavePetMode() : enterPetMode()) },
    { label: `Circle something for Sarah (${POINTER_KEY_LABEL})`, click: () => startPointer() },
    { type: "separator" },
    { label: "Quit Sarah", click: quitSarah },
  ]));
  tray.on("click", () => (petWindow ? petWindow.show() : showWindow()));
  tray.on("double-click", () => (petWindow ? leavePetMode() : showWindow()));
}

ipcMain.handle("app-prefs-get", () => ({ ...appPrefs, background: backgroundEnabled() }));
ipcMain.handle("app-prefs-set", (_event, patch = {}) => {
  if (typeof patch.background === "boolean") appPrefs.background = patch.background;
  saveAppPrefs();
  return { ...appPrefs, background: backgroundEnabled() };
});

// Extra moves: Mixamo .fbx / .vrma files dropped into the avatar's
// animations/mixamo folder (listed for the renderer, which can't read folders).
const EXTRA_ANIMATIONS = path.join(__dirname, "renderer", "assets", "vrm", "animations", "mixamo");
ipcMain.handle("list-extra-animations", () => {
  try {
    fs.mkdirSync(EXTRA_ANIMATIONS, { recursive: true });
    const readme = path.join(EXTRA_ANIMATIONS, "README.txt");
    if (!fs.existsSync(readme)) {
      fs.writeFileSync(readme, [
        "Drop animations here and restart Sarah: each file becomes a move she can do.",
        "",
        "From mixamo.com: pick an animation, Download with Format: FBX Binary (.fbx),",
        "Skin: Without Skin, Frames per second: 30, and tick \"In Place\" if it's offered.",
        "The file name is the move's name (\"Hip Hop Dancing.fbx\" -> she can do \"hip hop dancing\").",
        ".vrma files (VRM animations) work too.",
      ].join("\r\n"));
    }
    return fs.readdirSync(EXTRA_ANIMATIONS).filter((f) => /\.(fbx|vrma)$/i.test(f)).sort();
  } catch (err) {
    console.warn("[Animations] can't list extras:", err.message);
    return [];
  }
});

// Browser Bridge setup: open Chrome's extensions page and the extension
// folder, so loading it is "Developer mode -> Load unpacked -> this folder".
ipcMain.handle("setup-chrome-bridge", async () => {
  const { shell } = require("electron");
  const folder = path.resolve(__dirname, "..", "chrome-extension");
  const chrome = [
    process.env.PROGRAMFILES && path.join(process.env.PROGRAMFILES, "Google", "Chrome", "Application", "chrome.exe"),
    process.env["PROGRAMFILES(X86)"] && path.join(process.env["PROGRAMFILES(X86)"], "Google", "Chrome", "Application", "chrome.exe"),
    process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, "Google", "Chrome", "Application", "chrome.exe"),
  ].find((p) => p && fs.existsSync(p));
  if (chrome) {
    require("child_process").spawn(chrome, ["chrome://extensions/"], { detached: true, stdio: "ignore" }).unref();
  }
  clipboard.writeText(folder);
  await shell.openPath(folder);
  return { ok: true, folder, chrome: Boolean(chrome) };
});

// One Sarah: launching her again (desktop shortcut) shows the running one.
// (SARAH_SINGLE_INSTANCE=0: test runs alongside the real one.)
const isFirstInstance = process.env.SARAH_SINGLE_INSTANCE === "0" || app.requestSingleInstanceLock();
if (!isFirstInstance) {
  app.quit();
} else {
  app.on("second-instance", () => (petWindow ? leavePetMode() : showWindow()));
}

app.on("before-quit", () => { quitting = true; });

// ---------------------------------------------------------------------------
// WINDOW CREATION
// ---------------------------------------------------------------------------
function webPreferences() {
  return {
    preload: path.resolve(__dirname, "preload.js"),
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: false,
    // Same-origin policy stays on: backend calls work through its CORS
    // headers, and a hijacked page can't read arbitrary file:// paths.
    webSecurity: true,
    nodeIntegrationInSubFrames: false,
    allowRunningInsecureContent: false,
    enableRemoteModule: false,
    backgroundThrottling: false,
    media: true,
    audio: true,
    video: false,
    experimentalFeatures: true,
    autoplayPolicy: "no-user-gesture-required",
    enableBlinkFeatures: 'ClipboardRead,ClipboardWrite',
  };
}

function createWindow() {
  // Clear cache only (NOT storage data - we need localStorage for conversation restore)
  session.defaultSession.clearCache();
  // REMOVED: session.defaultSession.clearStorageData() - this was wiping localStorage on every restart!

  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 1000,
    minHeight: 700,
    backgroundColor: "#050509",
    autoHideMenuBar: true,
    icon: path.join(__dirname, "renderer", "assets", "sarah.ico"),
    useContentSize: true,
    webPreferences: webPreferences(),
  });

  if (PERF_ENABLED) {
    recordPerfMessage("[PERF] frontend.perf_logging.enabled");
    mainWindow.webContents.on("console-message", (_event, _level, message) => {
      if (typeof message === "string" && message.startsWith("[PERF]")) {
        console.log(`[RENDERER] ${message}`);
        recordPerfMessage(message);
      }
    });
  }

  mainWindow.loadFile(path.join(__dirname, "renderer", "index.html"));

  mainWindow.on("ready-to-show", () => mainWindow.show());
  mainWindow.on("close", (event) => {
    if (!quitting && backgroundEnabled()) {
      event.preventDefault();
      hideToTray();
    }
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
    // The see-through overlays must not keep the app alive on their own.
    if (!petWindow) resetOverlays();
  });
}

// ---------------------------------------------------------------------------
// MENU SETUP - Required for Ctrl+V/C/X to work even when autoHideMenuBar: true
// ---------------------------------------------------------------------------
function setupMenu() {
  const template = [
    {
      label: "Edit",
      submenu: [
        { role: "undo" },
        { role: "redo" },
        { type: "separator" },
        { role: "cut" },
        { role: "copy" },
        { role: "paste" },
        { role: "pasteAndMatchStyle" },
        { role: "selectAll" },
      ],
    },
    {
      label: "View",
      submenu: [
        { role: "reload" },
        { role: "forceReload" },
        { role: "toggleDevTools" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
  ];

  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
  console.log("[Electron] Edit menu with paste shortcuts registered");
}

app.whenReady().then(() => {
  if (!isFirstInstance) return;
  setupMenu(); // Must be called BEFORE createWindow

  // The default session can predate the session-created listener; both
  // installers replace rather than stack, so repeating them is harmless.
  installPermissionHandler(session.defaultSession);
  installBackendAuth(session.defaultSession);

  // Issue #10: getDisplayMedia() (used by Start Recording in screen-capture
  // tab) silently fails in modern Electron unless a display-media request
  // handler is registered. Auto-pick the first screen source so the
  // recording flow works without showing a source-picker UI.
  session.defaultSession.setDisplayMediaRequestHandler((_request, callback) => {
    desktopCapturer.getSources({ types: ["screen"] }).then((sources) => {
      if (!sources.length) {
        callback({});
        return;
      }
      callback({ video: sources[0], audio: "loopback" });
    }).catch((err) => {
      console.warn("[Electron] setDisplayMediaRequestHandler failed:", err);
      callback({});
    });
  }, { useSystemPicker: false });

  createWindow();
  setupTray();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

// ===========================================================================
// Issue #31: Open file in VS Code
// Tries `code --goto <path>` first; falls back to the vscode://file URI via
// shell.openExternal so a user with the Insiders build or a custom install
// still gets a reasonable launch path.
// ===========================================================================
ipcMain.handle("open-in-vscode", async (_event, filePath) => {
  if (!filePath || typeof filePath !== "string") {
    return { ok: false, error: "no_path" };
  }
  const target = path.normalize(filePath);
  try {
    if (!fs.existsSync(target)) {
      return { ok: false, error: "not_found", path: target };
    }
  } catch (err) {
    return { ok: false, error: "stat_failed", message: String(err) };
  }

  const tryLaunch = (cmd, args) =>
    new Promise((resolve) => {
      execFile(cmd, args, { windowsHide: true, shell: false }, (err) => {
        resolve(!err);
      });
    });

  // 1) Try `code` (and Windows `code.cmd`) directly.
  const candidates = process.platform === "win32"
    ? ["code.cmd", "code"]
    : ["code"];
  for (const cmd of candidates) {
    const ok = await tryLaunch(cmd, ["--goto", target]);
    if (ok) return { ok: true, path: target, launcher: cmd };
  }

  // 2) Fall back to vscode://file URI.
  try {
    const { shell } = require("electron");
    const uri = `vscode://file/${target.replace(/\\/g, "/")}`;
    await shell.openExternal(uri);
    return { ok: true, path: target, launcher: "uri" };
  } catch (err) {
    return { ok: false, error: "launch_failed", message: String(err) };
  }
});

// ===========================================================================
// 1️⃣ SCREENSHOT CAPTURE HANDLER
// ===========================================================================
ipcMain.handle("capture-screen", async () => {
  const sources = await desktopCapturer.getSources({
    types: ["screen"],
    thumbnailSize: { width: 1920, height: 1080 },
  });

  if (!sources[0]) return null;
  return sources[0].thumbnail.toPNG();
});

// Sarah's eyes watch the screen continuously. getDisplayMedia() needs a user
// click, so the renderer opens the primary screen through getUserMedia with
// this desktopCapturer source id instead (no gesture needed).
ipcMain.handle("screen-source-id", async () => {
  const sources = await desktopCapturer.getSources({ types: ["screen"], thumbnailSize: { width: 0, height: 0 } });
  return sources[0]?.id || null;
});

// Where the screen her eyes watch is, in physical pixels (to map a spot in a
// grabbed frame back onto the real screen).
ipcMain.handle("screen-source-display", async () => {
  const { screen } = require("electron");
  const sources = await desktopCapturer.getSources({ types: ["screen"], thumbnailSize: { width: 0, height: 0 } });
  const id = String(sources[0]?.display_id || "");
  const display = screen.getAllDisplays().find((d) => String(d.id) === id) || screen.getPrimaryDisplay();
  const r = process.platform === "win32" ? screen.dipToScreenRect(null, display.bounds) : display.bounds;
  return { x: r.x, y: r.y, width: r.width, height: r.height };
});

// ===========================================================================
// SCREEN OVERLAY: one see-through, click-through window per display, on top
// of everything, where she circles / underlines / points at what she's
// talking about, and (pet mode) shows her speech bubble. Hidden from screen
// capture so her own eyes never see her marks.
// ===========================================================================
const overlays = new Map(); // display id -> BrowserWindow

function overlayFor(display) {
  let win = overlays.get(display.id);
  if (win && !win.isDestroyed()) return win;
  win = new BrowserWindow({
    ...display.bounds,
    frame: false, transparent: true, backgroundColor: "#00000000", hasShadow: false,
    alwaysOnTop: true, skipTaskbar: true, focusable: false, resizable: false, movable: false,
    fullscreenable: false, show: false,
    webPreferences: {
      preload: path.join(__dirname, "overlay-preload.js"), contextIsolation: true, nodeIntegration: false,
      // Covered or not, its timers and frames must keep running so marks fade on time.
      backgroundThrottling: false,
    },
  });
  win.setIgnoreMouseEvents(true);
  win.setAlwaysOnTop(true, "screen-saver");
  win.setVisibleOnAllWorkspaces?.(true, { visibleOnFullScreen: true });
  win.setContentProtection(true);
  win.loadFile(path.join(__dirname, "renderer", "overlay.html"));
  win.on("closed", () => { clearTimeout(win._failsafe); overlays.delete(display.id); });
  win._display = display;
  overlays.set(display.id, win);
  return win;
}

// Nothing may outlive its time: besides the overlay page's own timers, the
// main process wipes and hides each overlay window a moment after the last
// thing on it should have gone, even if that page has stalled.
const OVERLAY_MAX_S = 15;
const overlaySeconds = (s, fallback) => Math.max(1.5, Math.min(OVERLAY_MAX_S, Number(s) || fallback));
function extendOverlay(win, seconds) {
  const until = Date.now() + seconds * 1000 + 1200;
  if (win._until && win._until >= until) return;
  win._until = until;
  clearTimeout(win._failsafe);
  win._failsafe = setTimeout(() => {
    if (win.isDestroyed()) return;
    win._until = 0;
    win._bubbleDone = win._bubble;
    win._bubble = "";
    win.webContents.send("overlay:clear");
    win.hide();
  }, until - Date.now());
}

function sendToOverlay(win, channel, payload, seconds) {
  if (seconds) extendOverlay(win, seconds);
  const go = () => {
    if (win.isDestroyed()) return;
    if (!win.isVisible()) win.showInactive();
    win.webContents.send(channel, payload);
  };
  if (win.webContents.isLoading()) win.webContents.once("did-finish-load", go); else go();
}

function clearOverlays() {
  for (const win of overlays.values()) {
    if (win.isDestroyed()) continue;
    clearTimeout(win._failsafe);
    win._until = 0;
    if (win._bubble) win._bubbleDone = win._bubble;
    win._bubble = "";
    win.webContents.send("overlay:clear");
    win.hide();
  }
}

function resetOverlays() {
  for (const win of overlays.values()) if (!win.isDestroyed()) win.destroy();
  overlays.clear();
}

// The overlay page reports when nothing is left on it.
ipcMain.on("overlay-idle", (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (!win || win.isDestroyed() || win._pointer) return;
  clearTimeout(win._failsafe);
  win._until = 0;
  if (win._bubble) { win._bubbleDone = win._bubble; win._bubble = ""; }
  win.hide();
});

// Marks in physical screen pixels -> each display's overlay, in its own DIPs.
ipcMain.handle("overlay-mark", (_event, { marks = [], seconds = 6, clear = false } = {}) => {
  const { screen } = require("electron");
  if (clear) {
    clearOverlays();
    return { ok: true };
  }
  seconds = overlaySeconds(seconds, 6);
  const perDisplay = new Map();
  for (const m of marks) {
    const [x, y, w, h] = m.rect || [];
    if (![x, y, w, h].every(Number.isFinite)) continue;
    const r = process.platform === "win32" ? screen.screenToDipRect(null, { x, y, width: w, height: h }) : { x, y, width: w, height: h };
    const display = screen.getDisplayMatching(r);
    const local = { ...m, rect: [r.x - display.bounds.x, r.y - display.bounds.y, r.width, r.height] };
    if (!perDisplay.has(display.id)) perDisplay.set(display.id, { display, marks: [] });
    perDisplay.get(display.id).marks.push(local);
  }
  for (const { display, marks: list } of perDisplay.values()) {
    sendToOverlay(overlayFor(display), "overlay:marks", { marks: list, seconds }, seconds);
  }
  return { ok: perDisplay.size > 0 };
});

// Pet mode speech bubble at a screen point (DIPs, from the pet window).
ipcMain.handle("overlay-bubble", (_event, { text = "", x, y, seconds } = {}) => {
  const { screen } = require("electron");
  if (!Number.isFinite(x) || !Number.isFinite(y)) return { ok: false };
  const words = String(text || "").trim();
  const display = screen.getDisplayNearestPoint({ x: Math.round(x), y: Math.round(y) });
  for (const [id, win] of overlays) {
    if (id !== display.id && !win.isDestroyed()) { win._bubble = ""; win.webContents.send("overlay:bubble", { text: "" }); }
  }
  const win = overlays.get(display.id);
  if (!words) {
    if (win && !win.isDestroyed()) { win._bubble = ""; win.webContents.send("overlay:bubble", { text: "" }); }
    return { ok: true };
  }
  // Words whose time is up stay gone, even as she keeps moving.
  if (win && !win.isDestroyed() && win._bubbleDone === words) return { ok: true };
  const target = overlayFor(display);
  // Only new words set the deadline; following her around doesn't extend it.
  const fresh = target._bubble !== words;
  target._bubble = words;
  if (fresh) target._bubbleDone = "";
  const secs = fresh ? overlaySeconds(seconds, Math.min(12, 2.5 + words.length * 0.055)) : 0;
  sendToOverlay(target, "overlay:bubble", { text: words, x: x - display.bounds.x, y: y - display.bounds.y, seconds }, secs);
  return { ok: true };
});

app.whenReady().then(() => {
  const { screen } = require("electron");
  for (const ev of ["display-added", "display-removed", "display-metrics-changed"]) screen.on(ev, resetOverlays);
  try {
    if (!globalShortcut.register(POINTER_KEY, () => startPointer())) console.warn(`[pointer] ${POINTER_KEY} is taken by another app`);
  } catch (err) {
    console.warn("[pointer] shortcut unavailable:", err);
  }
});
app.on("will-quit", () => { try { globalShortcut.unregisterAll(); } catch {} });

// ===========================================================================
// CIRCLE SOMETHING FOR HER: press the shortcut (or the tray item), draw
// around anything on any screen, and she's asked "what's this?" with a
// picture of exactly that spot. Esc, a right-click or 30 s cancels.
// ===========================================================================
const POINTER_KEY = "CommandOrControl+Alt+Space";
const POINTER_KEY_LABEL = process.platform === "darwin" ? "Cmd+Option+Space" : "Ctrl+Alt+Space";
let pointerActive = false;
let pointerTimer = null;

function startPointer() {
  if (pointerActive) return;
  const { screen } = require("electron");
  pointerActive = true;
  for (const display of screen.getAllDisplays()) {
    const win = overlayFor(display);
    clearTimeout(win._failsafe);
    win._until = 0;
    win._pointer = true;
    win.setIgnoreMouseEvents(false);
    win.setFocusable(true);
    sendToOverlay(win, "overlay:pointer", { on: true, key: POINTER_KEY_LABEL });
  }
  const here = screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
  setTimeout(() => overlays.get(here.id)?.focus(), 60); // so Esc reaches it
  clearTimeout(pointerTimer);
  pointerTimer = setTimeout(endPointer, 30000); // never stuck in drawing mode
}

function endPointer() {
  pointerActive = false;
  clearTimeout(pointerTimer);
  for (const win of overlays.values()) {
    if (win.isDestroyed()) continue;
    win._pointer = false;
    win.setIgnoreMouseEvents(true);
    win.setFocusable(false);
    win.webContents.send("overlay:pointer", { on: false });
    extendOverlay(win, 3); // the stroke fades, then the window hides
  }
}

ipcMain.on("overlay-pointer-cancel", () => endPointer());

ipcMain.on("overlay-pointer-done", async (event, { rect } = {}) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  endPointer();
  const display = win?._display;
  if (!display || !Array.isArray(rect) || !rect.every(Number.isFinite)) return;
  const { screen } = require("electron");
  const [lx, ly, lw, lh] = rect;
  const dip = { x: display.bounds.x + lx, y: display.bounds.y + ly, width: lw, height: lh };
  const phys = process.platform === "win32" ? screen.dipToScreenRect(null, dip) : dip;
  let image = null;
  try {
    // A still of that display at full resolution, cropped to the circle
    // plus a margin (her own overlay is excluded from captures).
    const scale = display.scaleFactor || 1;
    const size = { width: Math.round(display.size.width * scale), height: Math.round(display.size.height * scale) };
    const sources = await desktopCapturer.getSources({ types: ["screen"], thumbnailSize: size });
    const src = sources.find((s) => String(s.display_id) === String(display.id)) || sources[0];
    if (src) {
      const shot = src.thumbnail;
      const k = shot.getSize().width / display.size.width;
      const pad = 32;
      const x = Math.max(0, Math.round((lx - pad) * k));
      const y = Math.max(0, Math.round((ly - pad) * k));
      const w = Math.min(shot.getSize().width - x, Math.round((lw + pad * 2) * k));
      const h = Math.min(shot.getSize().height - y, Math.round((lh + pad * 2) * k));
      let crop = shot.crop({ x, y, width: w, height: h });
      if (crop.getSize().width > 1400) crop = crop.resize({ width: 1400 });
      image = crop.toDataURL();
    }
  } catch (err) {
    console.warn("[pointer] capture failed:", err);
  }
  let target = petWindow && !petWindow.isDestroyed() ? petWindow : mainWindow;
  if (!target) return;
  if (target === mainWindow && !mainWindow.isVisible()) showWindow();
  target.webContents.send("sarah:pointer-question", { image, rect: [phys.x, phys.y, phys.width, phys.height] });
});

// ===========================================================================
// 2️⃣ SAVE SCREENSHOT
// ===========================================================================
ipcMain.handle("save-screenshot", async (_, imgBuffer) => {
  const result = await dialog.showSaveDialog({
    title: "Save Screenshot",
    defaultPath: "sarah_screenshot.png",
    filters: [{ name: "Images", extensions: ["png"] }],
  });

  if (result.canceled) return null;
  await fsp.writeFile(result.filePath, imgBuffer);
  return result.filePath;
});

// ===========================================================================
// 3️⃣ IMAGE DESCRIPTION (VISION ENDPOINT)
// ===========================================================================
ipcMain.handle("describe-image", async (_, imgBuffer) => {
  if (!imgBuffer) return "No image buffer received.";

  const base64 = imgBuffer.toString("base64");

  try {
    const response = await fetchFn(`${BACKEND_BASE}/api/vision`, {
      method: "POST",
      headers: backendHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        prompt: "Describe this image in detail.",
        image: base64,
      }),
    });

    const json = await response.json().catch(() => null);
    return json?.description || "No description available.";
  } catch (err) {
    console.error("[describe-image] Vision error:", err);
    return "Vision backend unavailable.";
  }
});

// ===========================================================================
// 4️⃣ OCR (Tesseract.js)
// ===========================================================================
ipcMain.handle("ocr-image", async (_, imgBuffer) => {
  if (!imgBuffer) return "(no image for OCR)";

  const tempPath = path.join(app.getPath("temp"), "sarah_ocr.png");
  await fsp.writeFile(tempPath, imgBuffer);

  try {
    const result = await Tesseract.recognize(tempPath, "eng", {
      logger: (m) =>
        m.status && console.log("[OCR]", m.status, m.progress),
    });

    return result.data?.text || "(no text found)";
  } catch (err) {
    console.error("[OCR] failure:", err);
    return "(OCR error)";
  }
});

// ===========================================================================
// 5️⃣ MULTI-FRAME VIDEO SUMMARY (for recording analysis)
// ===========================================================================
ipcMain.handle("summarize-video-frames", async (_, frameBuffers) => {
  if (!frameBuffers || !frameBuffers.length) {
    return "No frames provided for summary.";
  }

  // Convert buffers to base64 strings
  const framesBase64 = frameBuffers.map((buf) =>
    buf instanceof Buffer ? buf.toString("base64") : Buffer.from(buf).toString("base64")
  );

  try {
    const response = await fetchFn(`${BACKEND_BASE}/api/video-summary`, {
      method: "POST",
      headers: backendHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        prompt:
          "Summarize what is happening across these video frames. Identify actions, objects, and changes over time.",
        frames: framesBase64,
      }),
    });

    const json = await response.json().catch(() => null);
    return json?.summary || "No summary received from backend.";
  } catch (err) {
    console.error("[summarize-video-frames] Vision error:", err);
    return "Video-summary backend unavailable.";
  }
});

// ===========================================================================
// 6️⃣ CLIPBOARD HANDLERS (Native clipboard access)
// ===========================================================================
ipcMain.handle("read-clipboard", async () => {
  try {
    // Debug: Check all available formats
    const formats = clipboard.availableFormats();
    console.log("[Clipboard] Available formats:", formats);

    // Try Electron's clipboard - multiple formats
    let text = clipboard.readText();
    console.log("[Clipboard] readText() length:", text?.length || 0);

    // Try reading from selection clipboard (Linux) or other sources
    if (!text || text.length === 0) {
      text = clipboard.readText('selection');
      console.log("[Clipboard] readText('selection') length:", text?.length || 0);
    }

    // Try reading HTML and extracting text
    if (!text || text.length === 0) {
      const html = clipboard.readHTML();
      if (html && html.length > 0) {
        // Strip HTML tags to get plain text
        text = html.replace(/<[^>]*>/g, '').trim();
        console.log("[Clipboard] Extracted from HTML length:", text?.length || 0);
      }
    }

    // Try reading RTF
    if (!text || text.length === 0) {
      const rtf = clipboard.readRTF();
      if (rtf && rtf.length > 0) {
        // Basic RTF to text (strip RTF codes)
        text = rtf.replace(/\{\\[^{}]*\}|\\[a-z]+\d* ?/gi, '').trim();
        console.log("[Clipboard] Extracted from RTF length:", text?.length || 0);
      }
    }

    // If still empty, try PowerShell as fallback (Windows-specific)
    if (!text || text.length === 0) {
      console.log("[Clipboard] Trying PowerShell fallback...");
      try {
        const { stdout } = await execFileAsync("powershell", ["-NoProfile", "-Command", "Get-Clipboard"], {
          encoding: "utf8",
          timeout: 3000,
          windowsHide: true,
        });
        text = stdout.trim();
        console.log("[Clipboard] PowerShell clipboard length:", text?.length || 0);
      } catch (psErr) {
        console.error("[Clipboard] PowerShell error:", psErr.message);
      }
    }

    console.log("[Clipboard] Final text length:", text?.length || 0);
    if (text && text.length > 0) {
      console.log("[Clipboard] First 100 chars:", text.substring(0, 100));
    }
    return text || "";
  } catch (err) {
    console.error("[Clipboard] Read error:", err);
    return "";
  }
});

ipcMain.handle("write-clipboard", async (_, text) => {
  try {
    clipboard.writeText(text);
    console.log("[Clipboard] Wrote text, length:", text.length);
    return true;
  } catch (err) {
    console.error("[Clipboard] Write error:", err);
    return false;
  }
});

// Read clipboard IMAGE (for Snipping Tool, screenshots, etc.)
ipcMain.handle("read-clipboard-image", async () => {
  try {
    const formats = clipboard.availableFormats();
    console.log("[Clipboard Image] Available formats:", formats);

    // Check if there's an image
    if (!formats.some(f => f.startsWith('image/'))) {
      console.log("[Clipboard Image] No image in clipboard");
      return null;
    }

    // Read the image as NativeImage
    const nativeImage = clipboard.readImage();
    if (nativeImage.isEmpty()) {
      console.log("[Clipboard Image] Image is empty");
      return null;
    }

    // Convert to PNG base64 data URL
    const pngBuffer = nativeImage.toPNG();
    const base64 = pngBuffer.toString('base64');
    const dataUrl = `data:image/png;base64,${base64}`;

    console.log("[Clipboard Image] Read image, size:", pngBuffer.length, "bytes,", nativeImage.getSize().width, "x", nativeImage.getSize().height);

    return {
      dataUrl: dataUrl,
      size: pngBuffer.length,
      width: nativeImage.getSize().width,
      height: nativeImage.getSize().height
    };
  } catch (err) {
    console.error("[Clipboard Image] Read error:", err);
    return null;
  }
});

// ===========================================================================
// 7️⃣ FILE/FOLDER DIALOG HANDLERS (for Project uploads)
// ===========================================================================
ipcMain.handle("show-open-dialog-folder", async () => {
  try {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: "Select Folder to Upload",
      properties: ["openDirectory"],
    });

    if (result.canceled || !result.filePaths.length) {
      console.log("[Dialog] Folder selection canceled");
      return { canceled: true, files: [] };
    }

    const folderPath = result.filePaths[0];
    console.log("[Dialog] Selected folder:", folderPath);

    // Recursively read all files in the folder
    const files = [];
    const excludedDirs = new Set(["node_modules", ".git", "__pycache__", ".venv", "venv"]);
    const binaryExts = new Set([
      ".exe", ".dll", ".so", ".dylib", ".bin", ".pyc", ".pyo", ".whl", ".egg",
      ".zip", ".tar", ".gz", ".rar", ".7z", ".png", ".jpg", ".jpeg", ".gif",
      ".ico", ".bmp", ".mp3", ".mp4", ".wav", ".avi", ".mov", ".pdf",
    ]);

    const readDir = async (dir, baseDir) => {
      const entries = await fsp.readdir(dir, { withFileTypes: true });
      for (const entry of entries) {
        const fullPath = path.join(dir, entry.name);
        const relativePath = path.relative(baseDir, fullPath);

        if (entry.isDirectory()) {
          if (excludedDirs.has(entry.name)) continue;
          await readDir(fullPath, baseDir);
          continue;
        }

        const ext = path.extname(entry.name).toLowerCase();
        if (binaryExts.has(ext)) continue;

        try {
          const [content, stats] = await Promise.all([
            fsp.readFile(fullPath, "utf8"),
            fsp.stat(fullPath),
          ]);
          files.push({
            name: entry.name,
            path: relativePath.replace(/\\/g, "/"),
            content,
            size: stats.size,
          });
        } catch (readErr) {
          console.warn("[Dialog] Skipping file (read error):", fullPath, readErr.message);
        }
      }
    };

    await readDir(folderPath, folderPath);
    console.log("[Dialog] Read", files.length, "files from folder");

    return { canceled: false, files };
  } catch (err) {
    console.error("[Dialog] Folder dialog error:", err);
    return { canceled: true, files: [], error: err.message };
  }
});

ipcMain.handle("show-open-dialog-files", async (_, multiple = true) => {
  try {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: multiple ? "Select Files to Upload" : "Select File to Upload",
      properties: multiple ? ["openFile", "multiSelections"] : ["openFile"],
    });

    if (result.canceled || !result.filePaths.length) {
      console.log("[Dialog] File selection canceled");
      return { canceled: true, files: [] };
    }

    console.log("[Dialog] Selected", result.filePaths.length, "file(s)");

    const files = [];
    for (const filePath of result.filePaths) {
      try {
        const [content, stats] = await Promise.all([
          fsp.readFile(filePath, "utf8"),
          fsp.stat(filePath),
        ]);
        files.push({
          name: path.basename(filePath),
          path: path.basename(filePath),
          content,
          size: stats.size,
        });
      } catch (readErr) {
        console.warn("[Dialog] Skipping file (read error):", filePath, readErr.message);
      }
    }

    console.log("[Dialog] Read", files.length, "files");
    return { canceled: false, files };
  } catch (err) {
    console.error("[Dialog] File dialog error:", err);
    return { canceled: true, files: [], error: err.message };
  }
});

// ===========================================================================
// CLEAN EXIT
// ===========================================================================
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

// For the test harness (tests drive the pointer without the global shortcut).
module.exports = { startPointer, endPointer };
