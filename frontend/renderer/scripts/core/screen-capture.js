// Extracted from dashboard.js (improvement #7). Loaded as an ES module.
// Screen Capture panel: screenshot preview/describe/OCR, recording, smart analysis. Side-effect module: wires its buttons on import.
import { API_BASE } from "./config.js";
import { registerAttachment } from "./attachments.js";

// ============================================================================
// SCREENSHOT PREVIEW + DESCRIBE + OCR + ATTACHMENT
// ============================================================================

let lastScreenshotBuffer = null;

window.openScreenshotPreview = function (buffer) {
  lastScreenshotBuffer = buffer;

  const modal = document.getElementById("screenshot-preview");
  const img = document.getElementById("preview-img");
  if (!modal || !img) return;

  const blob = new Blob([buffer], { type: "image/png" });
  const url = URL.createObjectURL(blob);
  img.src = url;

  modal.classList.add("show");
};

window.takeScreenshot = async function () {
  const bridge = window.sarahVision || window.electronAPI;
  if (!bridge || !bridge.captureScreen) {
    console.error("[Screenshot] No capture bridge available.");
    return;
  }

  const buffer = await bridge.captureScreen();
  if (!buffer) return;

  const attId = registerAttachment("screenshot", buffer);
  window.openScreenshotPreview(buffer);

  const sendBtn = document.getElementById("preview-send");
  if (sendBtn && !sendBtn._bound) {
    sendBtn._bound = true;
    sendBtn.addEventListener("click", () => {
      window.SARAH_UI.appendMessage("user", "(Screenshot attached)", {
        attachmentId: attId,
        attachmentKind: "screenshot",
        attachmentLabel: "View screenshot",
      });
      document.getElementById("screenshot-preview")?.classList.remove("show");
    });
  }
};

document.getElementById("preview-close")?.addEventListener("click", () => {
  document.getElementById("screenshot-preview")?.classList.remove("show");
});

document
  .getElementById("preview-describe")
  ?.addEventListener("click", async () => {
    if (!lastScreenshotBuffer) return;

    console.log("[Screenshot] Describe button clicked");

    try {
      // Convert buffer to Blob
      const blob = new Blob([lastScreenshotBuffer], { type: "image/png" });

      console.log("[Screenshot] Sending to Ollama for description...");

      // Create FormData for Ollama vision endpoint
      const formData = new FormData();
      formData.append("file", blob, "screenshot.png");
      formData.append("mode", "general");
      formData.append("model", "qwen3-vl:8b");

      // Call Ollama vision API for image description
      const response = await fetch(`${API_BASE}/vision/analyze`, {
        method: 'POST',
        body: formData
      });

      if (!response.ok) {
        throw new Error(`API returned ${response.status}`);
      }

      const result = await response.json();

      if (result.analysis) {
        window.SARAH_UI.appendMessage(
          "assistant",
          `Image description:\n${result.analysis}`
        );
        console.log("[Screenshot] Description complete");
      } else {
        throw new Error(result.error || "Analysis failed");
      }
    } catch (err) {
      console.error("[Screenshot] Description failed:", err);
      window.SARAH_UI.appendMessage(
        "assistant",
        `Image description:\nFailed to analyze: ${err.message}`
      );
    }
  });

// Ollama Vision Analysis - Detailed analysis with mode selection
document.getElementById("preview-ollama")?.addEventListener("click", async () => {
  if (!lastScreenshotBuffer) return;

  const modeSelect = document.getElementById("ollama-mode-select");
  if (!modeSelect) {
    console.error("[Ollama Vision] Mode selector not found");
    return;
  }

  const mode = modeSelect.value;
  console.log(`[Ollama Vision] Analyzing with mode: ${mode}`);

  // Close screenshot preview modal
  document.getElementById("screenshot-preview")?.classList.remove("show");

  // Show loading message in chat
  const loadingMsg = window.SARAH_UI.appendMessage(
    "assistant",
    `🔬 Analyzing screenshot with Ollama qwen3-vl:8b...\nMode: ${mode.toUpperCase()}`
  );

  try {
    // Convert buffer to Blob
    const blob = new Blob([lastScreenshotBuffer], { type: "image/png" });

    // Create FormData for multipart upload
    const formData = new FormData();
    formData.append("file", blob, "screenshot.png");
    formData.append("mode", mode);
    formData.append("model", "qwen3-vl:8b");

    console.log("[Ollama Vision] Sending request to backend...");

    const response = await fetch(`${API_BASE}/vision/analyze`, {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Backend returned ${response.status}: ${errorText}`);
    }

    const result = await response.json();

    if (result.ok === false || !result.analysis) {
      throw new Error(result.error || "Ollama analysis failed");
    }

    console.log(`[Ollama Vision] Success in ${result.timing_ms}ms`);

    // Remove loading message
    if (loadingMsg && loadingMsg.remove) {
      loadingMsg.remove();
    }

    // Display result with mode indicator
    const modeEmoji = {
      ui: "🖥️",
      general: "📸",
      code: "💻",
      ocr: "📄"
    }[mode] || "🔬";

    window.SARAH_UI.appendMessage(
      "assistant",
      `${modeEmoji} **Ollama Vision: ${mode.toUpperCase()}** (${(result.timing_ms / 1000).toFixed(1)}s)\n\n${result.analysis}`
    );

  } catch (err) {
    console.error("[Ollama Vision] Analysis failed:", err);

    // Remove loading message
    if (loadingMsg && loadingMsg.remove) {
      loadingMsg.remove();
    }

    // Show error with troubleshooting
    window.SARAH_UI.appendMessage(
      "assistant",
      `❌ **Ollama Vision Analysis Failed**\n\n${err.message}\n\n**Troubleshooting:**\n1. Check Ollama is running: \`ollama serve\`\n2. Verify model downloaded: \`ollama list\`\n3. Test Ollama: \`curl http://localhost:11434\`\n4. Backend logs may have more details`
    );
  }
});

document.getElementById("preview-ocr")?.addEventListener("click", async () => {
  if (!lastScreenshotBuffer) return;
  const bridge = window.sarahVision || window.electronAPI;
  if (!bridge || !bridge.ocrImage) return;

  const text = await bridge.ocrImage(lastScreenshotBuffer);
  window.SARAH_UI.appendMessage(
    "assistant",
    `[OCR Extracted Text]\n${text || "(no text found)"}`
  );
});

// Also wire sv-shot button if present
document.getElementById("sv-shot")?.addEventListener("click", () => {
  window.takeScreenshot();
});

// ============================================================================
// TRUE SCREEN RECORDING + TIMELINE + MULTI-FRAME SUMMARY
// ============================================================================

let screenStream = null;
let mediaRecorder = null;
let recordedChunks = [];
let lastRecordingBlob = null;

const screenSnapshotBtn = document.getElementById("screen-snapshot");
const screenStartBtn = document.getElementById("screen-start");
const screenStopBtn = document.getElementById("screen-stop");
const screenAnalyzeBtn = document.getElementById("screen-analyze");
const screenPlayer = document.getElementById("screen-recording-player");
const screenTimeline = document.getElementById("screen-timeline");
const screenResultsEl = document.getElementById("screen-results");

async function startScreenRecording() {
  if (mediaRecorder) return;

  try {
    const stream =
      navigator.mediaDevices.getDisplayMedia &&
      (await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: 15 },
        audio: false,
      }));

    if (!stream) {
      console.error("[Screen] Could not obtain display media stream.");
      return;
    }

    screenStream = stream;
    recordedChunks = [];
    lastRecordingBlob = null;

    mediaRecorder = new MediaRecorder(stream, {
      mimeType: "video/webm; codecs=vp9",
    });

    mediaRecorder.ondataavailable = (ev) => {
      if (ev.data && ev.data.size > 0) {
        recordedChunks.push(ev.data);
      }
    };

    mediaRecorder.onstop = () => {
      const blob = new Blob(recordedChunks, { type: "video/webm" });
      lastRecordingBlob = blob;

      const url = URL.createObjectURL(blob);
      if (screenPlayer) {
        screenPlayer.src = url;
        screenPlayer.pause();
        screenPlayer.currentTime = 0;
      }

      if (screenResultsEl) {
        screenResultsEl.textContent = `Recording stopped. Captured ~${Math.round(
          (blob.size / (1024 * 1024)) * 10
        ) / 10} MB of video.`;
      }

      if (screenStream) {
        screenStream.getTracks().forEach((t) => t.stop());
      }
      screenStream = null;
      mediaRecorder = null;
    };

    mediaRecorder.start(300);

    if (screenResultsEl) {
      screenResultsEl.textContent =
        "Recording started… capturing screen as video (webm).";
    }
  } catch (err) {
    console.error("[Screen] startScreenRecording failed:", err);
    if (screenResultsEl) {
      screenResultsEl.textContent = "Failed to start recording screen.";
    }
  }
}

function stopScreenRecording() {
  if (!mediaRecorder) return;
  mediaRecorder.stop();

  if (screenResultsEl) {
    screenResultsEl.textContent = "Stopping recording and finalizing video…";
  }
}

screenSnapshotBtn?.addEventListener("click", () => {
  if (typeof window.takeScreenshot === "function") {
    window.takeScreenshot();
  } else {
    console.error("[Screen] takeScreenshot function not available");
  }
});

screenStartBtn?.addEventListener("click", startScreenRecording);
screenStopBtn?.addEventListener("click", stopScreenRecording);

// Timeline scrub
if (screenPlayer && screenTimeline) {
  screenPlayer.addEventListener("loadedmetadata", () => {
    screenTimeline.min = 0;
    screenTimeline.max = Math.floor(screenPlayer.duration * 1000);
    screenTimeline.value = 0;
  });

  screenPlayer.addEventListener("timeupdate", () => {
    if (!screenPlayer.duration) return;
    const t = screenPlayer.currentTime * 1000;
    screenTimeline.value = Math.floor(t);
  });

  screenTimeline.addEventListener("input", () => {
    if (!screenPlayer.duration) return;
    const tMs = Number(screenTimeline.value || 0);
    screenPlayer.currentTime = tMs / 1000;
  });
}

// Extract key frames from recording blob
async function extractKeyFramesFromBlob(blob, frameCount = 5) {
  if (!blob) return [];

  const video = document.createElement("video");
  video.src = URL.createObjectURL(blob);
  video.muted = true;
  video.playsInline = true;

  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");

  await new Promise((resolve, reject) => {
    video.onloadedmetadata = () => resolve();
    video.onerror = (e) => reject(e);
  });

  const duration = video.duration;
  canvas.width = video.videoWidth || 1280;
  canvas.height = video.videoHeight || 720;

  const frames = [];

  for (let i = 0; i < frameCount; i++) {
    const t = ((i + 1) / (frameCount + 1)) * duration;
    await new Promise((resolve) => {
      video.currentTime = t;
      video.onseeked = () => resolve();
    });

    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

    const blobPng = await new Promise((resolve) =>
      canvas.toBlob((b) => resolve(b), "image/png")
    );
    const buf = new Uint8Array(await blobPng.arrayBuffer());
    frames.push(buf);
  }

  return frames;
}

async function analyzeLastRecording() {
  if (!lastRecordingBlob) {
    if (screenResultsEl) {
      screenResultsEl.textContent =
        "No recording available. Start and stop a recording first.";
    }
    return;
  }

  const bridge = window.sarahVision;
  if (!bridge || !bridge.summarizeVideoFrames) {
    console.error("[Screen] sarahVision.summarizeVideoFrames not available.");
    return;
  }

  if (screenResultsEl) {
    screenResultsEl.textContent = "Extracting key frames from recording…";
  }

  const frames = await extractKeyFramesFromBlob(lastRecordingBlob, 5);
  if (!frames.length) {
    if (screenResultsEl) {
      screenResultsEl.textContent = "Could not extract frames from recording.";
    }
    return;
  }

  if (screenResultsEl) {
    screenResultsEl.textContent =
      "Analyzing recording via multi-frame summary…";
  }

  const summary = await bridge.summarizeVideoFrames(frames);

  if (screenResultsEl) {
    screenResultsEl.textContent = "Recording summary:\n\n" + summary;
  }

  if (
    window.SARAH_UI &&
    typeof window.SARAH_UI.sendMultimodalContext === "function"
  ) {
    await window.SARAH_UI.sendMultimodalContext(
      "screen recording",
      summary,
      null
    );
  }
}

screenAnalyzeBtn?.addEventListener("click", analyzeLastRecording);

// ============================================================================
// SMART SCREENSHOT ANALYSIS
// ============================================================================

const smartAnalyzeBtn = document.getElementById("screen-smart-analyze");
const analysisModeSelect = document.getElementById("analysis-mode");

async function performSmartAnalysis() {
  if (!lastRecordingBlob) {
    // No recording, try to take a screenshot
    if (screenResultsEl) {
      screenResultsEl.textContent = "Taking screenshot for analysis...";
    }

    try {
      // Capture current screen
      const canvas = await html2canvas(document.body);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.9));

      if (!blob) {
        if (screenResultsEl) {
          screenResultsEl.textContent = "Failed to capture screenshot.";
        }
        return;
      }

      await analyzeScreenshotWithMode(blob);
    } catch (err) {
      console.error("[SmartAnalysis] Screenshot capture failed:", err);
      if (screenResultsEl) {
        screenResultsEl.textContent = "Failed to capture screenshot: " + err.message;
      }
    }
    return;
  }

  // Analyze last recording's first frame
  const frames = await extractKeyFramesFromBlob(lastRecordingBlob, 1);
  if (!frames.length) {
    if (screenResultsEl) {
      screenResultsEl.textContent = "Could not extract frame for analysis.";
    }
    return;
  }

  // Convert base64 to blob
  const base64Data = frames[0].split(',')[1];
  const binaryData = atob(base64Data);
  const arrayBuffer = new Uint8Array(binaryData.length);
  for (let i = 0; i < binaryData.length; i++) {
    arrayBuffer[i] = binaryData.charCodeAt(i);
  }
  const blob = new Blob([arrayBuffer], { type: 'image/jpeg' });

  await analyzeScreenshotWithMode(blob);
}

async function analyzeScreenshotWithMode(imageBlob) {
  const mode = analysisModeSelect?.value || "general";

  console.log("[Screenshot] Starting analysis with mode:", mode);
  console.log("[Screenshot] Image blob size:", imageBlob.size);

  if (screenResultsEl) {
    const modeNames = {
      "general": "Auto-Detecting",
      "error_detection": "Error Detection",
      "ui_analysis": "UI Analysis",
      "code_recognition": "Code Extraction"
    };
    screenResultsEl.textContent = `Running ${modeNames[mode]} analysis...`;
  }

  try {
    // Convert blob to base64
    console.log("[Screenshot] Converting blob to base64...");
    const reader = new FileReader();
    const base64Promise = new Promise((resolve, reject) => {
      reader.onload = () => resolve(reader.result.split(',')[1]);
      reader.onerror = reject;
    });
    reader.readAsDataURL(imageBlob);
    const imageBase64 = await base64Promise;
    console.log("[Screenshot] Base64 conversion complete, length:", imageBase64.length);

    console.log("[Screenshot] Using Ollama for image analysis");

    // Map mode to Ollama format (general, ui, code, ocr)
    const ollamaMode = mode === "error_detection" ? "general" :
                       mode === "ui_analysis" ? "ui" :
                       mode === "code_recognition" ? "code" : "general";

    // Create FormData for Ollama vision endpoint
    const formData = new FormData();
    formData.append("file", imageBlob, "screenshot.jpg");
    formData.append("mode", ollamaMode);
    formData.append("model", "qwen3-vl:8b");

    console.log("[Screenshot] Sending request to Ollama vision API...");
    const response = await fetch(`${API_BASE}/vision/analyze`, {
      method: 'POST',
      body: formData
    });

    console.log("[Screenshot] Response status:", response.status);

    if (!response.ok) {
      const errorText = await response.text();
      console.error("[Screenshot] API error response:", errorText);
      throw new Error(`API returned ${response.status}: ${errorText}`);
    }

    const result = await response.json();
    console.log("[Screenshot] Result:", result);

    if (!result.analysis) {
      throw new Error(result.error || "Ollama analysis failed");
    }

    // Display formatted result
    if (screenResultsEl) {
      const modeEmojis = {
        "error_detection": "🔴",
        "ui_analysis": "🎨",
        "code_recognition": "💻",
        "general": "🔍"
      };

      const emoji = modeEmojis[mode] || "✨";
      const modeNames = {
        "general": "Auto-Detect",
        "error_detection": "Error Detection",
        "ui_analysis": "UI Analysis",
        "code_recognition": "Code Recognition"
      };
      screenResultsEl.textContent = `${emoji} ${modeNames[mode]} Analysis:\n\n${result.analysis}`;
    }

    // Also display in chat
    if (window.SARAH_UI) {
      window.SARAH_UI.appendMessage("assistant", result.analysis);
    }

  } catch (err) {
    console.error("[Screenshot] Analysis failed:", err);
    if (screenResultsEl) {
      screenResultsEl.textContent = `Analysis failed: ${err.message}\n\nMake sure:\n1. Ollama is running with qwen3-vl:8b model\n2. Backend is running on port 8907\n3. Vision endpoint is accessible at /vision/analyze`;
    }
  }
}

smartAnalyzeBtn?.addEventListener("click", performSmartAnalysis);
