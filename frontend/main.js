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
  Menu
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
// WINDOW CREATION
// ---------------------------------------------------------------------------
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
    useContentSize: true,

    webPreferences: {
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
    }
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
  mainWindow.on("closed", () => (mainWindow = null));
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
