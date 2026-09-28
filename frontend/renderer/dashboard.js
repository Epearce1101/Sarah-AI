// Issue #5: diagnostics-dashboard + its two deps (~39KB) used to load eagerly
// here. They now lazy-load on first click of the Diagnostics sidebar button via
// `_ensureDiagnosticsDashboard()` below.

﻿////////////////////////////////////////////////////////////////////////////////
// SARAH V10 — DASHBOARD (FULL MULTIMODAL UPGRADE)
// - Chat UI
// - LLM mode switch (online/local)
// - Voice toggle + TTS playback
// - Live2D hooks
// - Health check
// - SQL conversations + memory browser
// - Delete conversation support
// - Screenshot + Preview + Describe + OCR
// - True screen recording + timeline scrub + multi-frame summarization
////////////////////////////////////////////////////////////////////////////////


// Self-contained pieces live in scripts/core/ (improvement #7).
import { API_BASE, parseDbTimestamp, sarahPerfEnd, sarahPerfStart } from "./scripts/core/config.js";
import { SarahBackend } from "./scripts/core/backend-client.js";
import { SarahTTS } from "./scripts/core/tts.js";
import { SarahSlashPalette } from "./scripts/core/slash-palette.js";
import { SarahModelPicker } from "./scripts/core/model-picker.js";
import { openAttachment } from "./scripts/core/attachments.js";
import { ProjectModal } from "./scripts/core/project-modal.js";
import "./scripts/core/screen-capture.js";
import { parseCues } from "./scripts/avatar3d/cues.js";
import { LiveVoice, isEcho } from "./scripts/core/live-voice.js";
import { SarahEyes } from "./scripts/core/eyes.js";
import { SarahSenses } from "./scripts/core/senses.js";

// -----------------------------------------------------------------------------
// UI Controller
// -----------------------------------------------------------------------------

class SarahUI {
  constructor(backend, tts) {
    this.backend = backend;
    this.tts = tts;

    this.chatLog =
      document.getElementById("chat-log") ||
      document.getElementById("chat-history");
    this.chatInput = document.getElementById("chat-input");
    this.chatSend = document.getElementById("chat-send");
    this.attachmentPreview = document.getElementById("chat-attachment-preview");
    this.pendingAttachments = [];

    // Snippet attachment state
    this.attachedSnippet = null;

    // Image attachment state
    this.attachedImage = null;  // Stores {name, dataUrl}

    this.newChatBtn =
      document.getElementById("new-chat") ||
      document.getElementById("btn-new-chat");

    this.voiceToggleBtn = document.getElementById("voice-toggle");
    this.llmModeBtn = document.getElementById("llm-mode-btn");

    this.moreToggleBtn = document.getElementById("more-toggle");
    this.morePanel = document.getElementById("more-panel");
    this.moreCloseBtn = document.getElementById("more-close");
    this.moreTabs = Array.from(document.querySelectorAll(".more-tab"));

    this.moreConvListEl = document.getElementById("more-conversation-list");
    this.moreConvEmptyEl = document.getElementById("more-conversation-empty");
    this.moreNewConvBtn = document.getElementById("more-new-conversation");

    this.moreViewConversations = document.getElementById(
      "more-view-conversations"
    );
    this.moreViewMemories = document.getElementById("more-view-memories");
    this.moreViewProjects = document.getElementById("more-view-projects");

    this.moreMemSearchForm = document.getElementById("more-memory-search");
    this.moreMemQueryInput = document.getElementById("more-memory-query");
    this.moreMemRecentEl = document.getElementById("more-memory-recent");
    this.moreMemPinnedEl = document.getElementById("more-memory-pinned");
    this.moreChatSearchResults = document.getElementById("more-chat-search-results");
    this.moreChatSearchList = document.getElementById("more-chat-search-list");
    this.morePinnedMessages = document.getElementById("more-pinned-messages");

    this.moreProjectListEl = document.getElementById("more-project-list");
    this.moreProjectEmptyEl = document.getElementById("more-project-empty");
    this.moreNewProjectBtn = document.getElementById("more-new-project");

    this.moreViewSkills = document.getElementById("more-view-skills");
    this.moreSkillsListEl = document.getElementById("more-skills-list");
    this.moreSkillsEmptyEl = document.getElementById("more-skills-empty");
    this.moreSkillDetailEl = document.getElementById("more-skill-detail");
    this.moreDiscoverSkillsBtn = document.getElementById("more-discover-skills");
    this.moreRefreshSkillsBtn = document.getElementById("more-refresh-skills");

    this.btnLibrary = document.getElementById("btn-library");
    this.btnProjects = document.getElementById("btn-projects");
    this.btnScreen = document.getElementById("btn-screen");
    this.btnDiagnostics = document.getElementById("btn-diagnostics");

    this.screenTab = document.getElementById("screen-tab");
    this.diagnosticsDashboard = document.getElementById("diagnostics-dashboard");
    this.diagnosticsCloseBtn = document.getElementById("diagnostics-close");

    this.diagnosticsEls = {
      wakePulse: document.getElementById("diag-wake-pulse"),
      wakeState: document.getElementById("diag-wake-state"),
      wakeTranscript: document.getElementById("diag-wake-transcript"),
      wakeConfidence: document.getElementById("diag-wake-confidence"),
      wakeReason: document.getElementById("diag-wake-reason"),
      wakeCooldown: document.getElementById("diag-wake-cooldown"),
      wakePrefix: document.getElementById("diag-wake-prefix"),
      modelState: document.getElementById("diag-model-state"),
      modelMode: document.getElementById("diag-model-mode"),
      modelContext: document.getElementById("diag-model-context"),
    };

    this.statusBarEl = document.getElementById("status-bar");
    this.modeLabelEl = document.getElementById("mode-label");
    this.modelStatusTextEl = document.getElementById("model-status-text");
    this.modelContextTextEl = document.getElementById("model-context-text");

    this.activeConversationId = null;
    this.llmMode = "online";
    this.llmStatus = {
      mode: "online",
      provider: "OpenRouter",
      model_name: "nvidia/nemotron-3-ultra-550b-a55b:free",
      model_label: "OpenRouter - Nemotron 3 Ultra 550B A55B (free)",
      token_budget: 1000000,
      context_window_tokens: 1000000,
      tokens_used: 0,
      auto_routed: false,
    };

    // Issue #5: defer construction until the user actually opens the panel.
    this.diagnostics = null;
    this._diagnosticsLoading = null;
    this.diagnosticsCloseBtn = document.getElementById("diagnostics-close");

    // Initialize ProjectModal
    this.projectModal = new ProjectModal(this.backend, this);

    // Wake-word + voice capture state
    this.voiceListening = false;
    this.voiceRecorder = null;
    this.voiceStream = null;
    this.voiceChunks = [];
    this.voiceAutoStopTimer = null;
    this.wakePollTimer = null;
    this.diagnosticsPollTimer = null;
    this.contextStatusTimer = null;
    this._contextSyncInFlight = false;
    this.voiceProcessing = false;
    this._wakeRequestInFlight = false;
    this._diagnosticsRequestInFlight = false;
    this._cachedMicStream = null;  // Cached microphone stream for faster response
    this._wakeWatcherBound = false;
    this._wakePollMs = 250;
    this._conversationById = new Map();
    this._projectById = new Map();
    this._skillBySlug = new Map();
    this._delegatedListEventsBound = false;
    this._delegatedChatEventsBound = false;

    this._bindDelegatedListEvents();
    this._bindDelegatedChatEvents();
    this._bindEvents();
    this._initState();
    this._startWakeWatcher();
    this._startContextStatusSync();
    this._initLiveVoice();
    this._startUsageMeter();
  }

  // Free daily allowance: no meter on screen (it's in Diagnostics), just a
  // warning the first time today's use passes 50 %, 75 %, 90 % and 95 %.
  _startUsageMeter() {
    const LEVELS = [95, 90, 75, 50];
    const check = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/usage`);
        if (!res.ok) return;
        const u = await res.json();
        const pct = (u.share || 0) * 100;
        const level = LEVELS.find((l) => pct >= l);
        if (!level) return;
        let warned = {};
        try { warned = JSON.parse(localStorage.getItem("sarah.usageWarned") || "{}"); } catch {}
        if (warned.day === u.day_utc && warned.level >= level) return;
        localStorage.setItem("sarah.usageWarned", JSON.stringify({ day: u.day_utc, level }));
        this._showWarning(`Sarah has used ${level}% of today's free requests (${u.used} of ${u.limit}). It resets at midnight UTC.`, level >= 90);
      } catch {
        /* backend restarting */
      }
    };
    check();
    this._usageTimer = setInterval(check, 60000);
  }

  _showWarning(message, urgent = false) {
    const el = document.createElement("div");
    el.className = "sarah-warning" + (urgent ? " urgent" : "");
    el.textContent = message;
    el.title = "Click to dismiss";
    el.addEventListener("click", () => el.remove());
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 15000);
  }

  //---------------------------------------------------------------------------
  // Live voice: always listening, no wake word. The backend endpoints each
  // turn and transcribes it on the GPU; talking over Sarah interrupts her.
  //---------------------------------------------------------------------------

  _initLiveVoice() {
    this.micToggleBtn = document.getElementById("mic-toggle");
    this.voiceCaption = document.getElementById("voice-caption");
    this._voiceQueue = [];
    // Mic ON/OFF is yours: saved, and nothing turns it back on but you.
    let enabled = true;
    try {
      const saved = localStorage.getItem("sarah.mic") ?? localStorage.getItem("sarah.liveVoice");
      enabled = saved !== "off";
    } catch {}
    this._micOn = enabled;
    this.liveVoice = new LiveVoice({
      onEvent: (msg) => this._onLiveVoiceEvent(msg),
      onStatus: (status, detail) => this._onLiveVoiceStatus(status, detail),
    });
    this.micToggleBtn?.addEventListener("click", () => this._toggleLiveVoice());
    // Keep the listener told whether she's talking (echo guard, barge-in).
    this._speakingSync = setInterval(() => this.liveVoice?.setSpeaking(Boolean(this.tts?._ttsPlaying)), 100);
    if (enabled) this.liveVoice.start();
    else this._onLiveVoiceStatus("off");
    // Pre-synthesize her acknowledgement sounds once the backend is up.
    setTimeout(() => this.tts?.prepareFillers?.().catch(() => {}), 4000);
    this._initEyes();
  }

  // Her eyes: camera + screen, watched locally and looked at (free cloud
  // vision) only when the view changes. Top-bar button cycles
  // camera+screen -> screen only -> off; the camera shows a red dot.
  // Camera and screen are separate ON/OFF switches. Your choice is saved and
  // only you change it: a restart, her tools or her initiative never turn a
  // switched-off camera (or screen) back on.
  _initEyes() {
    const cameraBtn = document.getElementById("camera-toggle");
    const screenBtn = document.getElementById("screen-toggle");
    const pref = (key, fallback) => { try { return localStorage.getItem(key) || fallback; } catch { return fallback; } };
    // Carry over the old single "Eyes" setting the first time.
    const legacy = pref("sarah.eyes", "on");
    this._eyePrefs = {
      camera: pref("sarah.camera", legacy === "on" ? "on" : "off") === "on",
      screen: pref("sarah.screen", legacy === "off" ? "off" : "on") === "on",
    };
    const render = (open = []) => {
      if (cameraBtn) {
        const on = this._eyePrefs.camera;
        cameraBtn.textContent = on ? (open.includes("camera") ? "Camera: ON" : "Camera: …") : "Camera: OFF";
        cameraBtn.classList.toggle("camera-on", open.includes("camera"));
        if (on && !open.includes("camera")) cameraBtn.title = "Camera unavailable (in use or not connected)";
      }
      if (screenBtn) {
        screenBtn.textContent = this._eyePrefs.screen ? "Screen: ON" : "Screen: OFF";
        screenBtn.classList.toggle("listening", open.includes("screen"));
      }
    };
    this.eyes = new SarahEyes({ onStatus: (_mode, open) => render(open) });
    const toggle = (kind) => {
      this._eyePrefs[kind] = !this._eyePrefs[kind];
      try { localStorage.setItem(`sarah.${kind}`, this._eyePrefs[kind] ? "on" : "off"); } catch {}
      this.eyes.setSources(this._eyePrefs);
    };
    cameraBtn?.addEventListener("click", () => toggle("camera"));
    screenBtn?.addEventListener("click", () => toggle("screen"));
    render();
    this.eyes.setSources(this._eyePrefs);
    // Her mind's line to her body (fresh looks, things she decides to say).
    this.senses = new SarahSenses({ eyes: this.eyes, ui: this }).start();
    this._initInitiativeToggle();
  }

  // Initiative: ON (she speaks up / acts on her own) or QUIET (she still
  // watches and remembers, but waits to be spoken to).
  _initInitiativeToggle() {
    const btn = document.getElementById("initiative-toggle");
    if (!btn) return;
    const render = (quiet) => {
      btn.textContent = quiet ? "Initiative: QUIET" : "Initiative: ON";
      btn.classList.toggle("listening", !quiet);
    };
    const set = async (quiet) => {
      try {
        await fetch(`${API_BASE}/api/agency/initiative?quiet=${quiet}`, { method: "POST" });
        localStorage.setItem("sarah.initiative", quiet ? "quiet" : "on");
      } catch {}
      render(quiet);
    };
    let quiet = false;
    try { quiet = localStorage.getItem("sarah.initiative") === "quiet"; } catch {}
    btn.addEventListener("click", () => { quiet = !quiet; set(quiet); });
    setTimeout(() => set(quiet), 3000);
  }

  // A tool she's using on her own (not in reply to you), shown briefly.
  _showOwnActivity(evt) {
    const names = { web_search: "researching", read_webpage: "reading", run_python: "working something out",
      run_shell: "working on your PC", look: "taking a closer look", create_tool: "building herself a tool",
      open_item: "opening something", control_input: "using the keyboard/mouse", research: "researching",
      browser: "browsing the web", http_request: "checking a web service", add_skill: "learning a skill",
      use_skill: "using one of her skills" };
    if (evt.status === "start") {
      this._setVoiceCaption(`Sarah is ${names[evt.name] || `using ${evt.name}`}…`, "acting");
      window.SARAH_AVATAR_DIRECTOR?.onWorking?.(evt.name);
    } else {
      setTimeout(() => this._setVoiceCaption(""), 1200);
    }
  }

  // Mic ON <-> OFF. OFF stops the microphone entirely (the device is
  // released, Windows' mic indicator goes out) until you switch it back on.
  async _toggleLiveVoice() {
    const lv = this.liveVoice;
    if (!lv) return;
    this._micOn = !this._micOn;
    try { localStorage.setItem("sarah.mic", this._micOn ? "on" : "off"); } catch {}
    if (this._micOn) {
      await lv.start();
    } else {
      lv.stop();
      this._releaseCachedMic();
    }
  }

  _releaseCachedMic() {
    this._cachedMicStream?.getTracks().forEach((t) => t.stop());
    this._cachedMicStream = null;
  }

  _onLiveVoiceStatus(status, detail = "") {
    this._liveVoiceStatus = status;
    const labels = {
      listening: "Mic: ON", muted: "Mic: OFF", connecting: "Mic: …", off: "Mic: OFF",
      "no-mic": "Mic: NO DEVICE", error: "Mic: ERROR",
    };
    if (this.micToggleBtn) {
      this.micToggleBtn.textContent = labels[status] || `Mic: ${status}`;
      this.micToggleBtn.title = detail || "Her microphone. Stays off until you turn it back on.";
      this.micToggleBtn.classList.toggle("listening", status === "listening");
    }
    if (status === "error" || status === "no-mic") console.warn("[LiveVoice]", status, detail);
  }

  _setVoiceCaption(text, state = "") {
    const el = this.voiceCaption;
    if (!el) return;
    el.textContent = text || "";
    el.dataset.state = state;
    el.classList.toggle("visible", Boolean(text));
  }

  _onLiveVoiceEvent(msg) {
    const director = window.SARAH_AVATAR_DIRECTOR;
    if (msg.type === "speech_start") {
      this._voiceTurnStarted = performance.now();
      // Talking over her: she stops and listens, like a person would.
      if (msg.barge_in || this.tts?._ttsPlaying) this.tts.stop();
      director?.onUserSpeaking?.();
      this._setVoiceCaption("…", "hearing");
    } else if (msg.type === "partial") {
      this._setVoiceCaption(msg.text, "hearing");
    } else if (msg.type === "speech_cancel") {
      this._setVoiceCaption("");
      director?.onUserStoppedSpeaking?.();
    } else if (msg.type === "final") {
      const text = String(msg.text || "").trim();
      if (!text || isEcho(text, this.tts?.spokenRecently?.(6000)) && this.tts?.isMuting?.()) {
        this._setVoiceCaption("");
        director?.onUserStoppedSpeaking?.();
        return;
      }
      this._setVoiceCaption(text, "heard");
      setTimeout(() => this._setVoiceCaption(""), 1500);
      if (window.B6_TIMING || window.SARAH_LATENCY) {
        console.log(`[LATENCY] speech->final ${Math.round(performance.now() - (this._voiceTurnStarted || 0))} ms (stt ${msg.stt_ms} ms)`);
      }
      this._submitVoiceTurn(text);
    }
  }

  // A spoken turn goes in like a typed one. If she's still answering the
  // previous one, it waits and is sent right after (joined if several).
  _submitVoiceTurn(text) {
    if (this._chatBusy) {
      this._voiceQueue.push(text);
      return;
    }
    this._voiceTurnSentAt = performance.now();
    this._nextModality = "voice";
    this._sendChat(text);
  }

  _flushVoiceQueue() {
    if (!this._voiceQueue?.length || this._chatBusy) return;
    const text = this._voiceQueue.splice(0).join(" ");
    this._submitVoiceTurn(text);
  }

  //---------------------------------------------------------------------------
  // Helpers
  //---------------------------------------------------------------------------

  _bindDelegatedListEvents() {
    if (this._delegatedListEventsBound) return;

    this.moreConvListEl?.addEventListener("click", (ev) =>
      this._handleConversationListClick(ev)
    );

    [
      this.moreMemRecentEl,
      this.moreMemPinnedEl,
      this.morePinnedMessages,
      this.moreChatSearchList,
    ].forEach((el) => {
      el?.addEventListener("click", (ev) => this._handleMemoryListClick(ev));
    });

    this.moreProjectListEl?.addEventListener("click", (ev) =>
      this._handleProjectListClick(ev)
    );

    this.moreSkillsListEl?.addEventListener("click", (ev) =>
      this._handleSkillListClick(ev)
    );

    this._delegatedListEventsBound = true;
  }

  _bindDelegatedChatEvents() {
    if (this._delegatedChatEventsBound) return;

    this.chatLog?.addEventListener("click", (ev) => {
      this._handleChatLogClick(ev).catch((err) =>
        console.warn("[ChatAction] Action failed:", err)
      );
    });

    this._delegatedChatEventsBound = true;
  }

  _setChatActionFeedback(button, label = "Done", resetLabel = null) {
    if (!button) return;
    const original = resetLabel || button.textContent;
    button.textContent = label;
    setTimeout(() => {
      button.textContent = original;
    }, 1500);
  }

  async _handleChatLogClick(ev) {
    const image = ev.target.closest(".chat-bubble-image");
    if (image && this.chatLog?.contains(image)) {
      window.open(image.src, "_blank");
      return;
    }

    const chip = ev.target.closest(".chat-attachment-chip");
    if (chip && this.chatLog?.contains(chip)) {
      const id = chip.dataset.attachmentId;
      if (id) openAttachment(id);
      return;
    }

    const button = ev.target.closest("[data-chat-action]");
    if (!button || !this.chatLog?.contains(button)) return;
    ev.stopPropagation();

    const bubble = button.closest(".chat-bubble");
    if (!bubble) return;

    const action = button.dataset.chatAction;
    const messageId = Number(bubble.dataset.messageId);
    const hasMessageId = Number.isFinite(messageId) && messageId > 0;
    const text = bubble.querySelector(".chat-bubble-text")?.textContent || "";

    if (action === "copy") {
      if (window.sarahVision?.writeClipboard) {
        await window.sarahVision.writeClipboard(text);
      } else {
        await navigator.clipboard.writeText(text);
      }
      this._setChatActionFeedback(button, "Done", "Copy");
      return;
    }

    if (!hasMessageId) return;

    if (action === "pin") {
      await this.backend.pinMessage(messageId, true);
      this._setChatActionFeedback(button, "Done", "Pin");
    } else if (action === "edit") {
      this._editMessage(bubble, messageId, text);
    } else if (action === "regenerate") {
      await this._regenerateResponse(messageId);
    } else if (action === "delete") {
      if (!confirm("Delete this message?")) return;
      await this.backend.deleteMessage(messageId);
      bubble.remove();
    }
  }

  async _handleConversationListClick(ev) {
    const actionEl = ev.target.closest("[data-conv-action]");
    const row = ev.target.closest(".more-conv-row");
    if (!row || !this.moreConvListEl?.contains(row)) return;

    const id = Number(row.dataset.conversationId);
    if (!Number.isFinite(id)) return;

    if (actionEl?.dataset.convAction === "rename") {
      ev.stopPropagation();
      this._startConversationRename(row, id);
      return;
    }

    if (actionEl?.dataset.convAction === "delete") {
      ev.stopPropagation();
      this._showDeleteConversationConfirm(row, id);
      return;
    }

    if (ev.target.closest(".more-conversation-item")) {
      await this.setActiveConversation(id);
      this.closeMorePanel();
    }
  }

  _startConversationRename(row, id) {
    if (row.querySelector(".conv-rename-input")) return;

    const btn = row.querySelector(".more-conversation-item");
    const rename = row.querySelector("[data-conv-action='rename']");
    const currentTitle = row.dataset.title || `Conversation #${id}`;

    const input = document.createElement("input");
    input.type = "text";
    input.className = "conv-rename-input";
    input.value = currentTitle;

    if (btn) btn.style.display = "none";
    row.insertBefore(input, rename || row.firstChild);
    input.focus();
    input.select();

    let finished = false;
    const saveRename = async () => {
      if (finished) return;
      finished = true;
      const newTitle = input.value.trim();
      if (newTitle && newTitle !== currentTitle) {
        try {
          await this.backend.renameConversation(id, newTitle);
          row.dataset.title = newTitle;
          const existing = this._conversationById.get(id);
          if (existing) existing.title = newTitle;
        } catch (err) {
          console.error("Rename failed:", err);
        }
      }
      input.remove();
      if (btn) {
        btn.style.display = "";
        btn.textContent = row.dataset.title || currentTitle;
      }
    };

    input.addEventListener("blur", saveRename);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        input.blur();
      } else if (e.key === "Escape") {
        input.value = currentTitle;
        input.blur();
      }
    });
  }

  _showDeleteConversationConfirm(row, id) {
    const title = row.dataset.title || `Conversation #${id}`;
    const confirmBox = document.createElement("div");
    confirmBox.className = "confirm-popup-backdrop";

    const win = document.createElement("div");
    win.className = "confirm-popup-window";

    const header = document.createElement("div");
    header.className = "confirm-popup-title";
    header.textContent = "Delete Conversation?";

    const text = document.createElement("div");
    text.className = "confirm-popup-text";
    text.textContent = `Are you sure you want to delete "${title}"? This cannot be undone.`;

    const actions = document.createElement("div");
    actions.className = "confirm-popup-actions";

    const cancel = document.createElement("button");
    cancel.className = "confirm-popup-cancel";
    cancel.textContent = "Cancel";

    const del = document.createElement("button");
    del.className = "confirm-popup-delete";
    del.textContent = "Delete";

    actions.appendChild(cancel);
    actions.appendChild(del);
    win.appendChild(header);
    win.appendChild(text);
    win.appendChild(actions);
    confirmBox.appendChild(win);
    document.body.appendChild(confirmBox);

    cancel.addEventListener("click", () => confirmBox.remove());
    del.addEventListener("click", async () => {
      try {
        confirmBox.remove();
        row.classList.add("conv-delete-anim");
        setTimeout(() => row.remove(), 250);

        if (this.activeConversationId === id) {
          this.activeConversationId = null;
          localStorage.removeItem("sarah_last_conversation_id");
          this.clearChat();
          this.appendMessage("assistant", "Chat deleted. Start a new one anytime.");
        }

        await this.backend.deleteConversation(id);
        await this.refreshConversations();
      } catch (err) {
        console.warn("Delete failed", err);
      }
    });
  }

  async _handleMemoryListClick(ev) {
    const actionEl = ev.target.closest("[data-memory-action]");
    if (!actionEl) return;

    const action = actionEl.dataset.memoryAction;
    try {
      if (action === "pin-memory") {
        await this.backend.setMemoryPinned(
          Number(actionEl.dataset.id),
          actionEl.dataset.pinned !== "true"
        );
        await this.refreshMemories();
      } else if (action === "delete-memory") {
        await this.backend.deleteMemory(Number(actionEl.dataset.id));
        await this.refreshMemories();
      } else if (action === "goto-message") {
        await this.setActiveConversation(Number(actionEl.dataset.conversationId));
        this.closeMorePanel();
      } else if (action === "pin-message") {
        await this.backend.pinMessage(
          Number(actionEl.dataset.id),
          actionEl.dataset.pinned !== "true"
        );
        await this.refreshMemories();
      }
    } catch (err) {
      console.warn("Memory action failed", err);
    }
  }

  _handleProjectListClick(ev) {
    const btn = ev.target.closest("[data-project-action='open']");
    const row = ev.target.closest(".more-conv-row");
    if (!btn || !row || !this.moreProjectListEl?.contains(row)) return;

    const id = Number(row.dataset.projectId);
    if (!Number.isFinite(id)) return;
    this.closeMorePanel();
    this.projectModal.open("manage", id);
  }

  async _handleSkillListClick(ev) {
    const actionEl = ev.target.closest("[data-skill-action]");
    if (!actionEl || !this.moreSkillsListEl?.contains(actionEl)) return;

    const slug = actionEl.dataset.slug;
    const skill = this._skillBySlug.get(slug);
    if (!slug || !skill) return;

    const action = actionEl.dataset.skillAction;
    actionEl.disabled = true;

    try {
      if (action === "enable" || action === "disable") {
        const enabled = action === "enable";
        await this.backend.setSkillEnabled(slug, enabled);
        this._renderSkillDetail(
          enabled ? "Skill enabled" : "Skill disabled",
          `${skill.name || slug} is ${enabled ? "enabled" : "disabled"} for future chat context injection.`
        );
        await this.refreshSkills();
      } else if (action === "manifest") {
        if (skill.stale) {
          this._renderSkillDetail(
            "Skill manifest unavailable",
            `${skill.name || slug} is stale. Run Discover Skills after restoring its SKILL.md file.`
          );
          return;
        }

        const data = await this.backend.getSkillManifest(slug);
        const manifest = data.skill || {};
        const parts = [
          manifest.description || "No description.",
          "",
          `Slug: ${manifest.slug || slug}`,
          `Path: ${manifest.path || "unknown"}`,
          `Body length: ${manifest.body_len || (manifest.body || "").length} chars`,
          "",
          manifest.body || "(empty body)",
        ];
        this._renderSkillDetail(manifest.name || skill.name || slug, parts.join("\n"));
      }
    } catch (err) {
      console.warn("[Skills] action failed:", err);
      this._renderSkillDetail("Skill action failed", err?.message || String(err));
    } finally {
      actionEl.disabled = false;
    }
  }

  _formatAttachmentSize(bytes = 0) {
    if (!bytes) return "";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  _isTextFile(file) {
    if (!file) return false;
    if ((file.type || "").startsWith("text/")) return true;
    if (["application/json", "application/xml", "application/javascript"].includes(file.type)) {
      return true;
    }
    return /\.(txt|md|json|xml|csv|log|js|jsx|ts|tsx|py|java|c|cpp|h|css|html|sql|yaml|yml)$/i.test(file.name || "");
  }

  _refreshImageUploadButton() {
    const imageUploadBtn = document.getElementById("image-upload-btn");
    if (!imageUploadBtn) return;

    const hasImage = this.pendingAttachments.some((att) => att.kind === "image") || !!this.attachedImage;
    imageUploadBtn.textContent = hasImage ? "IMG" : "+";
    imageUploadBtn.classList.toggle("has-attachment", hasImage);
  }

  _addPendingAttachment(attachment) {
    if (!attachment) return null;
    if (this.pendingAttachments.length >= 8) {
      this.setStatus("Attachment limit reached");
      return null;
    }

    const normalized = {
      id: attachment.id || `att-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      kind: attachment.kind,
      name: attachment.name || attachment.fileName || "attachment",
      type: attachment.type || "",
      size: attachment.size || 0,
      dataUrl: attachment.dataUrl || null,
      text: attachment.text || null,
    };

    this.pendingAttachments.push(normalized);
    this._renderPendingAttachments();
    return normalized;
  }

  _removePendingAttachment(id) {
    this.pendingAttachments = this.pendingAttachments.filter((att) => att.id !== id);
    this._renderPendingAttachments();
  }

  _clearPendingAttachments() {
    this.pendingAttachments = [];
    this._renderPendingAttachments();
  }

  _renderPendingAttachments() {
    if (!this.attachmentPreview) return;

    if (!this.pendingAttachments.length) {
      this.attachmentPreview.replaceChildren();
      this.attachmentPreview.classList.add("hidden");
      this._refreshImageUploadButton();
      return;
    }

    const fragment = document.createDocumentFragment();
    for (const att of this.pendingAttachments) {
      const card = document.createElement("div");
      card.className = "chat-attachment-card";

      if (att.kind === "image" && att.dataUrl) {
        const img = document.createElement("img");
        img.className = "chat-attachment-thumb";
        img.src = att.dataUrl;
        img.alt = att.name;
        card.appendChild(img);
      } else {
        const icon = document.createElement("div");
        icon.className = "chat-attachment-icon";
        icon.textContent = att.kind === "text" ? "TXT" : "FILE";
        card.appendChild(icon);
      }

      const meta = document.createElement("div");
      meta.className = "chat-attachment-meta";

      const name = document.createElement("div");
      name.className = "chat-attachment-name";
      name.textContent = att.name;
      meta.appendChild(name);

      const size = document.createElement("div");
      size.className = "chat-attachment-size";
      size.textContent = this._formatAttachmentSize(att.size) || att.type || att.kind;
      meta.appendChild(size);

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "chat-attachment-remove";
      remove.title = "Remove attachment";
      remove.textContent = "x";
      remove.addEventListener("click", () => this._removePendingAttachment(att.id));

      card.appendChild(meta);
      card.appendChild(remove);
      fragment.appendChild(card);
    }

    this.attachmentPreview.replaceChildren(fragment);
    this.attachmentPreview.classList.remove("hidden");
    this._refreshImageUploadButton();
  }

  _fileToDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  }

  async _queueTextFileAttachment(file) {
    const maxTextBytes = 1024 * 1024;
    if (file.size > maxTextBytes) {
      this.setStatus(`Text file too large: ${file.name}`);
      return;
    }

    const text = await file.text();
    this._addPendingAttachment({
      kind: "text",
      name: file.name || "pasted-text.txt",
      type: file.type || "text/plain",
      size: file.size || text.length,
      text,
    });
  }

  async _handleChatPaste(e) {
    const target = e.target;
    if (!target || target.id !== "chat-input") return;

    const cd = e.clipboardData;
    const items = Array.from(cd?.items || []);
    const files = items
      .filter((item) => item.kind === "file")
      .map((item) => item.getAsFile())
      .filter(Boolean);

    const imageFiles = files.filter((file) => (file.type || "").startsWith("image/"));
    const textFiles = files.filter((file) => !(file.type || "").startsWith("image/") && this._isTextFile(file));

    if (imageFiles.length || textFiles.length) {
      e.preventDefault();
      e.stopPropagation();

      for (const file of imageFiles) {
        const dataUrl = await this._fileToDataUrl(file);
        this._addPendingAttachment({
          kind: "image",
          name: file.name || `pasted-${Date.now()}.png`,
          type: file.type || "image/png",
          size: file.size,
          dataUrl,
        });
      }

      for (const file of textFiles) {
        await this._queueTextFileAttachment(file);
      }
      return;
    }

    const eventText = cd?.getData("text/plain") || cd?.getData("text") || "";
    if (eventText) {
      return; // Let Chromium perform native text paste.
    }

    if (window.sarahVision?.readClipboardImage) {
      e.preventDefault();
      e.stopPropagation();
      try {
        const ipcImage = await window.sarahVision.readClipboardImage();
        if (ipcImage?.dataUrl) {
          this._addPendingAttachment({
            kind: "image",
            name: `pasted-${Date.now()}.png`,
            type: "image/png",
            size: ipcImage.size || 0,
            dataUrl: ipcImage.dataUrl,
          });
        }
      } catch (err) {
        console.warn("[Chat Paste] IPC image paste failed:", err);
      }
    }
  }

  _normalizeMessageRole(role) {
    return role === "user" ? "user" : "assistant";
  }

  _createChatActionButton(action, label, title, extraClass = "") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `chat-action-btn${extraClass ? ` ${extraClass}` : ""}`;
    button.title = title;
    button.setAttribute("aria-label", title);
    button.textContent = label;
    button.dataset.chatAction = action;
    return button;
  }

  _createChatActions(role, messageId) {
    const actions = document.createElement("div");
    actions.className = "chat-bubble-actions";

    actions.appendChild(this._createChatActionButton("copy", "Copy", "Copy"));

    if (messageId) {
      actions.appendChild(this._createChatActionButton("pin", "Pin", "Pin"));
    }

    if (role === "user" && messageId) {
      actions.appendChild(this._createChatActionButton("edit", "Edit", "Edit"));
    }

    if (role === "assistant" && messageId) {
      actions.appendChild(this._createChatActionButton("regenerate", "Regen", "Regenerate"));
    }

    if (messageId) {
      actions.appendChild(
        this._createChatActionButton("delete", "Del", "Delete", "chat-action-btn-danger")
      );
    }

    return actions;
  }

  // Legacy bare gesture tags (<wave/>, <nod>) still honoured in replies.
  // Deliberately a fixed list: bare tags are stripped from the visible
  // text, so a broad list would eat real content like HTML in code answers.
  _knownMotionNames() {
    return (this._legacyMotionNames ||= new Set([
      "wave", "arms_up", "thinking_pose", "spin", "shrug", "point", "hand_to_chest",
      "lean_in", "step_back", "nod", "shake_head", "jump", "crouch", "blush",
      "laugh", "surprise", "distant", "wink", "clap", "bow",
    ]));
  }

  // Split a reply into readable text and its stage directions (<face>,
  // <look>, <point>, <gesture>, legacy <motion>/<wave/> tags). Cue `at`
  // offsets index into the returned text. `streaming` hides a tag that is
  // still being typed. Nothing is performed here: the TTS player (or the
  // director, when voice is off) performs cues in time with the reply.
  _parseReply(raw, { streaming = false } = {}) {
    const withoutThink = String(raw || "").replace(
      streaming ? /<think\b[^>]*>[\s\S]*?(?:<\/think>|$)/gi : /<think\b[^>]*>[\s\S]*?<\/think>/gi,
      ""
    );
    const parsed = parseCues(withoutThink, { bareGestures: this._knownMotionNames(), streaming });
    const lead = parsed.text.length - parsed.text.trimStart().length;
    if (!lead) return parsed;
    return {
      text: parsed.text.slice(lead),
      cues: parsed.cues.map((c) => ({ ...c, at: Math.max(0, c.at - lead) })),
    };
  }

  _previewAssistantText(raw) {
    return this._parseReply(raw, { streaming: true }).text;
  }

  // Send a turn via the streaming endpoint, painting the reply and starting
  // speech as it arrives. Returns { resp, bubble, speech }: `bubble` is the
  // live assistant bubble (null if nothing streamed) and `speech` the TTS
  // stream (null if voice is off). Falls back to the non-streaming request
  // only when the stream endpoint doesn't exist.
  async _streamAssistantReply(message, { regenerate = false, loadingEl = null } = {}) {
    let raw = "";
    let bubble = null;
    let textEl = null;
    const speech = this.tts.isEnabled() ? this.tts.createStream(this._lastReplyVoice || {}) : null;
    const director = window.SARAH_AVATAR_DIRECTOR;
    director?.resetStream?.();
    let felt = false;

    const onDelta = (text) => {
      raw += text;
      const { text: preview, cues } = this._parseReply(raw, { streaming: true });
      // Her feeling opens the reply: it reaches her face, posture and voice
      // the moment she has it, before her first word is shown or spoken.
      const feel = !felt && cues.find((c) => c.type === "feel");
      if (feel) {
        felt = true;
        director?.feel?.(feel.value, feel.amount, feel.reason);
        speech?.setVoice?.({ emotion: feel.value, intensity: feel.amount ?? 0.5 });
      }
      if (!bubble) {
        if (!preview.trim()) return;
        this._hideLoadingIndicator(loadingEl);
        bubble = this.appendMessage("assistant", "…");
        textEl = bubble?.querySelector(".chat-bubble-text");
        bubble?.classList.add("is-streaming");
      }
      if (textEl) textEl.textContent = preview;
      this._scrollToBottom();
      if (speech) speech.push(preview, cues);
      else director?.streamCues?.(cues);
    };

    const modality = this._nextModality || "text";
    this._nextModality = null;
    // What she's doing with her tools, shown live in her bubble.
    const onTool = (evt) => {
      if (!bubble) {
        this._hideLoadingIndicator(loadingEl);
        bubble = this.appendMessage("assistant", "…");
        textEl = bubble?.querySelector(".chat-bubble-text");
        bubble?.classList.add("is-streaming");
      }
      this._showToolActivity(bubble, evt);
      if (evt.status === "start") director?.onWorking?.(evt.name);
      this._scrollToBottom();
    };
    // Spoken turns: if her words aren't ready in ~0.6 s, she acknowledges
    // you out loud ("Mm," / "Oh!") the way people do while they think.
    let fillerTimer = null;
    if (speech && modality === "voice") {
      const kind = /\?\s*$|^(what|how|why|when|where|who|can|could|would|should|do|does|is|are)\b/i.test(message)
        ? "think" : /!\s*$/.test(message) ? "react" : "ack";
      // Not once a first sentence is nearly ready to be spoken anyway.
      fillerTimer = setTimeout(() => {
        const said = this._parseReply(raw, { streaming: true }).text.trim();
        if (said.length < 30 && !this.tts._ttsPlaying) this.tts.playFiller(kind);
      }, 600);
    }
    try {
      const resp = await this.backend.chatStream(message, this.activeConversationId, { regenerate, onDelta, onTool, modality });
      clearTimeout(fillerTimer);
      this._setActing(false);
      bubble?.classList.remove("is-streaming");
      return { resp, bubble, speech };
    } catch (err) {
      clearTimeout(fillerTimer);
      this._setActing(false);
      speech?.cancel();
      bubble?.classList.remove("is-streaming");
      if (!err?.streamUnavailable || bubble) throw err;
      console.warn("[Chat] Streaming endpoint unavailable, using /api/chat:", err);
      const resp = await this.backend.chat(message, true, this.activeConversationId, regenerate);
      return { resp, bubble: null, speech: null };
    }
  }

  // One line per tool call inside her bubble: what she's doing, then ✓/✗.
  _showToolActivity(bubble, evt) {
    if (!bubble) return;
    let list = bubble.querySelector(".chat-tool-activity");
    if (!list) {
      list = document.createElement("div");
      list.className = "chat-tool-activity";
      bubble.insertBefore(list, bubble.querySelector(".chat-bubble-text"));
    }
    const a = evt.args || {};
    const short = (s, n = 60) => { s = String(s ?? ""); return s.length > n ? `${s.slice(0, n)}…` : s; };
    const host = (u) => { try { return new URL(u).hostname; } catch { return short(u, 40); } };
    const labels = {
      web_search: () => `🔎 Searching “${short(a.query)}”`,
      read_webpage: () => `📄 Reading ${host(a.url)}`,
      run_python: () => "🐍 Running some code",
      run_shell: () => `⌨️ ${short(a.command, 70)}`,
      read_file: () => `📂 Reading ${short(a.path)}`,
      list_directory: () => `📂 Looking in ${short(a.path)}`,
      write_file: () => `✏️ Writing ${short(a.path)}`,
      move_path: () => `📦 Moving ${short(a.source, 40)}`,
      delete_path: () => `🗑️ Recycling ${short(a.path)}`,
      open_item: () => `🚀 Opening ${short(a.target)}`,
      control_input: () => `🖱️ ${a.action}${a.text ? ` “${short(a.text, 30)}”` : ""}`,
      look: () => `👀 Looking: ${short(a.question)}`,
      remember: () => "🧠 Remembering that",
      set_reminder: () => `⏰ Reminder: ${short(a.text)}`,
      install_package: () => `📦 Installing ${short(a.package)}`,
      create_tool: () => `🛠️ Building a tool: ${a.name}`,
      research: () => `📚 Researching “${short(a.question)}”`,
      browser: () => ({
        open: `🌐 Browsing ${host(a.url || "")}`, read: "🌐 Reading the page", click: `🌐 Clicking ${a.ref ?? short(a.text, 30)}`,
        type: `🌐 Typing “${short(a.text, 30)}”`, scroll: "🌐 Scrolling", look: "🌐 Looking at the page", close: "🌐 Closing the browser",
      }[a.action] || `🌐 ${a.action}`),
      http_request: () => `🔗 ${a.method || "GET"} ${host(a.url || "")}`,
      window: () => ({
        list: "🪟 Checking open windows", focus: `🪟 Switching to ${short(a.title, 30)}`, wait: `🪟 Waiting for ${short(a.title, 30)}`,
        close: `🪟 Closing ${short(a.title, 30)}`, close_without_saving: `🪟 Closing ${short(a.title, 30)} without saving`,
        close_and_save: `🪟 Saving and closing ${short(a.title, 30)}`,
      }[a.action] || `🪟 ${a.action} ${short(a.title, 30)}`),
      add_skill: () => `🎓 Learning a skill`,
      use_skill: () => `🎓 Using skill: ${short(a.name, 40)}`,
      remove_skill: () => `🎓 Forgetting skill: ${short(a.name, 40)}`,
    };
    if (evt.status === "start") {
      const row = document.createElement("div");
      row.className = "chat-tool-row running";
      row.textContent = (labels[evt.name] || (() => `🛠️ ${evt.name}`))();
      list.appendChild(row);
      this._setActing(true);
    } else {
      const row = [...list.querySelectorAll(".chat-tool-row.running")].pop();
      if (row) {
        row.classList.remove("running");
        row.classList.add(evt.ok ? "ok" : "failed");
        row.title = evt.summary || "";
        row.textContent += evt.ok ? " ✓" : " ✗";
      }
    }
  }

  // The Stop button shows while she's acting; it pauses all her tools.
  _setActing(on) {
    const btn = (this.stopActionsBtn ||= document.getElementById("stop-actions"));
    if (!btn) return;
    if (!btn._bound) {
      btn._bound = true;
      btn.addEventListener("click", async () => {
        btn.textContent = "Stopping…";
        await this.backend.stopActions(60).catch(() => {});
        this.tts?.stop();
        btn.textContent = "■ Stop";
        this._setActing(false);
      });
    }
    btn.classList.toggle("sarah-hidden", !on);
  }

  // Put the final reply into the streamed bubble (or a new one). Returns the
  // visible reply text.
  _finalizeAssistantReply(resp, bubble) {
    const rawReply = resp?.reply || "(no reply)";
    if (bubble) {
      this._addActionsToExistingBubble(bubble, "assistant", rawReply, resp?.assistant_message_id);
      return bubble.querySelector(".chat-bubble-text")?.textContent || "";
    }
    const reply = this._sanitizeAssistantDisplayText(rawReply);
    this.appendMessage("assistant", reply, null, { messageId: resp?.assistant_message_id });
    return reply;
  }

  // Voice (and body) for the finished reply: complete the TTS stream if one
  // ran, otherwise speak the whole reply; with voice off the director acts
  // out any stage directions that haven't been performed yet.
  _speakFinalReply(reply, resp, speech, source) {
    const voice = { emotion: resp?.emotion, intensity: resp?.emotion_intensity };
    this._lastReplyVoice = voice;
    const { cues } = this._parseReply(resp?.reply || "");
    const director = window.SARAH_AVATAR_DIRECTOR;
    const feel = cues.find((c) => c.type === "feel");
    if (feel) director?.feel?.(feel.value, feel.amount, feel.reason); // no-op if already felt
    if (speech) {
      speech.finish(reply, voice, cues);
    } else if (this.tts.isEnabled()) {
      this.tts.speak(reply, { ...voice, cues }).catch((err) => console.warn("[TTS] Failed:", err));
    } else {
      if (director) {
        // Cues already acted out while streaming are skipped.
        if (cues.length) director.streamCues(cues, { final: true });
        else director.performText(reply);
      }
      this._setAvatarMode("idle", { source });
    }
  }

  // Something Sarah said on her own (she sensed a touch, the user coming
  // back...). Already saved by the backend; shown and spoken like a reply.
  // It never talks over her: if she's mid-sentence, the line appears and her
  // body acts it, without cutting her voice off.
  presentSpontaneousReply(out, conversationId = this.activeConversationId) {
    if (!out?.reply || conversationId !== this.activeConversationId) return;
    const reply = this._sanitizeAssistantDisplayText(out.reply);
    this.appendMessage("assistant", reply, null, { messageId: out.assistant_message_id });
    this._scrollToBottom();
    window.SARAH_AVATAR_SYSTEM?.onAIResponse?.(reply, {
      emotion: out.emotion,
      intensity: out.emotion_intensity,
      source: "spontaneous",
    });
    const resp = { reply: out.reply, emotion: out.emotion, emotion_intensity: out.emotion_intensity };
    if (this.tts.isEnabled() && this.tts.isMuting()) {
      const { cues } = this._parseReply(out.reply);
      window.SARAH_AVATAR_DIRECTOR?.performText?.(reply, cues);
      return;
    }
    this._speakFinalReply(reply, resp, null, "spontaneous");
  }

  // Display text for an assistant message: stage directions, <think> blocks
  // and leaked internal notes removed. Pure: it's also used for history, so
  // it must never make the avatar act.
  _sanitizeAssistantDisplayText(text) {
    let cleaned = this._parseReply(text).text
      .replace(/\[\s*(?:add\s+)?internal\s+notes?[\w\s,.;:'"!?/-]*\]/gi, "");

    const internalHeading =
      /^\s*(?:#{1,6}\s*)?(?:[*_`]*|\[)?(?:creator\s+note|zero\s+note|jessie\s+note|sarah\s+note|internal\s+notes?|assistant\s+notes?|private\s+notes?|intent|reasoning|system\s+prompt)(?:[*_`]*|\])?\s*(?::|-|$)/i;

    const kept = [];
    for (const line of cleaned.split(/\r?\n/)) {
      if (internalHeading.test(line)) {
        if (kept.join("\n").trim()) break;
        continue;
      }
      kept.push(line);
    }

    cleaned = kept.join("\n").replace(/\n\s*(?:---|\*\*\*)\s*$/g, "").replace(/\n{3,}/g, "\n\n").trim();
    return cleaned || "I'm here.";
  }

  _createMessageBubble(role, text, imageUrl = null, meta = {}) {
    const normalizedRole = this._normalizeMessageRole(role);
    const displayText = normalizedRole === "assistant"
      ? this._sanitizeAssistantDisplayText(text)
      : String(text || "");
    const messageId = meta.messageId || null;
    const bubble = document.createElement("div");
    bubble.classList.add("chat-bubble", normalizedRole === "user" ? "user" : "sarah");
    bubble.dataset.role = normalizedRole;
    if (messageId) bubble.dataset.messageId = messageId;

    if (imageUrl) {
      const img = document.createElement("img");
      img.src = imageUrl;
      img.className = "chat-bubble-image";
      img.alt = "Attached image";
      bubble.appendChild(img);
    }

    const body = document.createElement("div");
    body.classList.add("chat-bubble-text");
    body.textContent = displayText;
    bubble.appendChild(body);

    if (meta.attachmentId) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chat-attachment-chip";
      chip.textContent =
        meta.attachmentLabel ||
        (meta.attachmentKind === "recording" ? "View recording" : "View screenshot");
      chip.dataset.attachmentId = meta.attachmentId;
      bubble.appendChild(chip);
    }

    bubble.appendChild(this._createChatActions(normalizedRole, messageId));
    return bubble;
  }

  appendMessage(role, text, imageUrl = null, meta = {}) {
    if (!this.chatLog) return null;

    const bubble = this._createMessageBubble(role, text, imageUrl, meta);
    this.chatLog.appendChild(bubble);

    if (!meta.noScroll) {
      requestAnimationFrame(() => {
        if (this.chatLog) {
          this.chatLog.scrollTop = this.chatLog.scrollHeight;
        }
      });
    }

    return bubble;
  }

  /**
   * Add action buttons to an existing bubble (used when we get messageId after API response)
   */
  _addActionsToExistingBubble(bubble, role, text, messageId) {
    const normalizedRole = this._normalizeMessageRole(role);
    const displayText = normalizedRole === "assistant"
      ? this._sanitizeAssistantDisplayText(text)
      : String(text || "");
    const oldActions = bubble.querySelector(".chat-bubble-actions");
    if (oldActions) oldActions.remove();

    bubble.dataset.role = normalizedRole;
    if (messageId) bubble.dataset.messageId = messageId;

    const textEl = bubble.querySelector(".chat-bubble-text");
    if (textEl) textEl.textContent = displayText;

    bubble.appendChild(this._createChatActions(normalizedRole, messageId));
    return bubble;
  }
  _editMessage(bubble, messageId, originalText) {
    const textEl = bubble.querySelector(".chat-bubble-text");
    if (!textEl) return;

    const textarea = document.createElement("textarea");
    textarea.className = "chat-edit-textarea";
    textarea.value = originalText;

    const editActions = document.createElement("div");
    editActions.className = "chat-edit-actions";

    const saveBtn = document.createElement("button");
    saveBtn.className = "chat-edit-btn save";
    saveBtn.textContent = "Save & Regenerate";

    const cancelBtn = document.createElement("button");
    cancelBtn.className = "chat-edit-btn cancel";
    cancelBtn.textContent = "Cancel";

    editActions.appendChild(cancelBtn);
    editActions.appendChild(saveBtn);

    textEl.style.display = "none";
    bubble.insertBefore(textarea, bubble.querySelector(".chat-bubble-actions"));
    bubble.insertBefore(editActions, bubble.querySelector(".chat-bubble-actions"));
    textarea.focus();

    cancelBtn.addEventListener("click", () => {
      textarea.remove();
      editActions.remove();
      textEl.style.display = "";
    });

    saveBtn.addEventListener("click", async () => {
      const newText = textarea.value.trim();
      if (!newText || newText === originalText) {
        textarea.remove();
        editActions.remove();
        textEl.style.display = "";
        return;
      }

      try {
        // Update the message in DB
        await this.backend.updateMessage(messageId, newText);
        // Delete all messages after this one (removes old AI responses)
        await this.backend.deleteMessagesAfter(this.activeConversationId, messageId);

        // Update the bubble text directly
        textEl.textContent = newText;
        textEl.style.display = "";
        textarea.remove();
        editActions.remove();

        // Add "Edited" tag to the bubble
        this._addMessageTag(bubble, "Edited");

        // Remove any AI bubbles after this user bubble
        let nextSibling = bubble.nextElementSibling;
        while (nextSibling) {
          const toRemove = nextSibling;
          nextSibling = nextSibling.nextElementSibling;
          toRemove.remove();
        }

        // Send the edited message to get new AI response
        await this._sendEditedMessage(newText, false); // false = not a re-roll
      } catch (err) {
        console.error("Edit failed:", err);
        alert("Failed to edit message");
        textarea.remove();
        editActions.remove();
        textEl.style.display = "";
      }
    });
  }

  /**
   * Add a small tag under a message bubble (e.g., "Edited", "Re-Rolled")
   */
  _addMessageTag(bubble, tagText) {
    // Remove existing tag if present
    const existingTag = bubble.querySelector(".chat-bubble-tag");
    if (existingTag) existingTag.remove();

    const tag = document.createElement("div");
    tag.className = "chat-bubble-tag";
    tag.textContent = tagText;
    bubble.appendChild(tag);
  }

  async _sendEditedMessage(text, isReroll = false) {
    // Re-send the edited user message to get a new AI response
    this.setInputDisabled(true);
    this.setStatus("Thinking...");
    this._setAvatarMode("thinking", { source: "chat" });
    this._setAvatarMode("thinking", { source: "chat-regenerate" });

    // Show loading indicator
    const loadingEl = this._showLoadingIndicator();

    try {
      // IMPORTANT: regenerate=true skips saving the user message (already exists)
      const { resp: result, bubble, speech } = await this._streamAssistantReply(text, {
        regenerate: true,
        loadingEl,
      });

      // Remove loading indicator
      this._hideLoadingIndicator(loadingEl);

      if (result.reply) {
        console.log("[ReRoll] assistant_message_id:", result.assistant_message_id);
        const reply = this._finalizeAssistantReply(result, bubble);

        // Issue #3: re-sync from /api/context_info for cumulative tokens.
        this.syncContextInfo(this.activeConversationId)
          .catch(err => console.warn("[Token] Context sync failed:", err));

        // Add "Re-Rolled" tag if this was a re-roll
        if (isReroll) {
          const lastBubble = this.chatLog?.lastElementChild;
          if (lastBubble) {
            this._addMessageTag(lastBubble, "Re-Rolled");
          }
        }

        this._speakFinalReply(reply, result, speech, "chat-regenerate-complete");
      } else {
        speech?.cancel();
      }
    } catch (err) {
      console.error("Regenerate failed:", err);
      this._hideLoadingIndicator(loadingEl);
      this.appendMessage("assistant", "Failed to regenerate response. Please try again.");
      this._setAvatarMode("idle", { source: "chat-regenerate-error" });
    }
    this.setInputDisabled(false);
    this.setStatus("Ready");
  }

  async _regenerateResponse(messageId) {
    console.log("[Regenerate] Starting regeneration for messageId:", messageId);
    if (!this.activeConversationId) {
      console.warn("[Regenerate] No active conversation ID");
      return;
    }

    try {
      // Find the user message before this AI message
      const messages = await this.backend.getConversationMessages(this.activeConversationId);
      console.log("[Regenerate] Messages in conversation:", messages.length);
      const msgIndex = messages.findIndex(m => m.id === messageId);
      console.log("[Regenerate] Message index:", msgIndex);
      if (msgIndex <= 0) {
        console.warn("[Regenerate] Message not found or at index 0");
        return;
      }

      const userMsg = messages[msgIndex - 1];
      console.log("[Regenerate] User message found:", userMsg.id, userMsg.role);
      if (userMsg.role !== "user") {
        console.warn("[Regenerate] Previous message is not user");
        return;
      }

      // Delete this AI response and any after
      console.log("[Regenerate] Deleting messages after:", userMsg.id);
      await this.backend.deleteMessagesAfter(this.activeConversationId, userMsg.id);

      // Find and remove the AI bubble from the DOM
      const aiBubble = this.chatLog?.querySelector(`[data-message-id="${messageId}"]`);
      if (aiBubble) {
        // Remove this bubble and any after it
        let nextSibling = aiBubble.nextElementSibling;
        aiBubble.remove();
        while (nextSibling) {
          const toRemove = nextSibling;
          nextSibling = nextSibling.nextElementSibling;
          toRemove.remove();
        }
      }

      // Send regenerate request (isReroll = true)
      console.log("[Regenerate] Sending re-roll with content:", userMsg.content.substring(0, 50) + "...");
      await this._sendEditedMessage(userMsg.content, true);
    } catch (err) {
      console.error("[Regenerate] Failed:", err);
    }
  }

  clearChat() {
    if (this.chatLog) this.chatLog.innerHTML = "";
    // Issue #19: drop attachments tied to the previous conversation so the
    // preview card under the input doesn't linger as a phantom "context" chip.
    this.attachedSnippet = null;
    this.attachedImage = null;
    if (typeof this._clearPendingAttachments === "function") {
      this._clearPendingAttachments();
    }
    const snippetStatus = document.getElementById("snippet-status");
    if (snippetStatus) snippetStatus.textContent = "";
  }

  _showLoadingIndicator() {
    if (!this.chatLog) return null;

    const loading = document.createElement("div");
    loading.className = "chat-loading";
    loading.innerHTML = `
      <div class="chat-loading-dots">
        <span class="chat-loading-dot"></span>
        <span class="chat-loading-dot"></span>
        <span class="chat-loading-dot"></span>
      </div>
      <span>Sarah is thinking...</span>
    `;
    this.chatLog.appendChild(loading);
    this.chatLog.scrollTop = this.chatLog.scrollHeight;
    return loading;
  }

  _hideLoadingIndicator(el) {
    if (el && el.parentNode) {
      el.parentNode.removeChild(el);
    }
  }

  setInputDisabled(disabled) {
    if (this.chatInput) this.chatInput.disabled = disabled;
    if (this.chatSend) this.chatSend.disabled = disabled;
  }

  setStatus(text) {
    console.log("[Status]", text);
    if (this.statusBarEl) this.statusBarEl.textContent = text;
  }

  _setAvatarMode(mode, options = {}) {
    try {
      window.SARAH_LIVE2D?.setAvatarMode?.(mode, options);
    } catch (err) {
      console.warn("[Live2D] Avatar mode update failed:", err);
    }
  }

  async syncAvatarBaselineFromContext(conversationId = this.activeConversationId) {
    if (!conversationId || !window.SARAH_AVATAR_SYSTEM?.updateFromContext) return null;
    try {
      const result = await this.backend.getAvatarContextWindow(conversationId);
      if (result?.ok && Array.isArray(result.messages)) {
        const state = window.SARAH_AVATAR_SYSTEM.updateFromContext(result.messages, {
          source: "context-window",
          conversationId,
        });
        console.log(
          "[AvatarContext] Baseline:",
          state.baseEmotion,
          `affinity=${state.affinity.toFixed(2)}`,
          `warmth=${state.baseWarmth.toFixed(2)}`,
          `volatility=${state.volatility.toFixed(2)}`
        );
        return state;
      }
    } catch (err) {
      console.warn("[AvatarContext] Failed to sync baseline:", err);
    }
    return null;
  }

  _mergeLLMStatus(info = {}) {
    const next = {
      ...this.llmStatus,
      ...info,
    };
    if (!next.provider) {
      next.provider = next.mode === "local" ? "Ollama" : "OpenRouter";
    }
    if (!next.model_name) {
      next.model_name = next.mode === "local" ? next.local_model : "nvidia/nemotron-3-ultra-550b-a55b:free";
    }
    if (next.mode === "online" && next.model_name === "openrouter/auto") {
      next.auto_routed = true;
      next.model_label = "OpenRouter auto-routed";
    }
    this.llmStatus = next;
    this.llmMode = next.mode || this.llmMode;
    return this._renderLLMStatus();
  }

  _renderLLMStatus() {
    const rendered = window.SARAH_MODEL_STATUS?.render?.(
      {
        modeButton: this.llmModeBtn,
        modelText: this.modelStatusTextEl,
        contextText: this.modelContextTextEl,
      },
      this.llmStatus
    );

    if (rendered) {
      this.llmStatus = { ...this.llmStatus, ...rendered };
    } else {
      this._updateLLMModeUI();
    }
    this._renderModelDiagnostics();
    return this.llmStatus;
  }

  _formatDiagText(value, fallback = "unknown") {
    const text = value == null || value === "" ? fallback : String(value);
    return text.length > 26 ? `${text.slice(0, 23)}...` : text;
  }

  _formatWakeConfidence(value, state) {
    if (state === "unknown" || value == null) return "unknown";
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "unknown";
    return `${Math.round(numeric * 100)}%`;
  }

  _setDiagnosticStateClass(el, state) {
    if (!el) return;
    el.classList.remove("is-awake", "is-blocked", "is-pending");
    if (state === "awake") {
      el.classList.add("is-awake");
    } else if (state === "blocked" || state === "error" || state === "offline") {
      el.classList.add("is-blocked");
    } else if (state === "prefix_pending" || state === "listening") {
      el.classList.add("is-pending");
    }
  }

  _renderWakeDiagnostics(data = {}) {
    if (this.diagnostics?.store) {
      this.diagnostics.store.patchWakeDiagnostics(data);
    }
    const els = this.diagnosticsEls || {};
    const state = data.current_state || (data.listener_running ? "listening" : "offline");
    const reason = data.wake_reason || data.reason || "unknown";
    const cooldown = Number(data.cooldown_remaining_seconds || 0);
    const prefixHits = Number(data.prefix_hit_count || 0);
    const prefixNeed = Number(data.prefix_hits_required || 2);

    if (els.wakePulse) els.wakePulse.textContent = this._formatDiagText(state);
    if (els.wakeState) els.wakeState.textContent = this._formatDiagText(state);
    if (els.wakeTranscript) {
      els.wakeTranscript.textContent = this._formatDiagText(data.last_transcript, "none");
      els.wakeTranscript.title = data.last_transcript || "none";
    }
    if (els.wakeConfidence) {
      const confidence = this._formatWakeConfidence(data.last_confidence, data.confidence_state);
      els.wakeConfidence.textContent = confidence;
      els.wakeConfidence.title = data.confidence_state || "unknown";
    }
    if (els.wakeReason) {
      els.wakeReason.textContent = this._formatDiagText(reason);
      els.wakeReason.title = `${data.reason || "unknown"} / ${reason}`;
    }
    if (els.wakeCooldown) els.wakeCooldown.textContent = `${cooldown.toFixed(1)}s`;
    if (els.wakePrefix) els.wakePrefix.textContent = `${prefixHits}/${prefixNeed}`;

    this._setDiagnosticStateClass(els.wakePulse, state);
    this._setDiagnosticStateClass(els.wakeState, state);
  }

  _renderModelDiagnostics() {
    this.diagnostics?.patchModelStatus?.(this.llmStatus || {});
    const els = this.diagnosticsEls || {};
    const status = this.llmStatus || {};
    const used = Number(status.tokens_used || 0);
    const budget = Number(status.token_budget || status.context_window_tokens || 0);
    const percent = budget > 0 ? Math.min(100, Math.round((used / budget) * 100)) : 0;

    if (els.modelState) els.modelState.textContent = status.mode === "local" ? "local" : "online";
    if (els.modelMode) els.modelMode.textContent = this._formatDiagText(status.provider || status.mode);
    if (els.modelContext) els.modelContext.textContent = `${percent}%`;
    this._setDiagnosticStateClass(els.modelState, percent >= 90 ? "blocked" : percent >= 70 ? "prefix_pending" : "awake");
  }

  async _ensureDiagnosticsDashboard() {
    if (this.diagnostics) return this.diagnostics;
    if (this._diagnosticsLoading) return this._diagnosticsLoading;
    this._diagnosticsLoading = (async () => {
      const mod = await import("./scripts/diagnostics/diagnostics-dashboard.js");
      const instance = new mod.SarahDiagnosticsDashboard({
        root: this.diagnosticsDashboard,
        backend: this.backend,
        apiBase: API_BASE,
        getLlmStatus: () => this.llmStatus,
      });
      instance.mount();
      this.diagnostics = instance;
      return instance;
    })();
    try {
      return await this._diagnosticsLoading;
    } finally {
      this._diagnosticsLoading = null;
    }
  }

  async openDiagnosticsDashboard() {
    const diag = await this._ensureDiagnosticsDashboard().catch((err) => {
      console.warn("[Diagnostics] Failed to lazy-load:", err);
      return null;
    });
    if (diag?.open) {
      diag.open();
      this.btnDiagnostics?.classList.add("active");
      this.diagnosticsPollTimer = null;
      return;
    }
    if (!this.diagnosticsDashboard) return;
    this.diagnosticsDashboard.classList.remove("hidden");
    this.diagnosticsDashboard.setAttribute("aria-hidden", "false");
    this.btnDiagnostics?.classList.add("active");
    this._renderWakeDiagnostics({ current_state: "syncing", reason: "startup" });
    this._renderModelDiagnostics();
    if (this.diagnosticsPollTimer) clearInterval(this.diagnosticsPollTimer);
    this.diagnosticsPollTimer = setInterval(() => this._pollDiagnosticsPanel(), 1000);
    this._pollDiagnosticsPanel();
  }

  closeDiagnosticsDashboard() {
    if (this.diagnostics?.close) {
      this.diagnostics.close();
      this.btnDiagnostics?.classList.remove("active");
      if (this.diagnosticsPollTimer) {
        clearInterval(this.diagnosticsPollTimer);
        this.diagnosticsPollTimer = null;
      }
      return;
    }
    if (!this.diagnosticsDashboard) return;
    this.diagnosticsDashboard.classList.add("hidden");
    this.diagnosticsDashboard.setAttribute("aria-hidden", "true");
    this.btnDiagnostics?.classList.remove("active");
    if (this.diagnosticsPollTimer) {
      clearInterval(this.diagnosticsPollTimer);
      this.diagnosticsPollTimer = null;
    }
  }

  toggleDiagnosticsDashboard() {
    if (!this.diagnosticsDashboard) return;
    if (this.diagnosticsDashboard.classList.contains("hidden")) {
      this.openDiagnosticsDashboard();
    } else {
      this.closeDiagnosticsDashboard();
    }
  }

  async _pollDiagnosticsPanel() {
    if (this.diagnosticsDashboard?.classList.contains("hidden")) return;
    if (this._diagnosticsRequestInFlight) return;
    this._diagnosticsRequestInFlight = true;
    try {
      const data = await this.backend.getWakeDiagnostics();
      if (data?.ok) {
        this._renderWakeDiagnostics(data);
      } else {
        this._renderWakeDiagnostics({
          current_state: "offline",
          reason: data?.error || "unreachable",
          wake_reason: "diagnostics_error",
        });
      }
    } finally {
      this._diagnosticsRequestInFlight = false;
    }
  }

  /**
   * Update the token context indicator bar
   * @param {number|null} tokensUsed - Tokens used in last request
   * @param {number|null} tokenBudget - Total token budget (default 1,048,756)
   */
  updateTokenIndicator(tokensUsed, tokenBudget = this.llmStatus?.context_window_tokens || this.llmStatus?.token_budget || 1000000) {
    const barFill = document.getElementById("token-bar-fill");
    const usageText = document.getElementById("token-usage-text");

    if (!barFill || !usageText) return;

    // Use defaults if not provided
    const used = tokensUsed ?? 0;
    const budget = tokenBudget ?? 1000000;

    // Calculate percentage
    const percentage = Math.min(100, Math.round((used / budget) * 100));

    // Update bar width
    barFill.style.width = `${percentage}%`;

    // Update text
    const fmt = window.SARAH_MODEL_STATUS?.formatTokenCount || ((value) => String(Math.round(Number(value) || 0)));
    usageText.textContent = `${fmt(used)} / ${fmt(budget)} (${percentage}%)`;
    this._mergeLLMStatus({
      tokens_used: used,
      token_budget: budget,
      context_window_tokens: budget,
    });

    // Update color based on usage level
    barFill.classList.remove("warning", "critical");
    if (percentage >= 90) {
      barFill.classList.add("critical");
    } else if (percentage >= 70) {
      barFill.classList.add("warning");
    }

    console.log(`[Token] Context usage: ${used}/${budget} (${percentage}%)`);
  }

  //---------------------------------------------------------------------------
  // Voice Toggle
  //---------------------------------------------------------------------------

  _updateVoiceToggleUI() {
    const enabled = this.tts.isEnabled();
    if (this.voiceToggleBtn) {
      this.voiceToggleBtn.textContent = enabled ? "Voice: ON" : "Voice: OFF";
    }
  }

  toggleVoice() {
    const state = !this.tts.isEnabled();
    this.tts.setEnabled(state);
    if (!state) this.tts.stop();
    this._updateVoiceToggleUI();
    this._restartWakeWatcher("voice-toggle");
  }

  //---------------------------------------------------------------------------
  // Wake Word → Voice Capture → STT
  //---------------------------------------------------------------------------

  _getWakePollIntervalMs() {
    const isVisible = document.visibilityState === "visible";
    const hasFocus = typeof document.hasFocus === "function" ? document.hasFocus() : true;
    const voiceEnabled = this.tts.isEnabled();
    return voiceEnabled && isVisible && hasFocus ? 250 : 1000;
  }

  _restartWakeWatcher(reason = "state-change") {
    if (this.wakePollTimer) clearInterval(this.wakePollTimer);
    this._wakePollMs = this._getWakePollIntervalMs();
    this.wakePollTimer = setInterval(() => this._pollWakeWord(), this._wakePollMs);
    console.log(`[Voice] Wake watcher interval=${this._wakePollMs}ms reason=${reason}`);
  }

  _startWakeWatcher() {
    this._restartWakeWatcher("startup");

    if (!this._wakeWatcherBound) {
      const onFocusChange = () => this._restartWakeWatcher("focus-change");
      window.addEventListener("focus", onFocusChange);
      window.addEventListener("blur", onFocusChange);
      document.addEventListener("visibilitychange", onFocusChange);
      this._wakeWatcherBound = true;
    }

    // Pre-warm microphone access (non-blocking) for faster response
    this._prewarmMicrophone();
  }

  async _prewarmMicrophone() {
    // Retired: live voice owns the microphone, and Mic OFF must mean off, so
    // no spare stream is held open "just in case".
    return;
    // eslint-disable-next-line no-unreachable
    if (this._cachedMicStream) return;
    try {
      this._cachedMicStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
      });
      console.log("[Voice] Microphone pre-warmed for faster response");
    } catch (err) {
      console.warn("[Voice] Microphone pre-warm failed (will request on demand):", err.message);
    }
  }

  async _pollWakeWord() {
    if (this.voiceListening || this.voiceProcessing || this._wakeRequestInFlight)
      return;
    // Live voice listens continuously; the wake word would double-capture.
    if (this.liveVoice?.active) return;
    // When voice is OFF, do not poll backend wake at all. Without this, false
    // wake fires from the backend (e.g. vosk hallucinating "hi"/"hey" on noise)
    // would re-activate the listening indicator and trigger silent captures.
    if (!this.tts?.isEnabled?.()) return;
    // Half-duplex: don't poll backend wake while TTS is playing or in tail.
    // The mic would otherwise pick up Sarah's own voice and trigger wake.
    if (this.tts?.isMuting?.()) return;
    this._wakeRequestInFlight = true;
    try {
      const status = await this.backend.checkWake();
      if (status?.wake) {
        if (window.B6_TIMING) console.log(`[TIMING] stage=wake.poll_received ts=${Date.now()}`);
        console.log("[Voice] ✨ WAKE WORD DETECTED! Starting voice capture...");
        this.setStatus("Wake word detected. Listening...");
        this._setAvatarMode("listening", { source: "wake-word" });
        await this._beginVoiceCapture();
      }
    } catch (err) {
      console.warn("[Voice] Wake poll failed:", err);
    } finally {
      this._wakeRequestInFlight = false;
    }
  }

  async _beginVoiceCapture() {
    if (this.voiceListening) {
      console.log("[Voice] Already listening, skipping");
      return;
    }
    // Mic OFF means off: the old record-a-clip path mustn't open it either.
    if (this._micOn === false) {
      this.setStatus("Mic is off");
      return;
    }
    if (!navigator?.mediaDevices?.getUserMedia) {
      console.warn("[Voice] getUserMedia not available");
      this.setStatus("Microphone API not available");
      return;
    }
    if (typeof MediaRecorder === "undefined") {
      console.warn("[Voice] MediaRecorder not supported in this environment");
      this.setStatus("MediaRecorder not supported");
      return;
    }

    console.log("[Voice] Starting voice capture...");
    const startTime = performance.now();

    try {
      this.voiceProcessing = true;
      this.tts.stop();

      // Use cached microphone stream for faster response, or request new one
      if (this._cachedMicStream && this._cachedMicStream.active) {
        this.voiceStream = this._cachedMicStream;
        console.log("[Voice] Using cached microphone stream");
      } else {
        console.log("[Voice] Requesting microphone access...");
        this.voiceStream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
        });
        this._cachedMicStream = this.voiceStream;  // Cache for next time
        console.log("[Voice] Microphone access granted");
      }

      this.voiceChunks = [];

      // Check supported MIME types
      let mimeType = "audio/webm";
      if (!MediaRecorder.isTypeSupported(mimeType)) {
        mimeType = "audio/webm;codecs=opus";
      }
      this._currentMimeType = mimeType;  // Store for later use

      this.voiceRecorder = new MediaRecorder(this.voiceStream, { mimeType });

      this.voiceRecorder.ondataavailable = (ev) => {
        if (ev.data && ev.data.size > 0) {
          this.voiceChunks.push(ev.data);
        }
      };

      this.voiceRecorder.onstop = async () => {
        console.log("[Voice] Recorder stopped, chunks:", this.voiceChunks.length);
        const blob = this.voiceChunks.length > 0
          ? new Blob(this.voiceChunks, { type: this._currentMimeType })
          : null;

        // Don't cleanup mic stream - keep it cached for next use
        this._cleanupVoiceState(false);  // false = don't close mic stream

        try {
          if (blob && blob.size > 0) {
            await this._handleVoiceBlob(blob);
          } else {
            this.setStatus("Didn't catch that. Try again.");
          }
        } finally {
          this.voiceProcessing = false;
        }
      };

      this.voiceRecorder.onerror = (err) => {
        console.error("[Voice] Recorder error:", err);
        this.setStatus("Recording error");
        this.voiceProcessing = false;
        this._cleanupVoiceState(false);
      };

      this.voiceListening = true;
      this._setVoiceListeningUI(true);
      this.setStatus("Listening... (auto-stops after 6s silence)");
      this._setAvatarMode("listening", { source: "voice-capture" });

      this.voiceRecorder.start(200);  // Reduced from 300ms to 200ms for faster chunks
      console.log(`[Voice] Recorder started in ${(performance.now() - startTime).toFixed(0)}ms`);

      // Start silence detection - auto-stop after 6 seconds of silence
      this._startSilenceDetection();
    } catch (err) {
      console.error("[Voice] Failed to start microphone:", err);
      this.setStatus("Mic unavailable: " + err.message);
      this.voiceProcessing = false;
      this._cleanupVoiceState();
    }
  }

  _stopVoiceCapture() {
    if (!this.voiceRecorder) return;
    if (this.voiceRecorder.state !== "inactive") {
      try {
        this.voiceRecorder.stop();
      } catch (err) {
        console.warn("[Voice] Stop failed:", err);
        this.voiceProcessing = false;
        this._cleanupVoiceState(false);  // Keep mic stream cached
      }
    }
  }

  _cleanupVoiceState(closeMicStream = true) {
    if (this.voiceAutoStopTimer) {
      clearTimeout(this.voiceAutoStopTimer);
      this.voiceAutoStopTimer = null;
    }

    // Stop silence detection
    this._stopSilenceDetection();

    // Only close mic stream if explicitly requested (keeps it cached for faster next use)
    if (closeMicStream && this.voiceStream) {
      this.voiceStream.getTracks().forEach((t) => t.stop());
      this.voiceStream = null;
      this._cachedMicStream = null;
    }

    this.voiceRecorder = null;
    this.voiceChunks = [];
    this.voiceListening = false;
    this._setVoiceListeningUI(false);
    this._setAvatarMode("idle", { source: "voice-cleanup" });
  }

  /**
   * Start silence detection - automatically stop recording after 6 seconds of silence
   */
  _startSilenceDetection() {
    if (!this.voiceStream) return;

    try {
      // Create audio context and analyzer
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      this._silenceAudioContext = new AudioCtx();
      this._silenceAnalyser = this._silenceAudioContext.createAnalyser();
      this._silenceAnalyser.fftSize = 512;
      this._silenceAnalyser.smoothingTimeConstant = 0.1;

      const source = this._silenceAudioContext.createMediaStreamSource(this.voiceStream);
      source.connect(this._silenceAnalyser);

      this._silenceDataArray = new Uint8Array(this._silenceAnalyser.frequencyBinCount);
      this._lastSoundTime = Date.now();
      this._silenceThreshold = 10;  // Lower threshold for better sensitivity (0-255)
      this._silenceTimeout = 6000;  // 6 seconds of silence to auto-stop
      this._hasDetectedSpeech = false;  // Track if user has spoken at all

      console.log("[Voice] Silence detection started (6s timeout after speech detected)");

      // Start monitoring
      const isVisible = document.visibilityState === "visible";
      const hasFocus = typeof document.hasFocus === "function" ? document.hasFocus() : true;
      const silenceCheckMs = isVisible && hasFocus ? 120 : 220;
      this._silenceCheckInterval = setInterval(() => {
        if (!this._silenceAnalyser || !this.voiceListening) {
          this._stopSilenceDetection();
          return;
        }

        this._silenceAnalyser.getByteFrequencyData(this._silenceDataArray);

        // Calculate average volume
        let sum = 0;
        for (let i = 0; i < this._silenceDataArray.length; i++) {
          sum += this._silenceDataArray[i];
        }
        const avgVolume = sum / this._silenceDataArray.length;

        // Check if sound detected
        if (avgVolume > this._silenceThreshold) {
          this._lastSoundTime = Date.now();
          if (!this._hasDetectedSpeech) {
            this._hasDetectedSpeech = true;
            console.log("[Voice] Speech detected, starting silence timer");
          }
        }

        // Only check silence timeout AFTER user has spoken at least once
        if (this._hasDetectedSpeech) {
          const silenceDuration = Date.now() - this._lastSoundTime;
          if (silenceDuration >= this._silenceTimeout) {
            console.log("[Voice] 6 seconds of silence detected after speech, auto-stopping...");
            this.setStatus("Processing your request...");
            this._stopVoiceCapture();
          }
        }
      }, silenceCheckMs);

    } catch (err) {
      console.warn("[Voice] Failed to start silence detection:", err);
    }
  }

  /**
   * Stop silence detection and clean up resources
   */
  _stopSilenceDetection() {
    if (this._silenceCheckInterval) {
      clearInterval(this._silenceCheckInterval);
      this._silenceCheckInterval = null;
    }

    if (this._silenceAudioContext && this._silenceAudioContext.state !== "closed") {
      try {
        this._silenceAudioContext.close();
      } catch (err) {
        // Ignore close errors
      }
    }
    this._silenceAudioContext = null;
    this._silenceAnalyser = null;
    this._silenceDataArray = null;
  }

  async _handleVoiceBlob(blob) {
    const startTime = performance.now();
    console.log("[Voice] Processing voice blob, size:", blob.size);
    // Half-duplex: drop the capture if it overlapped with TTS playback.
    // Without this, the mic recording would contain Sarah's own voice.
    if (this.tts?.isMuting?.()) {
      console.warn("[Voice] Discarding capture — overlapped with TTS playback");
      this.setStatus("");
      this._setAvatarMode("idle", { source: "stt-tts-overlap" });
      return;
    }
    this.setStatus("Transcribing...");
    this._setAvatarMode("thinking", { source: "stt" });
    try {
      // Send WebM directly to backend (no conversion needed - backend supports it)
      const audioB64 = await this._blobToBase64(blob);
      if (!audioB64) {
        this.setStatus("Audio capture failed.");
        this._setAvatarMode("idle", { source: "stt-empty-audio" });
        return;
      }

      console.log(`[Voice] Sending to STT (${(performance.now() - startTime).toFixed(0)}ms prep)`);

      const result = await this.backend.stt(
        audioB64,
        blob.type || "audio/webm"  // Use original MIME type
      );

      console.log("[Voice] STT result:", result);

      const text = (result?.text || "").trim();
      const normalized = text.toLowerCase();

      if (result?.ok === false && result?.error) {
        console.error("[Voice] STT error:", result.error);
        this.setStatus(`Voice to text failed: ${result.error}`);
        this._setAvatarMode("idle", { source: "stt-error" });
        return;
      }

      if (!text) {
        console.warn("[Voice] No text transcribed");
        this.setStatus("No speech detected.");
        this._setAvatarMode("idle", { source: "stt-no-speech" });
        return;
      }

      // Reject punctuation-only / sub-3-char STT outputs to avoid TTS feedback loops
      // where mic picks up Sarah's own voice and STT returns ".", ",", "uh", etc.
      const stripped = text.replace(/[\s\p{P}\p{S}]+/gu, "");
      if (stripped.length < 3) {
        // Issue #16: rate-limit this warning — it fires constantly on background
        // noise and floods the console. Keep one log per ~10s.
        const now = Date.now();
        if (!this._lastLowSigLogAt || now - this._lastLowSigLogAt > 10000) {
          console.warn("[Voice] Rejected low-signal STT result:", text);
          this._lastLowSigLogAt = now;
        }
        this.setStatus("No speech detected.");
        this._setAvatarMode("idle", { source: "stt-low-signal" });
        return;
      }

      console.log("[Voice] Transcribed text:", text);

      // Check if the user ONLY said "stop" to deactivate voice mode
      if (normalized.trim() === "stop" || normalized.trim() === "stop.") {
        console.log("[Voice] Stop command detected");
        this.setStatus("Voice mode stopped.");
        this._setAvatarMode("idle", { source: "voice-stop-command" });
        return;
      }

      // Remove trailing "stop" or "stop." from commands
      let cleanText = text;
      if (normalized.endsWith(" stop.") || normalized.endsWith(" stop")) {
        cleanText = text.replace(/\s+stop\.?$/i, "").trim();
        console.log("[Voice] Removed trailing 'stop' from command");
      }

      // Check for special voice commands BEFORE sending to AI
      const handled = await this._handleSpecialVoiceCommand(cleanText, normalized);
      if (handled) {
        this._setAvatarMode("idle", { source: "voice-command-handled" });
        return; // Command was handled, don't send to AI
      }

      // Display the transcribed text in the input field
      if (this.chatInput) {
        this.chatInput.value = cleanText;
        console.log("[Voice] Text displayed in input field");
        this.setStatus("Voice command ready");
      }

      // Auto-send the transcribed text
      console.log("[Voice] Sending transcribed text to chat...");
      await this._sendChat(cleanText);

      // Clear the input field after sending
      if (this.chatInput) {
        this.chatInput.value = "";
        console.log("[Voice] Input field cleared");
      }
    } catch (err) {
      console.error("[Voice] STT failed:", err);
      this.setStatus("Voice to text failed: " + err.message);
      this._setAvatarMode("idle", { source: "stt-exception" });
    }
  }

  _setVoiceListeningUI(active) {
    if (!this.voiceToggleBtn) return;
    if (active) {
      this.voiceToggleBtn.textContent = "🎤 LISTENING... (6s silence to stop)";
      this.voiceToggleBtn.classList.add("listening");
    } else {
      this.voiceToggleBtn.classList.remove("listening");
      this._updateVoiceToggleUI();
    }
  }

  _blobToBase64(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onloadend = () => {
        const res = reader.result;
        if (typeof res === "string") {
          const commaIdx = res.indexOf(",");
          resolve(commaIdx >= 0 ? res.slice(commaIdx + 1) : "");
        } else {
          resolve("");
        }
      };
      reader.onerror = () => reject(new Error("Failed to read audio blob"));
      reader.readAsDataURL(blob);
    });
  }

  async _handleSpecialVoiceCommand(text, normalized) {
    console.log("[Voice] Checking for special commands in:", normalized);

    // Voice command definitions with variations
    const VOICE_COMMANDS = {
      tts_enable: {
        phrases: [
          // Basic unmute
          "unmute", "un mute", "unmute yourself", "unmute voice",
          // Turn on variations
          "turn on voice", "turn voice on", "turn on the voice", "turn on speech",
          "turn on audio", "turn audio on", "turn on sound", "turn sound on",
          "turn on tts", "turn tts on", "turn on text to speech",
          // Enable variations
          "enable voice", "enable speech", "enable audio", "enable sound",
          "enable tts", "enable text to speech", "enable speaking",
          // Start variations
          "start speaking", "start talking", "start voice", "start audio",
          // Speak/talk variations
          "speak to me", "talk to me", "speak out loud", "speak up",
          "i want to hear you", "let me hear you", "use your voice",
          "use voice", "use speech", "use audio",
          // On variations
          "voice on", "audio on", "sound on", "speech on", "tts on",
          // Conversational
          "you can speak", "you can talk", "please speak", "please talk",
          "i want you to speak", "i want you to talk", "say it out loud",
          "read it out loud", "read aloud", "speak aloud",
          // Activate
          "activate voice", "activate speech", "activate tts", "activate audio"
        ],
        action: async () => {
          console.log("[Voice] TTS enable command detected");
          this.tts.setEnabled(true);
          this._updateVoiceToggleUI();
          this.setStatus("Voice output enabled");
          this.appendMessage("assistant", "Voice output has been enabled. I'll speak my responses now.");
          await this.tts.speak("Voice output has been enabled. I'll speak my responses now.");
        }
      },
      tts_disable: {
        phrases: [
          // Basic mute
          "mute", "mute yourself", "mute voice", "mute audio", "mute sound",
          // Turn off variations
          "turn off voice", "turn voice off", "turn off the voice", "turn off speech",
          "turn off audio", "turn audio off", "turn off sound", "turn sound off",
          "turn off tts", "turn tts off", "turn off text to speech",
          // Disable variations
          "disable voice", "disable speech", "disable audio", "disable sound",
          "disable tts", "disable text to speech", "disable speaking",
          // Stop variations
          "stop speaking", "stop talking", "stop voice", "stop audio",
          "stop the voice", "stop the audio", "stop reading",
          // Quiet variations
          "be quiet", "quiet", "silence", "hush", "shush", "shhh",
          // Off variations
          "voice off", "audio off", "sound off", "speech off", "tts off",
          // Don't speak variations
          "dont speak", "don't speak", "dont talk", "don't talk",
          "no voice", "no speech", "no audio", "no sound", "no talking",
          // Conversational
          "shut up", "be silent", "stay quiet", "stay silent",
          "stop it", "enough", "that's enough",
          "you dont need to speak", "you don't need to speak",
          "dont read it", "don't read it", "no need to speak",
          "just text", "text only", "type only", "just type",
          // Deactivate
          "deactivate voice", "deactivate speech", "deactivate tts", "deactivate audio"
        ],
        action: async () => {
          console.log("[Voice] TTS disable command detected");
          this.tts.setEnabled(false);
          this._updateVoiceToggleUI();
          this.setStatus("Voice output disabled");
          this.appendMessage("assistant", "Voice output has been disabled. I'll only respond with text now.");
        }
      }
    };

    // Check for TTS commands with fuzzy matching (90% similarity threshold)
    const ttsMatch = this._matchVoiceCommand(normalized, VOICE_COMMANDS);
    if (ttsMatch) {
      await ttsMatch.action();
      return true;
    }

    // Project Management Commands
    if (normalized.includes("open project") || normalized.includes("load project")) {
      console.log("[Voice] Open project command detected");
      // Extract project name if mentioned
      const projectMatch = text.match(/(?:open|load) project (?:called |named )?(.+)/i);
      if (projectMatch) {
        const projectName = projectMatch[1].trim();
        await this._voiceOpenProject(projectName);
      } else {
        await this._voiceShowProjects();
      }
      return true;
    }

    if (normalized.includes("create project") || normalized.includes("new project")) {
      console.log("[Voice] Create project command detected");
      const projectMatch = text.match(/(?:create|new) project (?:called |named )?(.+)/i);
      if (projectMatch) {
        const projectName = projectMatch[1].trim();
        await this._voiceCreateProject(projectName);
      } else {
        this.appendMessage("assistant", "Please specify a project name. Say 'create project called [name]'");
        if (this.tts.isEnabled()) await this.tts.speak("Please specify a project name.");
      }
      return true;
    }

    if (normalized.includes("list projects") || normalized.includes("show projects") ||
        normalized.includes("my projects") || normalized.includes("all projects") ||
        normalized.includes("what projects")) {
      console.log("[Voice] List projects command detected");
      await this._voiceShowProjects();
      return true;
    }

    // Rename Project - "rename project X to Y" or "rename project to Y" (for current)
    if (normalized.includes("rename project") || normalized.includes("change project name")) {
      console.log("[Voice] Rename project command detected");
      // Pattern: rename project [old name] to [new name]
      const renameMatch = text.match(/rename project (?:(.+?) )?to (.+)/i) ||
                          text.match(/change project name (?:(?:from )?(.+?) )?to (.+)/i);
      if (renameMatch) {
        const oldName = renameMatch[1]?.trim() || null; // null means current project
        const newName = renameMatch[2].trim();
        await this._voiceRenameProject(oldName, newName);
      } else {
        this.appendMessage("assistant", "Please specify the new name. Say 'rename project to [new name]' or 'rename project [old name] to [new name]'");
        if (this.tts.isEnabled()) await this.tts.speak("Please specify the new name for the project.");
      }
      return true;
    }

    // Delete Project - "delete project X"
    if (normalized.includes("delete project") || normalized.includes("remove project")) {
      console.log("[Voice] Delete project command detected");
      const deleteMatch = text.match(/(?:delete|remove) project (?:called |named )?(.+)/i);
      if (deleteMatch) {
        const projectName = deleteMatch[1].trim();
        await this._voiceDeleteProject(projectName);
      } else {
        this.appendMessage("assistant", "Please specify which project to delete. Say 'delete project [name]'");
        if (this.tts.isEnabled()) await this.tts.speak("Please specify which project to delete.");
      }
      return true;
    }

    // Close Project - "close project"
    if (normalized.includes("close project") || normalized.includes("exit project")) {
      console.log("[Voice] Close project command detected");
      await this._voiceCloseProject();
      return true;
    }

    // Switch/Select Project - "switch to project X" or "select project X"
    if (normalized.includes("switch to project") || normalized.includes("select project") ||
        normalized.includes("go to project")) {
      console.log("[Voice] Switch project command detected");
      const switchMatch = text.match(/(?:switch to|select|go to) project (?:called |named )?(.+)/i);
      if (switchMatch) {
        const projectName = switchMatch[1].trim();
        await this._voiceOpenProject(projectName);
      } else {
        await this._voiceShowProjects();
      }
      return true;
    }

    // Project Info - "show project info" or "project details"
    if (normalized.includes("project info") || normalized.includes("project details") ||
        normalized.includes("current project")) {
      console.log("[Voice] Project info command detected");
      await this._voiceProjectInfo();
      return true;
    }

    // File Operations
    if (normalized.includes("create file") || normalized.includes("new file") ||
        normalized.includes("add file")) {
      console.log("[Voice] Create file command detected");
      const fileMatch = text.match(/(?:create|new|add) file (?:called |named )?(.+)/i);
      if (fileMatch) {
        const fileName = fileMatch[1].trim();
        await this._voiceCreateFile(fileName);
      } else {
        this.appendMessage("assistant", "Please specify a file name. Say 'create file called [name]'");
        if (this.tts.isEnabled()) await this.tts.speak("Please specify a file name.");
      }
      return true;
    }

    // Delete File - "delete file X"
    if (normalized.includes("delete file") || normalized.includes("remove file")) {
      console.log("[Voice] Delete file command detected");
      const fileMatch = text.match(/(?:delete|remove) file (?:called |named )?(.+)/i);
      if (fileMatch) {
        const fileName = fileMatch[1].trim();
        await this._voiceDeleteFile(fileName);
      } else {
        this.appendMessage("assistant", "Please specify which file to delete. Say 'delete file [name]'");
        if (this.tts.isEnabled()) await this.tts.speak("Please specify which file to delete.");
      }
      return true;
    }

    // Rename File - "rename file X to Y"
    if (normalized.includes("rename file") || normalized.includes("change file name")) {
      console.log("[Voice] Rename file command detected");
      const renameMatch = text.match(/rename file (?:(.+?) )?to (.+)/i) ||
                          text.match(/change file name (?:(?:from )?(.+?) )?to (.+)/i);
      if (renameMatch) {
        const oldName = renameMatch[1]?.trim() || null;
        const newName = renameMatch[2].trim();
        await this._voiceRenameFile(oldName, newName);
      } else {
        this.appendMessage("assistant", "Please specify file names. Say 'rename file [old name] to [new name]'");
        if (this.tts.isEnabled()) await this.tts.speak("Please specify the file names.");
      }
      return true;
    }

    // List Files - "show files" or "list files"
    if (normalized.includes("list files") || normalized.includes("show files") ||
        normalized.includes("my files") || normalized.includes("project files")) {
      console.log("[Voice] List files command detected");
      await this._voiceListFiles();
      return true;
    }

    // =========================================================================
    // Git Commands
    // =========================================================================

    // Git Status - "git status", "show status", "check status"
    if (normalized.includes("git status") || normalized.includes("show status") ||
        normalized.includes("check status") || normalized.includes("what changed")) {
      console.log("[Voice] Git status command detected");
      await this._voiceGitStatus();
      return true;
    }

    // Git Commit - "commit changes", "git commit", "save changes"
    if (normalized.includes("commit") || normalized.includes("save changes")) {
      console.log("[Voice] Git commit command detected");
      const msgMatch = text.match(/commit (?:with message |message )?["']?(.+?)["']?$/i) ||
                       text.match(/save changes (?:as |with )?["']?(.+?)["']?$/i);
      const message = msgMatch ? msgMatch[1].trim() : null;
      await this._voiceGitCommit(message);
      return true;
    }

    // Git Push - "push changes", "git push", "push to remote"
    if (normalized.includes("push") && (normalized.includes("git") || normalized.includes("changes") ||
        normalized.includes("remote") || normalized.includes("origin"))) {
      console.log("[Voice] Git push command detected");
      await this._voiceGitPush();
      return true;
    }

    // Git Pull - "pull changes", "git pull", "pull from remote"
    if (normalized.includes("pull") && (normalized.includes("git") || normalized.includes("changes") ||
        normalized.includes("remote") || normalized.includes("origin"))) {
      console.log("[Voice] Git pull command detected");
      await this._voiceGitPull();
      return true;
    }

    // Git Diff - "show diff", "git diff", "what's different"
    if (normalized.includes("diff") || normalized.includes("what's different") ||
        normalized.includes("show changes")) {
      console.log("[Voice] Git diff command detected");
      await this._voiceGitDiff();
      return true;
    }

    // Git Branches - "list branches", "show branches", "what branch"
    if (normalized.includes("branch") || normalized.includes("branches")) {
      if (normalized.includes("list") || normalized.includes("show") || normalized.includes("what")) {
        console.log("[Voice] Git branches command detected");
        await this._voiceGitBranches();
        return true;
      }
      // Switch branch - "switch to branch X", "checkout branch X"
      if (normalized.includes("switch") || normalized.includes("checkout")) {
        const branchMatch = text.match(/(?:switch to|checkout) (?:branch )?(.+)/i);
        if (branchMatch) {
          await this._voiceGitCheckout(branchMatch[1].trim());
          return true;
        }
      }
      // Create branch - "create branch X", "new branch X"
      if (normalized.includes("create") || normalized.includes("new")) {
        const branchMatch = text.match(/(?:create|new) branch (?:called |named )?(.+)/i);
        if (branchMatch) {
          await this._voiceGitCheckout(branchMatch[1].trim(), true);
          return true;
        }
      }
    }

    // Git Stash - "stash changes", "git stash"
    if (normalized.includes("stash")) {
      if (normalized.includes("pop") || normalized.includes("restore") || normalized.includes("apply")) {
        await this._voiceGitStash("pop");
      } else if (normalized.includes("list")) {
        await this._voiceGitStash("list");
      } else {
        await this._voiceGitStash("push");
      }
      return true;
    }

    // Time Command
    if (normalized.includes("what time") || normalized.includes("whats the time") ||
        normalized.includes("tell me the time") || normalized.includes("current time")) {
      console.log("[Voice] Time command detected");
      await this._voiceGetTime();
      return true;
    }

    // Screenshot Command
    if (normalized.includes("take screenshot") || normalized.includes("take a screenshot") ||
        normalized.includes("capture screen") || normalized.includes("screenshot")) {
      console.log("[Voice] Screenshot command detected");
      await this._voiceTakeScreenshot();
      return true;
    }

    // New Chat Command
    if (normalized.includes("new chat") || normalized.includes("start new chat") ||
        normalized.includes("new conversation")) {
      console.log("[Voice] New chat command detected");
      await this.handleNewChatClick(true);
      this.appendMessage("assistant", "Started a new conversation.");
      if (this.tts.isEnabled()) await this.tts.speak("Started a new conversation.");
      return true;
    }

    // Confirmation Commands (for delete operations)
    if ((normalized.includes("yes delete") || normalized.includes("confirm delete") ||
         normalized.includes("yes remove") || normalized === "yes" || normalized === "confirm")) {
      console.log("[Voice] Confirmation command detected");
      if (this._pendingProjectDelete) {
        const handled = await this._voiceConfirmDeleteProject();
        if (handled) return true;
      }
      // If no pending delete, let it pass through
    }

    // Cancel Commands
    if (normalized.includes("cancel") || normalized.includes("nevermind") ||
        normalized.includes("no") || normalized.includes("stop")) {
      if (this._pendingProjectDelete) {
        this._pendingProjectDelete = null;
        this.appendMessage("assistant", "Deletion cancelled.");
        if (this.tts.isEnabled()) await this.tts.speak("Deletion cancelled.");
        return true;
      }
    }

    // Help Command - List available voice commands
    if (normalized.includes("voice commands") || normalized.includes("what can you do") ||
        normalized.includes("help commands") || normalized.includes("list commands")) {
      console.log("[Voice] Help command detected");
      await this._voiceShowHelp();
      return true;
    }

    return false; // No special command matched
  }

  async _voiceShowHelp() {
    const helpText = `**Available Voice Commands:**

**TTS Control:**
- "mute" / "unmute" - Toggle voice output
- "enable voice" / "disable voice"

**Project Management:**
- "create project called [name]"
- "rename project [old] to [new]"
- "delete project [name]"
- "open project [name]" / "switch to project [name]"
- "close project"
- "list projects" / "show projects"
- "project info"

**File Management:**
- "create file called [name]"
- "rename file [old] to [new]"
- "delete file [name]"
- "list files" / "show files"

**General:**
- "new chat" - Start a new conversation
- "what time is it"
- "take screenshot"
`;
    this.appendMessage("assistant", helpText);
    if (this.tts.isEnabled()) {
      await this.tts.speak("I can help with TTS control, project management, file management, and more. Check the chat for the full list.");
    }
  }

  /**
   * Match voice input against command phrases with fuzzy matching
   * @param {string} input - Normalized input text
   * @param {Object} commands - Object with command definitions
   * @returns {Object|null} - Matched command or null
   */
  _matchVoiceCommand(input, commands) {
    const words = input.split(/\s+/);

    for (const [cmdName, cmdDef] of Object.entries(commands)) {
      for (const phrase of cmdDef.phrases) {
        // Exact match
        if (input.includes(phrase)) {
          console.log(`[Voice] Exact match: "${phrase}" in "${input}"`);
          return cmdDef;
        }

        // Fuzzy match - check if 90% of phrase words are in input
        const phraseWords = phrase.split(/\s+/);
        if (phraseWords.length > 1) {
          const matchedWords = phraseWords.filter(pw =>
            words.some(w => w.includes(pw) || pw.includes(w))
          );
          const matchRatio = matchedWords.length / phraseWords.length;
          if (matchRatio >= 0.9) {
            console.log(`[Voice] Fuzzy match (${Math.round(matchRatio * 100)}%): "${phrase}" ~ "${input}"`);
            return cmdDef;
          }
        }

        // Single word similarity check (for single-word commands like "mute")
        if (phraseWords.length === 1) {
          for (const word of words) {
            const similarity = this._stringSimilarity(word, phrase);
            if (similarity >= 0.85) {
              console.log(`[Voice] Similar match (${Math.round(similarity * 100)}%): "${phrase}" ~ "${word}"`);
              return cmdDef;
            }
          }
        }
      }
    }
    return null;
  }

  /**
   * Calculate string similarity (Levenshtein-based)
   */
  _stringSimilarity(s1, s2) {
    const longer = s1.length > s2.length ? s1 : s2;
    const shorter = s1.length > s2.length ? s2 : s1;
    if (longer.length === 0) return 1.0;

    const editDistance = (a, b) => {
      const matrix = Array(b.length + 1).fill(null).map(() => Array(a.length + 1).fill(null));
      for (let i = 0; i <= a.length; i++) matrix[0][i] = i;
      for (let j = 0; j <= b.length; j++) matrix[j][0] = j;
      for (let j = 1; j <= b.length; j++) {
        for (let i = 1; i <= a.length; i++) {
          const cost = a[i - 1] === b[j - 1] ? 0 : 1;
          matrix[j][i] = Math.min(
            matrix[j][i - 1] + 1,
            matrix[j - 1][i] + 1,
            matrix[j - 1][i - 1] + cost
          );
        }
      }
      return matrix[b.length][a.length];
    };

    return (longer.length - editDistance(longer, shorter)) / longer.length;
  }

  async _voiceOpenProject(projectName) {
    try {
      const projects = await this.backend.listProjects();
      const project = projects.find(p =>
        p.name.toLowerCase().includes(projectName.toLowerCase())
      );

      if (project) {
        this.appendMessage("assistant", `Opening project: ${project.name}`);
        if (this.tts.isEnabled()) await this.tts.speak(`Opening project ${project.name}`);
        // Open the project in the modal
        if (this.projectModal) {
          this.projectModal.open("manage", project.id);
        }
      } else {
        this.appendMessage("assistant", `Project '${projectName}' not found. Say 'list projects' to see available projects.`);
        if (this.tts.isEnabled()) await this.tts.speak(`Project ${projectName} not found.`);
      }
    } catch (err) {
      console.error("[Voice] Failed to open project:", err);
      this.appendMessage("assistant", "Failed to open project.");
    }
  }

  async _voiceShowProjects() {
    try {
      const projects = await this.backend.listProjects();
      if (projects.length === 0) {
        this.appendMessage("assistant", "No projects found. Say 'create project called [name]' to create one.");
        if (this.tts.isEnabled()) await this.tts.speak("No projects found.");
      } else {
        const projectList = projects.map(p => p.name).join(", ");
        this.appendMessage("assistant", `Available projects: ${projectList}`);
        if (this.tts.isEnabled()) await this.tts.speak(`You have ${projects.length} projects.`);
      }
    } catch (err) {
      console.error("[Voice] Failed to list projects:", err);
      this.appendMessage("assistant", "Failed to list projects.");
    }
  }

  async _voiceCreateProject(projectName) {
    try {
      const result = await this.backend.createProject(projectName, "Created via voice command");
      this.appendMessage("assistant", `Project '${projectName}' has been created successfully.`);
      if (this.tts.isEnabled()) await this.tts.speak(`Project ${projectName} has been created.`);
      // Refresh the projects list
      await this.refreshProjects();
    } catch (err) {
      console.error("[Voice] Failed to create project:", err);
      this.appendMessage("assistant", `Failed to create project '${projectName}'.`);
      if (this.tts.isEnabled()) await this.tts.speak("Failed to create project.");
    }
  }

  async _voiceRenameProject(oldName, newName) {
    try {
      const projects = await this.backend.listProjects();
      let project = null;

      if (oldName) {
        // Find project by name
        project = projects.find(p =>
          p.name.toLowerCase().includes(oldName.toLowerCase())
        );
      } else if (this.projectModal?.currentProjectId) {
        // Use current project if no name specified
        project = projects.find(p => p.id === this.projectModal.currentProjectId);
      }

      if (!project) {
        this.appendMessage("assistant", `Project '${oldName || "current"}' not found. Say 'list projects' to see available projects.`);
        if (this.tts.isEnabled()) await this.tts.speak("Project not found.");
        return;
      }

      await this.backend.renameProject(project.id, newName);
      this.appendMessage("assistant", `Project '${project.name}' has been renamed to '${newName}'.`);
      if (this.tts.isEnabled()) await this.tts.speak(`Project renamed to ${newName}.`);
      // Refresh projects list
      await this.refreshProjects();
    } catch (err) {
      console.error("[Voice] Failed to rename project:", err);
      this.appendMessage("assistant", "Failed to rename project.");
      if (this.tts.isEnabled()) await this.tts.speak("Failed to rename project.");
    }
  }

  async _voiceDeleteProject(projectName) {
    try {
      const projects = await this.backend.listProjects();
      const project = projects.find(p =>
        p.name.toLowerCase().includes(projectName.toLowerCase())
      );

      if (!project) {
        this.appendMessage("assistant", `Project '${projectName}' not found. Say 'list projects' to see available projects.`);
        if (this.tts.isEnabled()) await this.tts.speak("Project not found.");
        return;
      }

      // Confirm deletion
      this.appendMessage("assistant", `Are you sure you want to delete project '${project.name}'? This cannot be undone. Say 'yes delete' to confirm.`);
      if (this.tts.isEnabled()) await this.tts.speak(`Are you sure you want to delete project ${project.name}? Say yes delete to confirm.`);

      // Store pending deletion for confirmation
      this._pendingProjectDelete = project;
    } catch (err) {
      console.error("[Voice] Failed to find project for deletion:", err);
      this.appendMessage("assistant", "Failed to find project.");
    }
  }

  async _voiceConfirmDeleteProject() {
    if (!this._pendingProjectDelete) {
      return false;
    }

    try {
      const project = this._pendingProjectDelete;
      await this.backend.deleteProject(project.id);
      this.appendMessage("assistant", `Project '${project.name}' has been deleted.`);
      if (this.tts.isEnabled()) await this.tts.speak(`Project ${project.name} has been deleted.`);
      this._pendingProjectDelete = null;
      // Refresh projects list
      await this.refreshProjects();
      return true;
    } catch (err) {
      console.error("[Voice] Failed to delete project:", err);
      this.appendMessage("assistant", "Failed to delete project.");
      this._pendingProjectDelete = null;
      return true;
    }
  }

  async _voiceCloseProject() {
    if (this.projectModal && this.projectModal.modal.classList.contains("show")) {
      this.projectModal.close();
      this.appendMessage("assistant", "Project closed.");
      if (this.tts.isEnabled()) await this.tts.speak("Project closed.");
    } else {
      this.appendMessage("assistant", "No project is currently open.");
      if (this.tts.isEnabled()) await this.tts.speak("No project is currently open.");
    }
  }

  async _voiceProjectInfo() {
    try {
      if (this.projectModal?.currentProjectId) {
        const projectData = await this.backend.getProject(this.projectModal.currentProjectId);
        if (projectData.ok && projectData.project) {
          const p = projectData.project;
          const files = await this.backend.getProjectFiles(this.projectModal.currentProjectId);
          const fileCount = files?.length || 0;

          const info = `Current project: ${p.name}. ${fileCount} files. ${p.description || 'No description.'}`;
          this.appendMessage("assistant", info);
          if (this.tts.isEnabled()) await this.tts.speak(`Current project is ${p.name} with ${fileCount} files.`);
          return;
        }
      }

      // No current project - show all projects
      await this._voiceShowProjects();
    } catch (err) {
      console.error("[Voice] Failed to get project info:", err);
      this.appendMessage("assistant", "Failed to get project information.");
    }
  }

  async _voiceListFiles() {
    try {
      if (!this.projectModal?.currentProjectId) {
        this.appendMessage("assistant", "No project is currently open. Say 'open project [name]' first.");
        if (this.tts.isEnabled()) await this.tts.speak("No project is currently open.");
        return;
      }

      const files = await this.backend.getProjectFiles(this.projectModal.currentProjectId);
      if (!files || files.length === 0) {
        this.appendMessage("assistant", "This project has no files yet. Say 'create file [name]' to add one.");
        if (this.tts.isEnabled()) await this.tts.speak("This project has no files.");
      } else {
        const fileList = files.map(f => f.file_name).join(", ");
        this.appendMessage("assistant", `Project files: ${fileList}`);
        if (this.tts.isEnabled()) await this.tts.speak(`You have ${files.length} files in this project.`);
      }
    } catch (err) {
      console.error("[Voice] Failed to list files:", err);
      this.appendMessage("assistant", "Failed to list files.");
    }
  }

  async _voiceDeleteFile(fileName) {
    try {
      if (!this.projectModal?.currentProjectId) {
        this.appendMessage("assistant", "No project is currently open. Say 'open project [name]' first.");
        if (this.tts.isEnabled()) await this.tts.speak("No project is currently open.");
        return;
      }

      const files = await this.backend.getProjectFiles(this.projectModal.currentProjectId);
      const file = files.find(f =>
        f.file_name.toLowerCase().includes(fileName.toLowerCase())
      );

      if (!file) {
        this.appendMessage("assistant", `File '${fileName}' not found. Say 'list files' to see available files.`);
        if (this.tts.isEnabled()) await this.tts.speak("File not found.");
        return;
      }

      await this.backend.deleteFile(this.projectModal.currentProjectId, file.id);
      this.appendMessage("assistant", `File '${file.file_name}' has been deleted.`);
      if (this.tts.isEnabled()) await this.tts.speak(`File ${file.file_name} has been deleted.`);
    } catch (err) {
      console.error("[Voice] Failed to delete file:", err);
      this.appendMessage("assistant", "Failed to delete file.");
    }
  }

  async _voiceRenameFile(oldName, newName) {
    try {
      if (!this.projectModal?.currentProjectId) {
        this.appendMessage("assistant", "No project is currently open. Say 'open project [name]' first.");
        if (this.tts.isEnabled()) await this.tts.speak("No project is currently open.");
        return;
      }

      const files = await this.backend.getProjectFiles(this.projectModal.currentProjectId);
      const file = files.find(f =>
        f.file_name.toLowerCase().includes(oldName?.toLowerCase() || "")
      );

      if (!file) {
        this.appendMessage("assistant", `File '${oldName}' not found. Say 'list files' to see available files.`);
        if (this.tts.isEnabled()) await this.tts.speak("File not found.");
        return;
      }

      await this.backend.renameFile(this.projectModal.currentProjectId, file.id, newName);
      this.appendMessage("assistant", `File '${file.file_name}' has been renamed to '${newName}'.`);
      if (this.tts.isEnabled()) await this.tts.speak(`File renamed to ${newName}.`);
    } catch (err) {
      console.error("[Voice] Failed to rename file:", err);
      this.appendMessage("assistant", "Failed to rename file.");
    }
  }

  // =========================================================================
  // Git Voice Command Helpers
  // =========================================================================

  async _voiceGitStatus() {
    try {
      if (!this.projectModal?.currentProjectId) {
        this.appendMessage("assistant", "No project is currently open. Say 'open project [name]' first.");
        if (this.tts.isEnabled()) await this.tts.speak("No project is currently open.");
        return;
      }

      const status = await this.backend.getGitStatus(this.projectModal.currentProjectId);

      if (!status.ok) {
        this.appendMessage("assistant", `Git error: ${status.error}`);
        if (this.tts.isEnabled()) await this.tts.speak("This project is not a git repository.");
        return;
      }

      const modified = status.modified?.length || 0;
      const added = status.added?.length || 0;
      const deleted = status.deleted?.length || 0;
      const untracked = status.untracked?.length || 0;
      const total = modified + added + deleted + untracked;

      let message = `**Git Status** (Branch: ${status.branch})\n\n`;
      if (total === 0) {
        message += "Working tree clean - no changes to commit.";
      } else {
        if (modified > 0) message += `- ${modified} modified file(s)\n`;
        if (added > 0) message += `- ${added} added file(s)\n`;
        if (deleted > 0) message += `- ${deleted} deleted file(s)\n`;
        if (untracked > 0) message += `- ${untracked} untracked file(s)\n`;
      }

      this.appendMessage("assistant", message);
      if (this.tts.isEnabled()) {
        if (total === 0) {
          await this.tts.speak(`On branch ${status.branch}. Working tree clean.`);
        } else {
          await this.tts.speak(`On branch ${status.branch}. ${total} files changed.`);
        }
      }
    } catch (err) {
      console.error("[Voice] Failed to get git status:", err);
      this.appendMessage("assistant", "Failed to get git status.");
    }
  }

  async _voiceGitCommit(message = null) {
    try {
      if (!this.projectModal?.currentProjectId) {
        this.appendMessage("assistant", "No project is currently open. Say 'open project [name]' first.");
        if (this.tts.isEnabled()) await this.tts.speak("No project is currently open.");
        return;
      }

      // If no message provided, ask for one
      if (!message) {
        this.appendMessage("assistant", "Please provide a commit message. Say 'commit with message [your message]'");
        if (this.tts.isEnabled()) await this.tts.speak("Please provide a commit message.");
        return;
      }

      // First stage all changes
      const addResult = await this.backend.gitAdd(this.projectModal.currentProjectId);
      if (!addResult.ok) {
        this.appendMessage("assistant", `Failed to stage changes: ${addResult.error}`);
        return;
      }

      // Then commit
      const result = await this.backend.gitCommit(this.projectModal.currentProjectId, message);

      if (result.ok) {
        this.appendMessage("assistant", `Changes committed: "${message}"`);
        if (this.tts.isEnabled()) await this.tts.speak("Changes committed successfully.");
      } else {
        this.appendMessage("assistant", `Commit failed: ${result.error}`);
        if (this.tts.isEnabled()) await this.tts.speak(result.error || "Commit failed.");
      }
    } catch (err) {
      console.error("[Voice] Failed to commit:", err);
      this.appendMessage("assistant", "Failed to commit changes.");
    }
  }

  async _voiceGitPush() {
    try {
      if (!this.projectModal?.currentProjectId) {
        this.appendMessage("assistant", "No project is currently open. Say 'open project [name]' first.");
        if (this.tts.isEnabled()) await this.tts.speak("No project is currently open.");
        return;
      }

      this.appendMessage("assistant", "Pushing to remote...");
      const result = await this.backend.gitPush(this.projectModal.currentProjectId);

      if (result.ok) {
        this.appendMessage("assistant", `Push successful: ${result.message}`);
        if (this.tts.isEnabled()) await this.tts.speak("Push successful.");
      } else {
        this.appendMessage("assistant", `Push failed: ${result.error}`);
        if (this.tts.isEnabled()) await this.tts.speak("Push failed.");
      }
    } catch (err) {
      console.error("[Voice] Failed to push:", err);
      this.appendMessage("assistant", "Failed to push changes.");
    }
  }

  async _voiceGitPull() {
    try {
      if (!this.projectModal?.currentProjectId) {
        this.appendMessage("assistant", "No project is currently open. Say 'open project [name]' first.");
        if (this.tts.isEnabled()) await this.tts.speak("No project is currently open.");
        return;
      }

      this.appendMessage("assistant", "Pulling from remote...");
      const result = await this.backend.gitPull(this.projectModal.currentProjectId);

      if (result.ok) {
        this.appendMessage("assistant", `Pull successful: ${result.message}`);
        if (this.tts.isEnabled()) await this.tts.speak(result.message || "Pull successful.");
      } else {
        this.appendMessage("assistant", `Pull failed: ${result.error}`);
        if (this.tts.isEnabled()) await this.tts.speak("Pull failed.");
      }
    } catch (err) {
      console.error("[Voice] Failed to pull:", err);
      this.appendMessage("assistant", "Failed to pull changes.");
    }
  }

  async _voiceGitDiff() {
    try {
      if (!this.projectModal?.currentProjectId) {
        this.appendMessage("assistant", "No project is currently open. Say 'open project [name]' first.");
        if (this.tts.isEnabled()) await this.tts.speak("No project is currently open.");
        return;
      }

      const diff = await this.backend.getGitDiff(this.projectModal.currentProjectId);

      if (!diff || diff.trim() === "") {
        this.appendMessage("assistant", "No changes to show.");
        if (this.tts.isEnabled()) await this.tts.speak("No changes to show.");
      } else {
        // Truncate if too long
        const displayDiff = diff.length > 2000 ? diff.substring(0, 2000) + "\n...(truncated)" : diff;
        this.appendMessage("assistant", `**Git Diff:**\n\`\`\`diff\n${displayDiff}\n\`\`\``);
        if (this.tts.isEnabled()) await this.tts.speak("Showing git diff in the chat.");
      }
    } catch (err) {
      console.error("[Voice] Failed to get diff:", err);
      this.appendMessage("assistant", "Failed to get git diff.");
    }
  }

  async _voiceGitBranches() {
    try {
      if (!this.projectModal?.currentProjectId) {
        this.appendMessage("assistant", "No project is currently open. Say 'open project [name]' first.");
        if (this.tts.isEnabled()) await this.tts.speak("No project is currently open.");
        return;
      }

      const result = await this.backend.gitBranches(this.projectModal.currentProjectId);

      if (result.ok) {
        const branchList = result.branches.map(b => b === result.current ? `**${b}** (current)` : b).join("\n- ");
        this.appendMessage("assistant", `**Git Branches:**\n- ${branchList}`);
        if (this.tts.isEnabled()) {
          await this.tts.speak(`Currently on branch ${result.current}. ${result.branches.length} branches total.`);
        }
      } else {
        this.appendMessage("assistant", `Failed to list branches: ${result.error}`);
      }
    } catch (err) {
      console.error("[Voice] Failed to list branches:", err);
      this.appendMessage("assistant", "Failed to list branches.");
    }
  }

  async _voiceGitCheckout(branch, create = false) {
    try {
      if (!this.projectModal?.currentProjectId) {
        this.appendMessage("assistant", "No project is currently open. Say 'open project [name]' first.");
        if (this.tts.isEnabled()) await this.tts.speak("No project is currently open.");
        return;
      }

      const result = await this.backend.gitCheckout(this.projectModal.currentProjectId, branch, create);

      if (result.ok) {
        this.appendMessage("assistant", result.message);
        if (this.tts.isEnabled()) await this.tts.speak(result.message);
      } else {
        this.appendMessage("assistant", `Checkout failed: ${result.error}`);
        if (this.tts.isEnabled()) await this.tts.speak("Checkout failed.");
      }
    } catch (err) {
      console.error("[Voice] Failed to checkout:", err);
      this.appendMessage("assistant", "Failed to checkout branch.");
    }
  }

  async _voiceGitStash(action = "push") {
    try {
      if (!this.projectModal?.currentProjectId) {
        this.appendMessage("assistant", "No project is currently open. Say 'open project [name]' first.");
        if (this.tts.isEnabled()) await this.tts.speak("No project is currently open.");
        return;
      }

      const result = await this.backend.gitStash(this.projectModal.currentProjectId, action);

      if (result.ok) {
        if (action === "list") {
          const output = result.output || "No stashes found.";
          this.appendMessage("assistant", `**Stash List:**\n${output}`);
        } else {
          this.appendMessage("assistant", result.message);
        }
        if (this.tts.isEnabled()) await this.tts.speak(result.message || "Stash operation complete.");
      } else {
        this.appendMessage("assistant", `Stash failed: ${result.error}`);
        if (this.tts.isEnabled()) await this.tts.speak(result.error || "Stash failed.");
      }
    } catch (err) {
      console.error("[Voice] Failed to stash:", err);
      this.appendMessage("assistant", "Failed to stash changes.");
    }
  }

  async _voiceCreateFile(fileName) {
    this.appendMessage("assistant", `To create a file, I'll need to send that request to the chat with proper context. Let me forward your request.`);
    if (this.tts.isEnabled()) await this.tts.speak("I'll help you create that file.");
    // Return false to let it go to the AI
    return false;
  }

  async _voiceGetTime() {
    try {
      // Get user's timezone
      const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
      console.log("[Voice] Detected timezone:", timezone);

      // Call backend time API
      const timeData = await this.backend.getLocalTime(timezone);
      console.log("[Voice] Time data from backend:", timeData);

      if (timeData.ok) {
        const timeStr = timeData.full_datetime || timeData.current_time;
        const response = `It's currently ${timeStr}`;
        this.appendMessage("assistant", response);
        if (this.tts.isEnabled()) {
          await this.tts.speak(response);
        }
      } else {
        throw new Error("Failed to get time from backend");
      }
    } catch (err) {
      console.error("[Voice] Failed to get time:", err);
      const now = new Date();
      const timeStr = now.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
      const response = `It's ${timeStr}`;
      this.appendMessage("assistant", response);
      if (this.tts.isEnabled()) await this.tts.speak(response);
    }
  }

  async _voiceTakeScreenshot() {
    try {
      this.appendMessage("assistant", "Taking a screenshot...");
      if (this.tts.isEnabled()) await this.tts.speak("Taking a screenshot");

      // Use the existing screenshot functionality
      const captureBtn = document.getElementById("btn-capture");
      if (captureBtn) {
        captureBtn.click();
        this.appendMessage("assistant", "Screenshot captured successfully.");
        if (this.tts.isEnabled()) await this.tts.speak("Screenshot captured.");
      } else {
        throw new Error("Screenshot button not found");
      }
    } catch (err) {
      console.error("[Voice] Failed to take screenshot:", err);
      this.appendMessage("assistant", "Failed to take screenshot.");
      if (this.tts.isEnabled()) await this.tts.speak("Failed to take screenshot.");
    }
  }

  async _convertToWav(blob) {
    try {
      const audioContext = new (window.AudioContext || window.webkitAudioContext)();
      const arrayBuffer = await blob.arrayBuffer();
      const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);

      // Convert to WAV format
      const wavBuffer = this._audioBufferToWav(audioBuffer);
      const wavBlob = new Blob([wavBuffer], { type: "audio/wav" });

      audioContext.close();
      return wavBlob;
    } catch (err) {
      console.error("[Voice] Failed to convert to WAV:", err);
      // If conversion fails, return original blob
      return blob;
    }
  }

  _audioBufferToWav(audioBuffer) {
    const numChannels = audioBuffer.numberOfChannels;
    const sampleRate = audioBuffer.sampleRate;
    const format = 1; // PCM
    const bitDepth = 16;

    const bytesPerSample = bitDepth / 8;
    const blockAlign = numChannels * bytesPerSample;

    const data = [];
    for (let i = 0; i < audioBuffer.numberOfChannels; i++) {
      data.push(audioBuffer.getChannelData(i));
    }

    const interleaved = this._interleave(data);
    const dataLength = interleaved.length * bytesPerSample;
    const buffer = new ArrayBuffer(44 + dataLength);
    const view = new DataView(buffer);

    // RIFF chunk descriptor
    this._writeString(view, 0, "RIFF");
    view.setUint32(4, 36 + dataLength, true);
    this._writeString(view, 8, "WAVE");

    // FMT sub-chunk
    this._writeString(view, 12, "fmt ");
    view.setUint32(16, 16, true); // SubChunk1Size (16 for PCM)
    view.setUint16(20, format, true);
    view.setUint16(22, numChannels, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * blockAlign, true); // ByteRate
    view.setUint16(32, blockAlign, true);
    view.setUint16(34, bitDepth, true);

    // Data sub-chunk
    this._writeString(view, 36, "data");
    view.setUint32(40, dataLength, true);

    // Write PCM samples
    this._floatTo16BitPCM(view, 44, interleaved);

    return buffer;
  }

  _interleave(channelData) {
    const length = channelData[0].length;
    const result = new Float32Array(length * channelData.length);

    let offset = 0;
    for (let i = 0; i < length; i++) {
      for (let channel = 0; channel < channelData.length; channel++) {
        result[offset++] = channelData[channel][i];
      }
    }
    return result;
  }

  _writeString(view, offset, string) {
    for (let i = 0; i < string.length; i++) {
      view.setUint8(offset + i, string.charCodeAt(i));
    }
  }

  _floatTo16BitPCM(view, offset, input) {
    for (let i = 0; i < input.length; i++, offset += 2) {
      const s = Math.max(-1, Math.min(1, input[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
  }

  //---------------------------------------------------------------------------
  // Init
  //---------------------------------------------------------------------------

  async _initState() {
    console.log("[UI] _initState() starting...");
    this._updateVoiceToggleUI();
    await this._refreshLLMModeFromBackend();

    // Sync context info to set correct token budget based on LLM mode
    await this.syncContextInfo();

    // Try to restore last conversation from localStorage
    const lastConvId = localStorage.getItem("sarah_last_conversation_id");
    console.log("[UI] Last conversation ID from localStorage:", lastConvId);
    let restoredConversation = false;

    if (lastConvId) {
      try {
        const convId = parseInt(lastConvId, 10);
        console.log("[UI] Attempting to restore conversation:", convId);

        // Verify the conversation still exists
        const conversations = await this.backend.listConversations();
        console.log("[UI] Available conversations:", conversations?.length || 0, conversations?.map(c => c.id));
        const exists = conversations.some(c => c.id === convId);

        if (exists) {
          console.log("[UI] Conversation exists, setting as active...");
          await this.setActiveConversation(convId);
          restoredConversation = true;
          console.log("[UI] Successfully restored conversation:", convId);

          // Scroll to bottom after loading messages
          this._scrollToBottom();
        } else {
          // Conversation was deleted, clear the stored ID
          localStorage.removeItem("sarah_last_conversation_id");
          console.log("[UI] Last conversation no longer exists (ID:", convId, "), cleared storage");
        }
      } catch (err) {
        console.warn("[UI] Failed to restore last conversation:", err);
        localStorage.removeItem("sarah_last_conversation_id");
      }
    } else {
      console.log("[UI] No last conversation ID in localStorage");
    }

    // Show welcome message only if no conversation was restored
    if (!restoredConversation && this.chatLog && this.chatLog.children.length === 0) {
      console.log("[UI] No conversation restored, showing welcome message");
      this.appendMessage(
        "assistant",
        "Hey, Creator! How can I assist you today?"
      );
    }

    try {
      const health = await this.backend.health();
      this.setStatus(health?.ok ? "Backend OK" : "Backend issue");
    } catch {
      this.setStatus("Backend error");
    }
  }

  /**
   * Scroll chat log to bottom (with requestAnimationFrame for reliability)
   */
  _scrollToBottom() {
    if (this.chatLog) {
      // Use requestAnimationFrame to ensure scroll happens after DOM update
      requestAnimationFrame(() => {
        if (this.chatLog) {
          this.chatLog.scrollTop = this.chatLog.scrollHeight;
        }
      });
    }
  }

  async _refreshLLMModeFromBackend() {
    try {
      const data = await this.backend.getLLMMode();
      if (data?.mode) {
        this.llmMode = data.mode;
        this._mergeLLMStatus(data);
      }
    } catch {}
    this._updateLLMModeUI();
  }

  _updateLLMModeUI() {
    if (window.SARAH_MODEL_STATUS && this.llmStatus) {
      this._renderLLMStatus();
      return;
    }
    if (!this.llmModeBtn) return;
    this.llmModeBtn.textContent =
      this.llmMode === "online" ? "LLM: OPENROUTER AUTO" : "LLM: LOCAL";
  }

  async toggleLLMMode() {
    const newMode = this.llmMode === "online" ? "local" : "online";
    try {
      const data = await this.backend.setLLMMode(newMode);
      if (data?.mode) {
        this.llmMode = data.mode;
        this._mergeLLMStatus(data);
      }
      // Update token budget based on the selected provider.
      if (data?.token_budget) {
        this.updateTokenIndicator(0, data.token_budget);
      }
    } catch {
      this.llmMode = newMode;
      this._mergeLLMStatus({ mode: newMode });
    }
    this._updateLLMModeUI();

    // Sync context info to update token budget display
    this.syncContextInfo();

    // When switching to LOCAL mode, check Ollama health
    if (newMode === "local") {
      this.setStatus("Checking Ollama connection...");
      this._checkOllamaHealth();
    } else {
      this.setStatus("Switched to Online mode (OpenRouter auto)");
    }
  }

  async _checkOllamaHealth() {
    const statusDot = document.getElementById("vision-status-dot");
    const statusText = document.getElementById("vision-status-text");
    const statusMeta = document.getElementById("vision-status-meta");

    try {
      const response = await fetch(`${API_BASE}/api/vision/health`);
      if (!response.ok) throw new Error("Health check failed");

      const health = await response.json();
      console.log("[LLM Mode] Ollama health:", health);

      if (health.vision_ready && health.provider === "openrouter") {
        this.setStatus("Ollama not reachable - vision uses OpenRouter");
        if (statusDot) statusDot.className = "vision-status-dot status-ready";
        if (statusText) statusText.textContent = "Vision: Ready";
        if (statusMeta) statusMeta.textContent = `cloud · ${String(health.model || "").split("/").pop()}`;
      } else if (!health.ollama_reachable) {
        this.setStatus("Ollama not reachable - start Ollama first");
        if (statusDot) statusDot.className = "vision-status-dot status-offline";
        if (statusText) statusText.textContent = "Vision: Offline";
        if (statusMeta) statusMeta.textContent = "Run: ollama serve";
      } else if (health.vision_ready) {
        this.setStatus("Ollama ready - Local mode active");
        if (statusDot) statusDot.className = "vision-status-dot status-ready";
        if (statusText) statusText.textContent = "Vision: Ready";
        if (statusMeta) statusMeta.textContent = health.model || "qwen3-vl:8b";
      } else if (health.last_error) {
        // Warmup failed with error
        this.setStatus(`Ollama error: ${health.last_error}`);
        if (statusDot) statusDot.className = "vision-status-dot status-offline";
        if (statusText) statusText.textContent = "Vision: Error";
        if (statusMeta) statusMeta.textContent = health.last_error.substring(0, 30);
      } else {
        // Connected but model may not be available yet
        this.setStatus("Ollama connected - checking model...");
        if (statusDot) statusDot.className = "vision-status-dot status-checking";
        if (statusText) statusText.textContent = "Vision: Checking...";
        if (statusMeta) statusMeta.textContent = health.model || "checking model";

        // Retry health check after 2 seconds
        setTimeout(() => this._checkOllamaHealth(), 2000);
      }
    } catch (err) {
      console.warn("[LLM Mode] Ollama health check failed:", err);
      this.setStatus("Cannot reach Ollama - check if it's running");
      if (statusDot) statusDot.className = "vision-status-dot status-offline";
      if (statusText) statusText.textContent = "Vision: Offline";
      if (statusMeta) statusMeta.textContent = "Connection failed";
    }
  }

  //---------------------------------------------------------------------------
  // MORE Panel + Events
  //---------------------------------------------------------------------------

  _bindEvents() {
    console.log("[UI] _bindEvents() called - binding all event listeners");

    // Defer snippet binding to ensure DOM is ready
    console.log("[UI] Setting up setTimeout for snippet paste binding...");
    setTimeout(() => {
      console.log("[UI] setTimeout callback executing...");
      console.log("[UI] _bindSnippetPaste exists:", typeof this._bindSnippetPaste);
      this._bindSnippetPaste();
    }, 100);

    if (this.chatSend) {
      this.chatSend.addEventListener("click", () => this.sendChatFromInput());
    }

    // Note: chat-input keydown handler is registered below (capture-phase) with
    // Ctrl-shortcut passthrough. A second bubble-phase listener used to live here
    // and caused double-Enter sends; removed.

    // Snippet input controls
    console.log("[UI] Looking for snippet elements...");
    const snippetToggle = document.getElementById("snippet-toggle");
    const snippetSection = document.getElementById("snippet-section");
    const snippetClose = document.getElementById("snippet-close");
    const snippetAnalyze = document.getElementById("snippet-analyze");
    const snippetAttach = document.getElementById("snippet-attach");
    const snippetPaste = document.getElementById("snippet-paste");
    const snippetFileInput = document.getElementById("snippet-file-input");
    const snippetClear = document.getElementById("snippet-clear");

    console.log("[UI] Snippet elements found:");
    console.log("[UI]   - snippetToggle:", !!snippetToggle);
    console.log("[UI]   - snippetSection:", !!snippetSection);
    console.log("[UI]   - snippetPaste:", !!snippetPaste);

    if (snippetToggle) {
      snippetToggle.addEventListener("click", () => {
        snippetSection?.classList.toggle("hidden");
        if (!snippetSection?.classList.contains("hidden")) {
          document.getElementById("snippet-input")?.focus();
        }
      });
    }

    if (snippetClose) {
      snippetClose.addEventListener("click", () => {
        snippetSection?.classList.add("hidden");
      });
    }

    if (snippetAnalyze) {
      snippetAnalyze.addEventListener("click", () => this._handleSnippetAnalyze());
    }

    if (snippetAttach) {
      snippetAttach.addEventListener("click", () => this._handleSnippetAttach());
    }

    if (snippetPaste) {
      snippetPaste.addEventListener("click", async () => {
        console.log("[Snippet] Paste button clicked!");

        const inputEl = document.getElementById("snippet-input");
        const statusEl = document.getElementById("snippet-status");

        if (!inputEl) return;

        try {
          let clipboardText = "";

          // Method 1: Try Electron clipboard bridge
          if (window.sarahVision && window.sarahVision.readClipboard) {
            clipboardText = await window.sarahVision.readClipboard();
            console.log("[Snippet] Electron clipboard length:", clipboardText.length);
          }

          // Method 2: Try browser clipboard API as fallback
          if (!clipboardText || clipboardText.length === 0) {
            console.log("[Snippet] Trying browser clipboard API...");
            try {
              clipboardText = await navigator.clipboard.readText();
              console.log("[Snippet] Browser clipboard length:", clipboardText.length);
            } catch (browserErr) {
              console.log("[Snippet] Browser clipboard failed:", browserErr.message);
            }
          }

          // If we got text, paste it
          if (clipboardText && clipboardText.length > 0) {
            // Insert at cursor or replace all
            inputEl.value = clipboardText;
            inputEl.selectionStart = inputEl.selectionEnd = clipboardText.length;

            if (statusEl) statusEl.textContent = `✅ Pasted ${clipboardText.length} characters - Ready to analyze!`;
            console.log("[Snippet] Successfully pasted, new length:", inputEl.value.length);
          } else {
            // Give helpful message
            if (statusEl) statusEl.textContent = "⚠️ Clipboard empty - Use Ctrl+V or type directly into the text area";
            console.warn("[Snippet] Clipboard is empty!");
          }
        } catch (err) {
          console.error("[Snippet] Button paste failed:", err);
          if (statusEl) statusEl.textContent = `⚠️ Paste failed - Use Ctrl+V or type directly`;
        }
      });
    }

    if (snippetFileInput) {
      snippetFileInput.addEventListener("change", async (e) => {
        const file = e.target.files[0];
        if (!file) return;

        console.log("[Snippet] Loading file:", file.name);

        const inputEl = document.getElementById("snippet-input");
        const statusEl = document.getElementById("snippet-status");

        try {
          const text = await file.text();
          if (inputEl) {
            inputEl.value = text;
            if (statusEl) statusEl.textContent = `✅ Loaded ${file.name} (${text.length} chars)`;
          }
          console.log("[Snippet] File loaded successfully, length:", text.length);
        } catch (err) {
          console.error("[Snippet] File load failed:", err);
          if (statusEl) statusEl.textContent = `❌ Failed to load file: ${err.message}`;
        }

        // Reset file input so same file can be loaded again
        e.target.value = '';
      });
    }

    if (snippetClear) {
      snippetClear.addEventListener("click", () => {
        const input = document.getElementById("snippet-input");
        const status = document.getElementById("snippet-status");
        if (input) input.value = "";
        if (status) status.textContent = "";
        this.attachedSnippet = null;
      });
    }

    // Snippet paste binding is in _bindSnippetPaste() method (called via setTimeout at start of _bindEvents)

    // Improvement #8: the header model label opens the model picker.
    this.modelStatusTextEl?.addEventListener("click", () => {
      this._modelPicker ??= new SarahModelPicker({
        anchor: this.modelStatusTextEl,
        backend: this.backend,
        onChanged: (status) => {
          this._mergeLLMStatus(status);
          this.syncContextInfo(this.activeConversationId)
            .catch((err) => console.warn("[Token] Context sync failed:", err));
        },
      });
      this._modelPicker.toggle();
    });

    if (this.chatInput) {
      this.chatInput.removeAttribute("readonly");
      this.chatInput.removeAttribute("disabled");

      // Issue #26: install slash-command palette BEFORE the main chat-input
      // keydown handler so palette navigation (Arrow / Enter / Tab / Escape)
      // intercepts events while the popover is open.
      this._installSlashPalette();

      this.chatInput.addEventListener(
        "keydown",
        (e) => {
          // Issue #26: when the slash palette is handling the keystroke, skip
          // the default Enter→send / shortcut routing.
          if (this.slashPalette?.open) return;

          const k = (e.key || "").toLowerCase();
          const isShortcut = e.ctrlKey || e.metaKey;

          if (isShortcut && ["v", "c", "x", "a", "z", "y"].includes(k)) {
            e.stopPropagation();
            return;
          }

          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            e.stopPropagation();
            this.sendChatFromInput();
          }
        },
        true
      );

      this.chatInput.addEventListener("paste", (e) => {
        this._handleChatPaste(e).catch((err) => console.warn("[Chat Paste] failed:", err));
      });
    }
    // Image upload button
    const imageUploadBtn = document.getElementById("image-upload-btn");
    const imageUploadInput = document.getElementById("image-upload-input");

    if (imageUploadBtn && imageUploadInput) {
      imageUploadBtn.addEventListener("click", () => {
        imageUploadInput.click();
      });

      imageUploadInput.addEventListener("change", async (e) => {
        const file = e.target.files?.[0];
        if (!file) return;

        // Validate file type
        if (!file.type.startsWith("image/")) {
          alert("Please select an image file");
          return;
        }

        const dataUrl = await this._fileToDataUrl(file);
        this._addPendingAttachment({
          kind: "image",
          name: file.name,
          type: file.type,
          size: file.size,
          dataUrl,
        });
        console.log(`[Image Upload] Queued: ${file.name}`);
        // Reset input so same file can be selected again
        e.target.value = "";
      });
    }

    if (this.newChatBtn) {
      this.newChatBtn.addEventListener("click", () =>
        this.handleNewChatClick()
      );
    }

    if (this.voiceToggleBtn) {
      this.voiceToggleBtn.addEventListener("click", () => {
        // If currently listening, stop the recording
        if (this.voiceListening) {
          console.log("[Voice] Manual stop button clicked");
          this._stopVoiceCapture();
        } else {
          this.toggleVoice();
        }
      });
    }

    if (this.llmModeBtn) {
      this.llmModeBtn.addEventListener("click", () => this.toggleLLMMode());
    }

    // MORE panel
    if (this.moreToggleBtn) {
      this.moreToggleBtn.addEventListener("click", () => {
        if (this.morePanel.classList.contains("open")) this.closeMorePanel();
        else this.openMorePanel("conversations");
      });
    }

    if (this.moreCloseBtn) {
      this.moreCloseBtn.addEventListener("click", () => this.closeMorePanel());
    }

    // Click outside to close more panel
    document.addEventListener("click", (e) => {
      if (!this.morePanel?.classList.contains("open")) return;

      // Check if click is outside the panel and not on panel-opening buttons
      const isInsidePanel = this.morePanel.contains(e.target);
      const isToggleBtn = this.moreToggleBtn?.contains(e.target);
      const isLibraryBtn = this.btnLibrary?.contains(e.target);
      const isProjectsBtn = this.btnProjects?.contains(e.target);

      // Don't close if click is on the panel or any of its trigger buttons
      if (!isInsidePanel && !isToggleBtn && !isLibraryBtn && !isProjectsBtn) {
        this.closeMorePanel();
      }
    });

    if (this.moreTabs.length > 0) {
      this.moreTabs.forEach((btn) => {
        btn.addEventListener("click", () => {
          const view = btn.getAttribute("data-view");
          this.switchMoreView(view);
        });
      });
    }

    if (this.moreNewConvBtn) {
      this.moreNewConvBtn.addEventListener("click", () =>
        this.handleNewChatClick(true)
      );
    }

    if (this.moreMemSearchForm) {
      this.moreMemSearchForm.addEventListener("submit", (ev) => {
        ev.preventDefault();
        this.refreshMemories();
      });
    }

    if (this.moreNewProjectBtn) {
      this.moreNewProjectBtn.addEventListener("click", () => {
        this.projectModal.open("create");
      });
    }

    if (this.moreDiscoverSkillsBtn) {
      this.moreDiscoverSkillsBtn.addEventListener("click", () =>
        this.refreshSkills({ discover: true })
      );
    }

    if (this.moreRefreshSkillsBtn) {
      this.moreRefreshSkillsBtn.addEventListener("click", () =>
        this.refreshSkills()
      );
    }

    // Connect Library button to open conversations
    if (this.btnLibrary) {
      this.btnLibrary.addEventListener("click", (e) => {
        e.stopPropagation(); // Prevent click-outside handler from firing
        console.log("[UI] Library button clicked");
        this.toggleMorePanelView("conversations");
      });
    } else {
      console.warn("[UI] btnLibrary element not found!");
    }

    // Connect Projects button
    if (this.btnProjects) {
      this.btnProjects.addEventListener("click", (e) => {
        e.stopPropagation(); // Prevent click-outside handler from firing
        console.log("[UI] Projects button clicked");
        this.toggleMorePanelView("projects");
      });
    } else {
      console.warn("[UI] btnProjects element not found!");
    }

    // Connect Screen Capture button
    if (this.btnScreen) {
      this.btnScreen.addEventListener("click", () => {
        this.toggleScreenTab();
      });
    }

    if (this.btnDiagnostics) {
      this.btnDiagnostics.addEventListener("click", () => {
        this.toggleDiagnosticsDashboard();
      });
    }

    if (this.diagnosticsCloseBtn) {
      this.diagnosticsCloseBtn.addEventListener("click", () => {
        this.closeDiagnosticsDashboard();
      });
    }

    this.diagnosticsDashboard?.addEventListener("click", (ev) => {
      if (ev.target?.classList?.contains("diagnostics-backdrop")) {
        this.closeDiagnosticsDashboard();
      }
    });

    window.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && !this.diagnosticsDashboard?.classList.contains("hidden")) {
        this.closeDiagnosticsDashboard();
      }
    });

    // Mood Panel Controls - Make emotion panel interactive
    this._bindMoodControls();
  }

  //---------------------------------------------------------------------------
  // Mood Panel Controls
  //---------------------------------------------------------------------------

  _bindMoodControls() {
    const emotionEl = document.getElementById("emotion-name");
    const intensityEl = document.getElementById("emotion-intensity");
    const affinityEl = document.getElementById("emotion-affinity");

    // Available emotions for cycling
    const EMOTIONS = ["neutral", "happy", "excited", "confused", "angry", "sad", "frustrated"];

    // Make emotion clickable to cycle through emotions
    if (emotionEl) {
      emotionEl.classList.add("mood-clickable");
      emotionEl.title = "Click to change emotion";
      emotionEl.addEventListener("click", async () => {
        if (!this.activeConversationId) return;

        const currentEmotion = emotionEl.textContent.toLowerCase();
        const currentIndex = EMOTIONS.indexOf(currentEmotion);
        const nextEmotion = EMOTIONS[(currentIndex + 1) % EMOTIONS.length];

        try {
          const result = await this.backend.updateMood(this.activeConversationId, {
            emotion: nextEmotion
          });

          if (result.ok) {
            this._updateMoodDisplay(result.mood, result.avatar_params);
          }
        } catch (err) {
          console.warn("[Mood] Failed to update emotion:", err);
        }
      });
    }

    // Make intensity adjustable with mouse wheel
    if (intensityEl) {
      intensityEl.classList.add("mood-scrollable");
      intensityEl.title = "Scroll to adjust intensity (0.0-1.0)";
      intensityEl.addEventListener("wheel", async (ev) => {
        ev.preventDefault();
        if (!this.activeConversationId) return;

        const currentIntensity = parseFloat(intensityEl.textContent) || 0.2;
        const delta = ev.deltaY > 0 ? -0.1 : 0.1;
        const newIntensity = Math.max(0, Math.min(1, currentIntensity + delta));

        try {
          const result = await this.backend.updateMood(this.activeConversationId, {
            intensity: newIntensity
          });

          if (result.ok) {
            this._updateMoodDisplay(result.mood, result.avatar_params);
          }
        } catch (err) {
          console.warn("[Mood] Failed to update intensity:", err);
        }
      });
    }

    // Make affinity adjustable with mouse wheel
    if (affinityEl) {
      affinityEl.classList.add("mood-scrollable");
      affinityEl.title = "Scroll to adjust affinity (0.0-1.0)";
      affinityEl.addEventListener("wheel", async (ev) => {
        ev.preventDefault();
        if (!this.activeConversationId) return;

        const currentAffinity = parseFloat(affinityEl.textContent) || 0.9;
        const delta = ev.deltaY > 0 ? -0.1 : 0.1;
        const newAffinity = Math.max(0, Math.min(1, currentAffinity + delta));

        try {
          const result = await this.backend.updateMood(this.activeConversationId, {
            affinity: newAffinity
          });

          if (result.ok) {
            this._updateMoodDisplay(result.mood, result.avatar_params);
          }
        } catch (err) {
          console.warn("[Mood] Failed to update affinity:", err);
        }
      });
    }
  }

  _updateMoodDisplay(mood, avatarParams) {
    // Update UI elements
    const emotionEl = document.getElementById("emotion-name");
    const intensityEl = document.getElementById("emotion-intensity");
    const affinityEl = document.getElementById("emotion-affinity");

    if (emotionEl && mood.emotion) {
      emotionEl.textContent = mood.emotion;
    }
    if (intensityEl && typeof mood.intensity === "number") {
      intensityEl.textContent = mood.intensity.toFixed(2);
    }
    if (affinityEl && typeof mood.affinity === "number") {
      affinityEl.textContent = mood.affinity.toFixed(2);
    }

    // Update SARAH_EMOTION brain state (for Live2D avatar)
    if (window.SARAH_EMOTION) {
      // Map backend emotion to frontend emotion names
      const emotionMap = {
        "happy": "happy",
        "excited": "excited",
        "confused": "confused",
        "angry": "angry",
        "sad": "sad",
        "neutral": "neutral",
        "frustrated": "frustrated",
        "affectionate": "affectionate",
        "shy": "shy",
        "surprised": "surprised",
      };

      const frontendEmotion = emotionMap[mood.emotion] || "neutral";

      // Set emotion without triggering side effects (direct assignment)
      window.SARAH_EMOTION.emotion = frontendEmotion;
      window.SARAH_EMOTION.intensity = mood.intensity;
      // Convert affinity from 0-1 to the frontend's -100 to 100 scale
      window.SARAH_EMOTION.affinity = (mood.affinity - 0.5) * 200;

      // Trigger the Live2D update
      if (window.SARAH_LIVE2D?.applyMoodState) {
        window.SARAH_LIVE2D.applyMoodState(
          { ...mood, emotion: frontendEmotion },
          avatarParams
        );
      } else if (window.SARAH_LIVE2D?.setEmotion) {
        window.SARAH_LIVE2D.setEmotion(frontendEmotion, mood.intensity);
      }
    }

    window.SARAH_AVATAR_SYSTEM?.syncBackendMood?.(mood, avatarParams);

    const avatarMode = avatarParams?.mode || avatarParams?.state || avatarParams?.avatar_state;
    if (avatarMode) {
      this._setAvatarMode(avatarMode, { source: "backend-avatar-payload" });
    }
  }

  //---------------------------------------------------------------------------
  // Snippet Paste Binding (separate method to handle timing issues)
  //---------------------------------------------------------------------------

  _bindSnippetPaste() {
    const snippetInput = document.getElementById("snippet-input");
    const snippetStatus = document.getElementById("snippet-status");

    console.log("[Snippet] _bindSnippetPaste called");
    console.log("[Snippet] Found snippet-input:", !!snippetInput);
    console.log("[Snippet] Found snippet-status:", !!snippetStatus);

    if (!snippetInput) {
      console.error("[Snippet] ❌ snippet-input element NOT FOUND!");
      console.error("[Snippet] Retrying in 500ms...");
      setTimeout(() => this._bindSnippetPaste(), 500);
      return;
    }

    console.log("[Snippet] ✓ Element found, ID:", snippetInput.id);

    // Remove any restrictions
    snippetInput.removeAttribute("readonly");
    snippetInput.removeAttribute("disabled");
    snippetInput.readOnly = false;
    snippetInput.disabled = false;
    console.log("[Snippet] Editable attributes set");

    // Click detection
    snippetInput.addEventListener("click", () => {
      console.log("[Snippet] ✓ CLICK DETECTED");
    });

    // Focus detection
    snippetInput.addEventListener("focus", () => {
      console.log("[Snippet] ✓ FOCUS DETECTED");
    });

    // Input detection
    snippetInput.addEventListener("input", () => {
      console.log("[Snippet] ✓ INPUT DETECTED, value length:", snippetInput.value.length);
    });

    // Keydown with Ctrl+V detection
    snippetInput.addEventListener("keydown", (e) => {
      const k = (e.key || "").toLowerCase();
      const isShortcut = e.ctrlKey || e.metaKey;

      if (isShortcut && k === "v") {
        console.log("[Snippet] ✓ CTRL+V DETECTED");
      }

      // Allow paste shortcuts
      if (isShortcut && ["v", "c", "x", "a", "z", "y"].includes(k)) {
        e.stopPropagation();
        return; // Allow default behavior
      }
    }, true);

    // PASTE EVENT (highest priority) - supports both images and text
    snippetInput.addEventListener("paste", async (e) => {
      console.log("[Snippet] ========== PASTE EVENT DETECTED ==========");
      e.preventDefault();
      e.stopPropagation();

      const cd = e.clipboardData;
      const items = Array.from(cd?.items || []);
      const imgItem = items.find(i => i.kind === "file" && i.type.startsWith("image/"));

      // 1) Check for image paste first (Snipping Tool, screenshots, etc.)
      let imageData = null;

      // Method A: Try clipboardData.items (standard web API)
      if (imgItem) {
        console.log("[Snippet] Image detected via clipboardData.items");
        const file = imgItem.getAsFile();
        if (file) {
          try {
            const dataUrl = await new Promise((resolve, reject) => {
              const r = new FileReader();
              r.onload = () => resolve(r.result);
              r.onerror = reject;
              r.readAsDataURL(file);
            });
            imageData = { dataUrl, size: file.size };
            console.log("[Snippet] Image from clipboardData:", Math.round(file.size/1024), "KB");
          } catch (err) {
            console.warn("[Snippet] Failed to read image from clipboardData:", err);
          }
        }
      }

      // Method B: Try Electron IPC (for Snipping Tool, etc. that don't work with clipboardData)
      if (!imageData && window.sarahVision?.readClipboardImage) {
        console.log("[Snippet] Trying IPC readClipboardImage...");
        try {
          const ipcImage = await window.sarahVision.readClipboardImage();
          if (ipcImage && ipcImage.dataUrl) {
            imageData = ipcImage;
            console.log("[Snippet] Image from IPC:", Math.round(ipcImage.size/1024), "KB,", ipcImage.width, "x", ipcImage.height);
          }
        } catch (err) {
          console.warn("[Snippet] IPC readClipboardImage failed:", err);
        }
      }

      // If we got an image, store it for the next message
      if (imageData && imageData.dataUrl) {
        if (window.SARAH_UI) {
          window.SARAH_UI.pendingImageData = {
            dataUrl: imageData.dataUrl,
            fileName: `pasted-${Date.now()}.png`
          };
          console.log("[Snippet] Image attached to next message");
          if (snippetStatus) {
            snippetStatus.textContent = `✅ Image ready (${Math.round((imageData.size || 0)/1024)} KB) - send a message to analyze it!`;
          }
        }
        return;
      }

      // 2) No image found - try text paste via IPC
      console.log("[Snippet] No image found, trying text paste...");

      let clipText = "";

      // Method 1: Electron IPC
      if (window.sarahVision?.readClipboard) {
        try {
          clipText = await window.sarahVision.readClipboard();
          console.log("[Snippet] IPC clipboard length:", clipText?.length || 0);
        } catch (err) {
          console.warn("[Snippet] IPC clipboard failed:", err);
        }
      }

      // Method 2: Try clipboardData as fallback
      if (!clipText && cd) {
        clipText = cd.getData("text/plain") || cd.getData("text") || "";
        console.log("[Snippet] clipboardData fallback length:", clipText.length);
      }

      // Method 3: Try navigator.clipboard
      if (!clipText) {
        try {
          clipText = await navigator.clipboard.readText();
          console.log("[Snippet] navigator.clipboard length:", clipText?.length || 0);
        } catch (err) {
          console.warn("[Snippet] navigator.clipboard failed:", err);
        }
      }

      if (clipText) {
        // Insert at cursor position or replace selection
        const start = snippetInput.selectionStart;
        const end = snippetInput.selectionEnd;
        const before = snippetInput.value.substring(0, start);
        const after = snippetInput.value.substring(end);

        snippetInput.value = before + clipText + after;
        snippetInput.selectionStart = snippetInput.selectionEnd = start + clipText.length;

        console.log("[Snippet] Text inserted, new length:", snippetInput.value.length);

        if (snippetStatus) {
          snippetStatus.textContent = `✅ ${snippetInput.value.length} characters - Ready to analyze!`;
        }
      } else {
        console.warn("[Snippet] All clipboard methods failed!");
        if (snippetStatus) {
          snippetStatus.textContent = "⚠️ Paste failed - try the 📋 Paste button";
        }
      }
    }, false);  // Use bubbling phase, not capture

    // Make snippet input editable (remove any restrictions)
    snippetInput.removeAttribute("readonly");
    snippetInput.removeAttribute("disabled");

    // Add input event to track changes
    snippetInput.addEventListener("input", () => {
      console.log("[Snippet] Input event - new length:", snippetInput.value.length);
      if (snippetStatus && snippetInput.value.length > 0) {
        snippetStatus.textContent = `📝 ${snippetInput.value.length} characters`;
      }
    });

    // Add test function
    window.testSnippetPaste = async () => {
      console.log("[Test] Testing snippet paste manually...");
      try {
        if (window.sarahVision?.readClipboard) {
          const text = await window.sarahVision.readClipboard();
          console.log("[Test] Clipboard length:", text.length);
          snippetInput.value = text;
          if (snippetStatus) snippetStatus.textContent = `✅ Test: ${text.length} chars`;
        } else {
          console.error("[Test] Clipboard API not available");
        }
      } catch (err) {
        console.error("[Test] Failed:", err);
      }
    };

    console.log("[Snippet] ✓ All events bound successfully!");
    console.log("[Snippet] Test with: window.testSnippetPaste()");
  }

  //---------------------------------------------------------------------------
  // Mood Sync
  //---------------------------------------------------------------------------

  async syncMoodFromBackend() {
    if (!this.activeConversationId) return;

    try {
      const result = await this.backend.getMood(this.activeConversationId);
      if (result.ok) {
        this._updateMoodDisplay(result.mood, result.avatar_params);
        console.log("[Mood] Synced from backend:", result.mood.emotion, result.mood.intensity);
      }
    } catch (err) {
      console.warn("[Mood] Failed to sync from backend:", err);
    }
  }

  async syncTimezoneToBackend() {
    if (!this.activeConversationId) return;

    // Debounce: Only sync once per conversation (track synced conversations)
    if (!this._timezoneSyncedConversations) {
      this._timezoneSyncedConversations = new Set();
    }

    if (this._timezoneSyncedConversations.has(this.activeConversationId)) {
      return; // Already synced for this conversation
    }

    try {
      // Detect browser timezone using Intl API
      const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;

      if (!timezone) {
        console.warn("[Timezone] Could not detect browser timezone");
        return;
      }

      // Check if timezone is already set for this conversation
      const currentTz = await this.backend.getTimezone(this.activeConversationId);

      // Only update if timezone is not set or different
      if (!currentTz.timezone || currentTz.timezone !== timezone) {
        await this.backend.setTimezone(this.activeConversationId, timezone);
        console.log("[Timezone] Synced to backend:", timezone);
      }

      // Mark this conversation as synced
      this._timezoneSyncedConversations.add(this.activeConversationId);
    } catch (err) {
      console.warn("[Timezone] Failed to sync to backend:", err);
    }
  }

  openMorePanel(view = "conversations") {
    console.log("[UI] openMorePanel called with view:", view);
    if (!this.morePanel) {
      console.error("[UI] morePanel element not found!");
      return;
    }
    this.morePanel.classList.add("open");
    this.switchMoreView(view);
  }

  closeMorePanel() {
    this.morePanel.classList.remove("open");
  }

  // Issue #26: build the slash-command registry and attach the palette.
  _installSlashPalette() {
    if (!this.chatInput || this.slashPalette) return;
    const ui = this;
    const commands = [
      {
        name: "help",
        description: "Show the list of available slash commands",
        run: () => ui._showSlashHelp(),
      },
      {
        name: "clear",
        aliases: ["wipe"],
        description: "Clear the current chat log",
        run: () => ui.clearChat(),
      },
      {
        name: "new",
        aliases: ["new-chat"],
        description: "Start a new conversation",
        run: () => ui.handleNewChatClick(true),
      },
      {
        name: "mute",
        aliases: ["voice-off"],
        description: "Disable voice output (TTS)",
        run: () => {
          ui.tts?.setEnabled(false);
          ui._updateVoiceToggleUI?.();
          ui.setStatus?.("Voice output disabled");
        },
      },
      {
        name: "unmute",
        aliases: ["voice-on"],
        description: "Enable voice output (TTS)",
        run: () => {
          ui.tts?.setEnabled(true);
          ui._updateVoiceToggleUI?.();
          ui.setStatus?.("Voice output enabled");
        },
      },
      {
        name: "diagnostics",
        aliases: ["diag"],
        description: "Open the diagnostics dashboard",
        run: () => ui.openDiagnosticsDashboard?.(),
      },
      {
        name: "library",
        aliases: ["chats", "conversations"],
        description: "Open the conversation library",
        run: () => ui.toggleMorePanelView("conversations"),
      },
      {
        name: "projects",
        aliases: ["proj"],
        description: "Open the projects panel",
        run: () => ui.toggleMorePanelView("projects"),
      },
      {
        name: "memories",
        aliases: ["mem"],
        description: "Open the memories panel",
        run: () => ui.toggleMorePanelView("memories"),
      },
      {
        name: "screenshot",
        aliases: ["screen"],
        description: "Open the screen-capture panel",
        run: () => ui.toggleScreenTab?.(),
      },
    ];
    this.slashCommands = commands;
    this.slashPalette = new SarahSlashPalette(this.chatInput, commands);
  }

  _showSlashHelp() {
    const lines = (this.slashCommands || [])
      .map((c) => {
        const aliases = (c.aliases || []).map((a) => "/" + a).join(", ");
        const aliasText = aliases ? ` (also: ${aliases})` : "";
        return `\u2022 /${c.name}${aliasText} — ${c.description}`;
      })
      .join("\n");
    const body = `Available slash commands:\n${lines}`;
    if (typeof this.appendMessage === "function") {
      this.appendMessage("assistant", body);
    } else {
      console.log(body);
    }
  }

  // Issue #9: clicking the same sidebar button that opened a panel should close it.
  // Open if closed, switch view if open on a different view, close if open on this view.
  toggleMorePanelView(view = "conversations") {
    if (!this.morePanel) return;
    const isOpen = this.morePanel.classList.contains("open");
    if (isOpen && this._activeMoreView === view) {
      this.closeMorePanel();
    } else {
      this.openMorePanel(view);
    }
  }

  //---------------------------------------------------------------------------
  // Screen Capture Tab Toggle
  //---------------------------------------------------------------------------

  toggleScreenTab() {
    if (!this.screenTab) {
      console.warn("[SarahUI] Screen tab element not found");
      return;
    }

    const isHidden = this.screenTab.classList.contains("hidden");

    if (isHidden) {
      // Show screen tab
      this.screenTab.classList.remove("hidden");
      console.log("[SarahUI] Screen capture tab opened");
    } else {
      // Hide screen tab
      this.screenTab.classList.add("hidden");
      console.log("[SarahUI] Screen capture tab closed");
    }
  }

  // switch view between Conversations / Memories / Projects / Skills
  switchMoreView(view) {
    console.log("[UI] switchMoreView called with view:", view);
    this._activeMoreView = view;

    if (this.moreTabs) {
      this.moreTabs.forEach((b) =>
        b.classList.toggle("active", b.getAttribute("data-view") === view)
      );
    }

    if (this.moreViewConversations) {
      this.moreViewConversations.style.display =
        view === "conversations" ? "flex" : "none";
    }
    if (this.moreViewMemories) {
      this.moreViewMemories.style.display =
        view === "memories" ? "flex" : "none";
    }
    if (this.moreViewProjects) {
      this.moreViewProjects.style.display =
        view === "projects" ? "flex" : "none";
    }
    if (this.moreViewSkills) {
      this.moreViewSkills.style.display =
        view === "skills" ? "flex" : "none";
    }

    if (view === "conversations") this.refreshConversations();
    else if (view === "memories") this.refreshMemories();
    else if (view === "projects") this.refreshProjects();
    else if (view === "skills") this.refreshSkills();
  }

  //---------------------------------------------------------------------------
  // Conversations
  //---------------------------------------------------------------------------

  async handleNewChatClick(forceNew = false) {
    if (forceNew || this.activeConversationId == null) {
      this.activeConversationId = await this.backend.createConversation(
        "Main chat"
      );
    }
    this.clearChat();
    this.appendMessage(
      "assistant",
      "New chat started. How can I help you, Creator?"
    );

    // Sync mood state and timezone for new conversation
    await this.syncMoodFromBackend();
    await this.syncTimezoneToBackend();
  }

  async refreshConversations() {
    try {
      const list = await this.backend.listConversations();
      this.renderConversationListOptimized(list);
    } catch (err) {
      console.warn("Conv load failed", err);
    }
  }

  renderConversationList(list) {
    this.moreConvListEl.innerHTML = "";

    if (!list || list.length === 0) {
      this.moreConvEmptyEl.style.display = "block";
      return;
    }

    this.moreConvEmptyEl.style.display = "none";

    list.forEach((c) => {
      const row = document.createElement("div");
      row.className = "more-conv-row";

      const btn = document.createElement("button");
      btn.className = "more-conversation-item";
      btn.textContent = c.title || `Conversation #${c.id}`;
      if (c.id === this.activeConversationId) btn.classList.add("active");

      btn.addEventListener("click", () => {
        this.setActiveConversation(c.id);
        this.closeMorePanel();
      });

      // Rename button
      const rename = document.createElement("button");
      rename.className = "more-conv-rename";
      rename.title = "Rename";
      rename.innerHTML = "✏️";

      rename.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        const currentTitle = c.title || `Conversation #${c.id}`;

        // Create inline edit input (prompt() doesn't work well in Electron)
        const input = document.createElement("input");
        input.type = "text";
        input.className = "conv-rename-input";
        input.value = currentTitle;

        // Hide the button, show input
        btn.style.display = "none";
        row.insertBefore(input, rename);
        input.focus();
        input.select();

        const saveRename = async () => {
          const newTitle = input.value.trim();
          if (newTitle && newTitle !== currentTitle) {
            try {
              console.log("[Rename] Renaming conversation", c.id, "to:", newTitle);
              await this.backend.renameConversation(c.id, newTitle);
              console.log("[Rename] Success");
              c.title = newTitle;
            } catch (err) {
              console.error("Rename failed:", err);
            }
          }
          // Restore UI
          input.remove();
          btn.style.display = "";
          btn.textContent = c.title || currentTitle;
        };

        input.addEventListener("blur", saveRename);
        input.addEventListener("keydown", (e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            input.blur();
          } else if (e.key === "Escape") {
            input.value = currentTitle; // Reset to original
            input.blur();
          }
        });
      });

      const del = document.createElement("button");
      del.className = "more-conv-delete";
      del.textContent = "Del";

      del.addEventListener("click", async (ev) => {
        ev.stopPropagation();

        const confirmBox = document.createElement("div");
        confirmBox.className = "confirm-popup-backdrop";
        confirmBox.innerHTML = `
          <div class="confirm-popup-window">
            <div class="confirm-popup-title">Delete Conversation?</div>
            <div class="confirm-popup-text">
              Are you sure you want to delete "<b>${
                c.title || `Conversation #${c.id}`
              }</b>"?<br>
              This cannot be undone.
            </div>
            <div class="confirm-popup-actions">
              <button class="confirm-popup-cancel">Cancel</button>
              <button class="confirm-popup-delete">Delete</button>
            </div>
          </div>
        `;

        document.body.appendChild(confirmBox);

        confirmBox
          .querySelector(".confirm-popup-cancel")
          .addEventListener("click", () => {
            document.body.removeChild(confirmBox);
          });

        confirmBox
          .querySelector(".confirm-popup-delete")
          .addEventListener("click", async () => {
            try {
              document.body.removeChild(confirmBox);

              const rowElement = del.closest(".more-conv-row");
              if (rowElement) {
                rowElement.classList.add("conv-delete-anim");
                setTimeout(() => {
                  rowElement.remove();
                }, 250);
              }

              if (this.activeConversationId === c.id) {
                this.activeConversationId = null;
                // Clear from localStorage since we're deleting the active conversation
                localStorage.removeItem("sarah_last_conversation_id");
                this.clearChat();
                this.appendMessage(
                  "assistant",
                  "Chat deleted. Start a new one anytime."
                );
              }

              await this.backend.deleteConversation(c.id);
              await this.refreshConversations();
            } catch (err) {
              console.warn("Delete failed", err);
            }
          });
      });

      row.appendChild(btn);
      row.appendChild(rename);
      row.appendChild(del);
      this.moreConvListEl.appendChild(row);
    });
  }

  renderConversationListOptimized(list) {
    const perf = sarahPerfStart("more.conv.render");
    this._conversationById = new Map((list || []).map((c) => [Number(c.id), c]));

    if (!list || list.length === 0) {
      this.moreConvListEl.replaceChildren();
      this.moreConvEmptyEl.style.display = "block";
      sarahPerfEnd("more.conv.render", perf);
      return;
    }

    this.moreConvEmptyEl.style.display = "none";
    const fragment = document.createDocumentFragment();

    list.forEach((c) => {
      const row = document.createElement("div");
      row.className = "more-conv-row";
      row.dataset.conversationId = String(c.id);
      row.dataset.title = c.title || `Conversation #${c.id}`;

      const btn = document.createElement("button");
      btn.className = "more-conversation-item";
      btn.textContent = row.dataset.title;
      if (c.id === this.activeConversationId) btn.classList.add("active");

      const rename = document.createElement("button");
      rename.className = "more-conv-rename";
      rename.title = "Rename";
      rename.textContent = "Edit";
      rename.dataset.convAction = "rename";

      const del = document.createElement("button");
      del.className = "more-conv-delete";
      del.textContent = "Del";
      del.dataset.convAction = "delete";

      row.appendChild(btn);
      row.appendChild(rename);
      row.appendChild(del);
      fragment.appendChild(row);
    });

    this.moreConvListEl.replaceChildren(fragment);
    sarahPerfEnd("more.conv.render", perf);
  }

  async setActiveConversation(id) {
    this.activeConversationId = id;

    // Save to localStorage so we can restore on next startup
    if (id) {
      localStorage.setItem("sarah_last_conversation_id", id.toString());
      console.log("[UI] Saved last conversation ID:", id);
    }

    await this.reloadActiveConversation();
    this.refreshConversations();
  }

  async reloadActiveConversation() {
    const perf = sarahPerfStart("conversation.switch");
    if (!this.activeConversationId) {
      this.clearChat();
      this.appendMessage(
        "assistant",
        "New chat started. How can I help you?"
      );
      sarahPerfEnd("conversation.switch", perf);
      return;
    }

    try {
      const messages = await this.backend.getConversationMessages(
        this.activeConversationId,
        200
      );

      this.clearChat();
      if (!messages.length) {
        this.appendMessage(
          "assistant",
          "New chat started. How can I help you?"
        );
        sarahPerfEnd("conversation.switch", perf);
        return;
      }

      const fragment = document.createDocumentFragment();
      for (const msg of messages) {
        const role = msg.role === "assistant" ? "assistant" : "user";
        fragment.appendChild(
          this._createMessageBubble(role, msg.content, null, { messageId: msg.id })
        );
      }
      this.chatLog.replaceChildren(fragment);

      // Scroll to bottom after loading all messages
      this._scrollToBottom();
      sarahPerfEnd("conversation.switch", perf);
      console.log("[UI] Loaded", messages.length, "messages for conversation", this.activeConversationId);

      // Sync mood state, timezone, and context info from backend
      await this.syncMoodFromBackend();
      await this.syncAvatarBaselineFromContext(this.activeConversationId);
      await this.syncTimezoneToBackend();
      await this.syncContextInfo(this.activeConversationId);
    } catch (err) {
      console.warn("Reload failed", err);
      sarahPerfEnd("conversation.switch", perf);
    }
  }

  /**
   * Sync context info (token budget based on LLM mode)
   * Called when loading conversations and switching LLM modes
   * @param {number|null} conversationId - If provided, estimates tokens for this conversation
   */
  async syncContextInfo(conversationId = null) {
    if (this._contextSyncInFlight) return;
    this._contextSyncInFlight = true;
    try {
      // If we have a conversation ID, get token estimate for that conversation
      const info = await this.backend.getContextInfo(conversationId);
      const budget = info?.context_window_tokens || info?.token_budget || this.llmStatus?.token_budget || 1000000;
      const tokensUsed = info?.tokens_used || info?.context_used_tokens || 0;
      this._mergeLLMStatus(info || {});
      this.updateTokenIndicator(tokensUsed, budget);
      console.log(
        `[Context] Synced: mode=${info?.mode}, provider=${info?.provider}, model=${info?.model_name || info?.model}, tokens=${tokensUsed}/${budget}`
      );
    } catch (err) {
      console.warn("[Context] Failed to sync context info:", err);
    } finally {
      this._contextSyncInFlight = false;
    }
  }

  _startContextStatusSync() {
    if (this.contextStatusTimer) clearInterval(this.contextStatusTimer);
    this.contextStatusTimer = setInterval(() => {
      this.syncContextInfo(this.activeConversationId);
    }, 2000);
  }

  //---------------------------------------------------------------------------
  // Memories
  //---------------------------------------------------------------------------

  async refreshMemories() {
    const keyword = this.moreMemQueryInput.value.trim();
    try {
      // Issue #14: always pull a chat list so the user has something browsable
      // to pin from — empty keyword falls back to most-recent messages.
      const messageQuery = keyword || "";
      const messageLimit = keyword ? 25 : 30;
      const [recent, pinnedMem, pinnedMsgs, messageHits] = await Promise.all([
        this.backend.listMemories(keyword),
        this.backend.listPinnedMemories(),
        this.backend.getPinnedMessages(),
        this.backend.searchMessages(messageQuery, messageLimit),
      ]);

      const searchResults = messageHits?.results || [];

      this.renderMemoryListsOptimized(
        recent,
        pinnedMem,
        pinnedMsgs.messages || [],
        searchResults,
        { keyword },
      );
    } catch (err) {
      console.warn("Memory load failed", err);
    }
  }

  renderMemoryLists(recent, pinnedMemories, pinnedMessages, searchResults = [], opts = {}) {
    this.renderMemoryListsOptimized(recent, pinnedMemories, pinnedMessages, searchResults, opts);
  }

  renderMemoryListsOptimized(recent = [], pinnedMemories = [], pinnedMessages = [], searchResults = [], opts = {}) {
    const perf = sarahPerfStart("more.memory.render");

    // Issue #14: keep the chat-message list visible even when there's no search,
    // and relabel the heading so the user knows what they're browsing.
    if (this.moreChatSearchResults) {
      this.moreChatSearchResults.style.display = "block";
      const heading = this.moreChatSearchResults.querySelector(".more-section-title");
      if (heading) {
        heading.textContent = opts.keyword
          ? `Search Results${searchResults.length ? ` (${searchResults.length})` : ""}`
          : "Recent Chat Messages";
      }
    }

    const memoryCard = (mem) => {
      const el = document.createElement("div");
      el.className = "memory-card";

      const head = document.createElement("div");
      head.className = "memory-card-header";

      const title = document.createElement("span");
      title.className = "memory-card-title";
      const auto = (mem.tags || "").split(",").includes("auto");
      title.textContent = auto ? `#${mem.id} - learned fact` : `#${mem.id} - ${mem.role}`;

      const pinned = (mem.importance ?? 0) >= 5 || (mem.tags || "").includes("pinned");
      const pinBtn = document.createElement("button");
      pinBtn.className = "memory-pin-btn";
      pinBtn.textContent = pinned ? "Unpin" : "Pin";
      pinBtn.dataset.memoryAction = "pin-memory";
      pinBtn.dataset.id = String(mem.id);
      pinBtn.dataset.pinned = String(pinned);

      // Sarah learns facts automatically; let the user forget wrong ones.
      const delBtn = document.createElement("button");
      delBtn.className = "memory-pin-btn";
      delBtn.textContent = "Forget";
      delBtn.title = "Remove this memory";
      delBtn.dataset.memoryAction = "delete-memory";
      delBtn.dataset.id = String(mem.id);

      const actions = document.createElement("div");
      actions.className = "message-card-actions";
      actions.appendChild(pinBtn);
      actions.appendChild(delBtn);

      const body = document.createElement("div");
      body.className = "memory-card-body";
      body.textContent = mem.content;

      const meta = document.createElement("div");
      meta.className = "memory-card-meta";
      meta.textContent = mem.created_at ? parseDbTimestamp(mem.created_at).toLocaleString() : "";

      head.appendChild(title);
      head.appendChild(actions);
      el.appendChild(head);
      el.appendChild(body);
      el.appendChild(meta);
      return el;
    };

    const messageCard = (msg, isPinned = false) => {
      const el = document.createElement("div");
      el.className = "memory-card message-card";

      const head = document.createElement("div");
      head.className = "memory-card-header";

      const title = document.createElement("span");
      title.className = "memory-card-title";
      const convTitle = msg.conversation_title || `Chat #${msg.conversation_id}`;
      title.textContent = `${msg.role === "user" ? "You" : "Sarah"} - ${convTitle}`;

      const actions = document.createElement("div");
      actions.className = "message-card-actions";

      const gotoBtn = document.createElement("button");
      gotoBtn.className = "memory-pin-btn";
      gotoBtn.textContent = "Go";
      gotoBtn.title = "Go to conversation";
      gotoBtn.dataset.memoryAction = "goto-message";
      gotoBtn.dataset.conversationId = String(msg.conversation_id);

      const pinBtn = document.createElement("button");
      pinBtn.className = "memory-pin-btn";
      pinBtn.textContent = isPinned ? "Unpin" : "Pin";
      pinBtn.dataset.memoryAction = "pin-message";
      pinBtn.dataset.id = String(msg.id);
      pinBtn.dataset.pinned = String(isPinned);

      const body = document.createElement("div");
      body.className = "memory-card-body";
      body.textContent = msg.content.length > 200 ? msg.content.substring(0, 200) + "..." : msg.content;

      const meta = document.createElement("div");
      meta.className = "memory-card-meta";
      meta.textContent = msg.created_at ? parseDbTimestamp(msg.created_at).toLocaleString() : "";

      actions.appendChild(gotoBtn);
      actions.appendChild(pinBtn);
      head.appendChild(title);
      head.appendChild(actions);
      el.appendChild(head);
      el.appendChild(body);
      el.appendChild(meta);
      return el;
    };

    const buildFragment = (items, renderer) => {
      const fragment = document.createDocumentFragment();
      items.forEach((item) => fragment.appendChild(renderer(item)));
      return fragment;
    };

    if (this.moreChatSearchList) {
      if (searchResults.length > 0) {
        this.moreChatSearchList.replaceChildren(buildFragment(searchResults, (msg) => messageCard(msg, Boolean(msg.pinned))));
      } else {
        const empty = document.createElement("div");
        empty.className = "more-empty-state more-empty-state-compact";
        empty.textContent = opts.keyword
          ? `No messages match "${opts.keyword}".`
          : "No chat messages yet — start a conversation to see them here.";
        this.moreChatSearchList.replaceChildren(empty);
      }
    }

    if (this.morePinnedMessages) {
      if (pinnedMessages.length > 0) {
        this.morePinnedMessages.replaceChildren(buildFragment(pinnedMessages, (msg) => messageCard(msg, true)));
      } else {
        const empty = document.createElement("div");
        empty.className = "more-empty-state more-empty-state-compact";
        empty.textContent = "No pinned messages yet";
        this.morePinnedMessages.replaceChildren(empty);
      }
    }

    this.moreMemPinnedEl.replaceChildren(buildFragment(pinnedMemories, memoryCard));
    this.moreMemRecentEl.replaceChildren(buildFragment(recent, memoryCard));
    sarahPerfEnd("more.memory.render", perf);
  }

  //---------------------------------------------------------------------------
  // Projects
  //---------------------------------------------------------------------------

  async handleNewProjectClick() {
    const name = prompt("Enter project name:");
    if (!name || !name.trim()) return;

    const description = prompt("Enter project description (optional):");
    const rootPath = prompt(
      "Enter project root path (optional, for auto-detection):\n" +
      "Example: C:\\Users\\YourName\\Projects\\MyApp"
    );

    try {
      const projectId = await this.backend.createProject(
        name.trim(),
        description?.trim() || null,
        rootPath?.trim() || null
      );

      await this.refreshProjects();

      // If root path provided, offer to scan and import
      if (rootPath && rootPath.trim()) {
        if (confirm("Project created! Scan folder and import files?")) {
          await this.scanAndImportProject(projectId, rootPath.trim(), name.trim());
        }
      } else {
        this.showProjectUploadDialog(projectId, name.trim());
      }
    } catch (err) {
      console.warn("Create project failed", err);
      alert("Failed to create project: " + err.message);
    }
  }

  async scanAndImportProject(projectId, rootPath, projectName) {
    try {
      const scanResult = await this.backend.scanProjectFolder(rootPath);

      if (!scanResult.ok) {
        alert("Failed to scan project folder");
        return;
      }

      const metadata = scanResult.metadata;
      let info = `Detected:\n`;
      if (metadata.primary_language) info += `Language: ${metadata.primary_language}\n`;
      if (metadata.framework) info += `Framework: ${metadata.framework}\n`;
      if (metadata.package_manager) info += `Package Manager: ${metadata.package_manager}\n`;

      alert(info + "\nProject metadata saved!");

      await this.refreshProjects();
    } catch (err) {
      console.warn("Scan project failed", err);
      alert("Failed to scan project: " + err.message);
    }
  }

  async refreshProjects() {
    console.log("[UI] refreshProjects called");
    try {
      const list = await this.backend.listProjects();
      console.log("[UI] Projects loaded:", list?.length || 0, "projects");
      this.renderProjectListOptimized(list);
    } catch (err) {
      console.error("[UI] Project load failed:", err);
    }
  }

  /**
   * Format bytes to human readable size
   */
  _formatFileSize(bytes) {
    if (!bytes || bytes === 0) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    const k = 1024;
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + units[i];
  }

  renderProjectList(list) {
    this.renderProjectListOptimized(list);
  }

  renderProjectListOptimized(list) {
    const perf = sarahPerfStart("more.project.render");
    console.log("[UI] renderProjectListOptimized called with", list?.length || 0, "projects");

    if (!this.moreProjectListEl) {
      console.error("[UI] moreProjectListEl not found!");
      return;
    }

    this._projectById = new Map((list || []).map((p) => [Number(p.id), p]));

    if (!list || list.length === 0) {
      this.moreProjectListEl.replaceChildren();
      if (this.moreProjectEmptyEl) this.moreProjectEmptyEl.style.display = "block";
      sarahPerfEnd("more.project.render", perf);
      return;
    }

    if (this.moreProjectEmptyEl) this.moreProjectEmptyEl.style.display = "none";

    const fragment = document.createDocumentFragment();
    list.forEach((p) => {
      const row = document.createElement("div");
      row.className = "more-conv-row";
      row.dataset.projectId = String(p.id);

      const btn = document.createElement("button");
      btn.className = "more-conversation-item";
      btn.dataset.projectAction = "open";

      const title = document.createElement("div");
      title.className = "project-list-title";
      title.textContent = p.name;

      const meta = document.createElement("div");
      meta.className = "project-list-meta";

      const totalSize = this._formatFileSize(p.total_size || 0);
      const base = document.createElement("span");
      base.textContent = `${p.file_count} file(s) - ${totalSize}`;
      meta.appendChild(base);

      if (p.primary_language) {
        const lang = document.createElement("span");
        lang.className = "project-list-badge";
        lang.textContent = p.primary_language;
        meta.appendChild(lang);
      }

      if (p.current_branch) {
        const branch = document.createElement("span");
        branch.className = "project-list-badge project-list-badge-branch";
        branch.textContent = p.current_branch;
        meta.appendChild(branch);
      }

      btn.appendChild(title);
      btn.appendChild(meta);
      row.appendChild(btn);
      fragment.appendChild(row);
    });

    this.moreProjectListEl.replaceChildren(fragment);
    sarahPerfEnd("more.project.render", perf);
  }

  //---------------------------------------------------------------------------
  // Skills
  //---------------------------------------------------------------------------

  async refreshSkills(options = {}) {
    const discover = !!options.discover;

    try {
      if (this.moreDiscoverSkillsBtn) this.moreDiscoverSkillsBtn.disabled = true;
      if (this.moreRefreshSkillsBtn) this.moreRefreshSkillsBtn.disabled = true;

      if (discover) {
        this._renderSkillDetail(
          "Discovering skills",
          "Checking Sarah's skills..."
        );
        await this.backend.discoverSkills();
      }

      const skills = await this.backend.listSkills();
      this.renderSkillListOptimized(skills);

      if (discover) {
        const fresh = skills.filter((s) => !s.stale).length;
        const stale = skills.filter((s) => !!s.stale).length;
        this._renderSkillDetail(
          "Skills discovery complete",
          `${fresh} fresh skill(s), ${stale} stale row(s), ${skills.length} total.`
        );
      }
    } catch (err) {
      console.warn("[Skills] refresh failed:", err);
      this._renderSkillDetail("Skills refresh failed", err?.message || String(err));
    } finally {
      if (this.moreDiscoverSkillsBtn) this.moreDiscoverSkillsBtn.disabled = false;
      if (this.moreRefreshSkillsBtn) this.moreRefreshSkillsBtn.disabled = false;
    }
  }

  renderSkillListOptimized(skills = []) {
    const perf = sarahPerfStart("more.skills.render");
    const list = [...(skills || [])].sort((a, b) => {
      const staleDelta = Number(!!a.stale) - Number(!!b.stale);
      if (staleDelta !== 0) return staleDelta;
      return String(a.name || a.slug || "").localeCompare(String(b.name || b.slug || ""));
    });

    this._skillBySlug = new Map(
      list.map((skill) => [String(skill.slug), skill])
    );

    if (!this.moreSkillsListEl) {
      sarahPerfEnd("more.skills.render", perf);
      return;
    }

    if (list.length === 0) {
      this.moreSkillsListEl.replaceChildren();
      if (this.moreSkillsEmptyEl) this.moreSkillsEmptyEl.style.display = "block";
      sarahPerfEnd("more.skills.render", perf);
      return;
    }

    if (this.moreSkillsEmptyEl) this.moreSkillsEmptyEl.style.display = "none";

    const fragment = document.createDocumentFragment();
    const makeBadge = (text, className = "") => {
      const badge = document.createElement("span");
      badge.className = `skill-badge ${className}`.trim();
      badge.textContent = text;
      return badge;
    };

    for (const skill of list) {
      const slug = String(skill.slug || "");
      const enabled = skill.enabled === true || skill.enabled === 1;
      const stale = !!skill.stale;
      const bodyLen = Number(skill.body_len || 0);

      const card = document.createElement("div");
      card.className = "skill-card";
      card.dataset.slug = slug;

      const header = document.createElement("div");
      header.className = "skill-card-header";

      const heading = document.createElement("div");
      const title = document.createElement("div");
      title.className = "skill-card-title";
      title.textContent = skill.name || slug;

      const slugEl = document.createElement("div");
      slugEl.className = "skill-card-slug";
      slugEl.textContent = slug;

      heading.appendChild(title);
      heading.appendChild(slugEl);

      const status = document.createElement("span");
      status.className = `skill-badge ${enabled ? "enabled" : "disabled"}`;
      status.textContent = enabled ? "enabled" : "disabled";

      header.appendChild(heading);
      header.appendChild(status);
      card.appendChild(header);

      const desc = document.createElement("div");
      desc.className = "skill-card-description";
      desc.textContent = skill.description || "No description provided.";
      card.appendChild(desc);

      const badges = document.createElement("div");
      badges.className = "skill-card-badges";
      badges.appendChild(makeBadge(stale ? "stale" : "fresh", stale ? "stale" : ""));
      badges.appendChild(makeBadge(`${bodyLen} chars`));
      if (skill.path) badges.appendChild(makeBadge("disk-backed"));
      card.appendChild(badges);

      if (skill.path) {
        const pathEl = document.createElement("div");
        pathEl.className = "skill-card-path";
        pathEl.title = skill.path;
        pathEl.textContent = skill.path;
        card.appendChild(pathEl);
      }

      const actions = document.createElement("div");
      actions.className = "skill-card-actions";

      const toggle = document.createElement("button");
      toggle.className = `pill-btn pill-btn-small ${enabled ? "" : "pill-btn-accent"}`.trim();
      toggle.dataset.skillAction = enabled ? "disable" : "enable";
      toggle.dataset.slug = slug;
      toggle.textContent = enabled ? "Disable" : "Enable";

      const manifest = document.createElement("button");
      manifest.className = "pill-btn pill-btn-small";
      manifest.dataset.skillAction = "manifest";
      manifest.dataset.slug = slug;
      manifest.textContent = "Manifest";

      actions.appendChild(toggle);
      actions.appendChild(manifest);
      card.appendChild(actions);
      fragment.appendChild(card);
    }

    this.moreSkillsListEl.replaceChildren(fragment);
    sarahPerfEnd("more.skills.render", perf);
  }

  _renderSkillDetail(title, body) {
    if (!this.moreSkillDetailEl) return;

    const titleEl = document.createElement("div");
    titleEl.className = "skill-detail-title";
    titleEl.textContent = title || "Skill detail";

    const bodyEl = document.createElement("pre");
    bodyEl.textContent = String(body || "");

    this.moreSkillDetailEl.replaceChildren(titleEl, bodyEl);
    this.moreSkillDetailEl.classList.remove("hidden");
  }

  showProjectUploadDialog(projectId, projectName) {
    const input = document.createElement("input");
    input.type = "file";
    input.multiple = true;

    input.addEventListener("change", async (e) => {
      const files = Array.from(e.target.files);
      if (files.length === 0) return;

      for (const file of files) {
        try {
          const content = await this.readFileAsText(file);

          await this.backend.uploadFileToProject(projectId, {
            file_name: file.name,
            file_path: file.webkitRelativePath || file.name,
            content: content,
            file_type: file.type || "text/plain",
            file_size: file.size,
          });
        } catch (err) {
          console.warn(`Failed to upload ${file.name}:`, err);
        }
      }

      alert(`Uploaded ${files.length} file(s) to project "${projectName}"`);
      await this.refreshProjects();
    });

    input.click();
  }

  async readFileAsText(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = (e) => resolve(e.target.result);
      reader.onerror = (e) => reject(e);
      reader.readAsText(file);
    });
  }

  async showProjectDetails(projectId, projectName) {
    try {
      // Load all project information
      const [project, files, gitStatus, commits] = await Promise.all([
        this.backend.getProject(projectId),
        this.backend.getProjectFiles(projectId),
        this.backend.getGitStatus(projectId).catch(() => ({ ok: false })),
        this.backend.getRecentCommits(projectId, 5).catch(() => [])
      ]);

      // Build detailed info
      let info = `📁 Project: ${projectName}\n`;
      info += `\n`;

      // Metadata
      if (project.ok && project.project) {
        const p = project.project;
        if (p.primary_language) {
          info += `Language: ${p.primary_language}\n`;
        }
        if (p.framework) {
          info += `Framework: ${p.framework}\n`;
        }
        if (p.package_manager) {
          info += `Package Manager: ${p.package_manager}\n`;
        }
      }

      // Git info
      if (gitStatus.ok) {
        info += `\n🌿 Git Status:\n`;
        info += `Branch: ${gitStatus.branch}\n`;
        if (gitStatus.modified && gitStatus.modified.length > 0) {
          info += `Modified: ${gitStatus.modified.length} file(s)\n`;
        }
        if (gitStatus.untracked && gitStatus.untracked.length > 0) {
          info += `Untracked: ${gitStatus.untracked.length} file(s)\n`;
        }
      }

      // Recent commits
      if (commits.length > 0) {
        info += `\n📝 Recent Commits:\n`;
        commits.slice(0, 3).forEach(c => {
          const date = new Date(c.timestamp * 1000).toLocaleDateString();
          info += `• ${c.message.substring(0, 50)} (${c.author}, ${date})\n`;
        });
      }

      // Files
      info += `\n📄 Files (${files.length}):\n`;
      if (files.length === 0) {
        info += "No files uploaded yet.\n";
      } else {
        files.slice(0, 10).forEach(f => {
          info += `• ${f.file_name} (${(f.file_size / 1024).toFixed(2)} KB)\n`;
        });
        if (files.length > 10) {
          info += `... and ${files.length - 10} more\n`;
        }
      }

      alert(info);

      // Options
      const action = prompt(
        "Choose action:\n" +
        "1 - Send project context to chat\n" +
        "2 - View git diff\n" +
        "3 - Set current working file\n" +
        "Enter number or leave blank to close:"
      );

      if (action === "1") {
        const context = await this.backend.getProjectContext(projectId);
        this.chatInput.value = `Here is the context from project "${projectName}":\n\n${context.substring(0, 500)}...\n\nWhat would you like to know about this project?`;
      } else if (action === "2") {
        const diff = await this.backend.getGitDiff(projectId);
        if (diff) {
          alert(`Git Diff:\n\n${diff.substring(0, 2000)}${diff.length > 2000 ? '\n...(truncated)' : ''}`);
        } else {
          alert("No changes to show");
        }
      } else if (action === "3") {
        if (files.length > 0) {
          const fileNames = files.map((f, i) => `${i + 1}. ${f.file_name}`).join("\n");
          const fileIndex = prompt(`Select file number:\n${fileNames}`);
          const index = parseInt(fileIndex) - 1;
          if (index >= 0 && index < files.length) {
            await this.backend.setCurrentFile(projectId, files[index].id);
            alert(`Set "${files[index].file_name}" as current working file`);
          }
        } else {
          alert("No files to set as current");
        }
      }

      this.closeMorePanel();
    } catch (err) {
      console.warn("Failed to load project details", err);
      alert("Failed to load project details");
    }
  }

  //---------------------------------------------------------------------------
  // Chat Flow
  //---------------------------------------------------------------------------

  async _sendChat(text) {
    const chatPerf = sarahPerfStart("chat.submit");
    const content = (text || "").trim();
    if (!content) {
      sarahPerfEnd("chat.submit.empty", chatPerf);
      return;
    }

    console.log("[Chat] _sendChat called, activeConversationId before:", this.activeConversationId);
    this._chatBusy = true;

    if (!this.activeConversationId) {
      try {
        this.activeConversationId = await this.backend.createConversation(
          "Main chat"
        );
        // Save to localStorage for restore on next startup
        localStorage.setItem("sarah_last_conversation_id", this.activeConversationId.toString());
        console.log("[UI] Created and saved new conversation:", this.activeConversationId);
        // Sync timezone BEFORE first message (blocking - ensures correct time context)
        await this.syncTimezoneToBackend();
      } catch (err) {
        console.warn("[Chat] Conversation setup failed:", err);
      }
    }

    console.log("[Chat] Sending message with conversation_id:", this.activeConversationId);

    // Chat messages route through the active LLM mode.
    // Use snippet panel or screenshot for local Ollama vision/code analysis.
    // Create user bubble (will update with messageId after response)
    this.appendMessage("user", content);
    window.SARAH_AVATAR_SYSTEM?.onUserMessage?.(content);
    sarahPerfEnd("chat.user_bubble.painted", chatPerf);
    const userBubble = this.chatLog?.lastElementChild;
    this.setInputDisabled(true);
    this.setStatus("Thinking...");

    // Show loading indicator
    const loadingEl = this._showLoadingIndicator();

    try {
      const { resp, bubble, speech } = await this._streamAssistantReply(content, { loadingEl });

      // Remove loading indicator
      this._hideLoadingIndicator(loadingEl);

      // Update user bubble with messageId for edit/delete functionality
      if (userBubble && resp?.user_message_id) {
        userBubble.dataset.messageId = resp.user_message_id;
        this._addActionsToExistingBubble(userBubble, "user", content, resp.user_message_id);
      }

      const reply = this._finalizeAssistantReply(resp, bubble);
      window.SARAH_AVATAR_SYSTEM?.onAIResponse?.(reply, {
        emotion: resp?.emotion,
        intensity: resp?.emotion_intensity,
        affinity: resp?.affinity_to_creator,
        source: "chat-response",
      });

      // Ensure scroll to bottom after new message
      this._scrollToBottom();

      // Issue #3: resp.tokens_used reflects only the current request's prompt
      // size, not the cumulative conversation context. After a vision-attached
      // turn, that single value made the bar look like it was tracking only
      // vision tokens. Re-sync from /api/context_info so the % reflects the
      // full conversation rolled up to date.
      console.log("[Token] Response tokens:", resp?.tokens_used, "/", resp?.token_budget);
      this.syncContextInfo(this.activeConversationId)
        .catch(err => console.warn("[Token] Context sync failed:", err));

      // Re-enable input immediately for better responsiveness
      this.setInputDisabled(false);
      if (this.chatInput) this.chatInput.focus();
      this.setStatus("Ready");

      // Finish voice and sync mood in background (non-blocking)
      this._speakFinalReply(reply, resp, speech, "chat-complete");
      this.syncAvatarBaselineFromContext(this.activeConversationId)
        .catch(err => console.warn("[AvatarContext] Sync failed:", err));
      this.syncMoodFromBackend().catch(err => console.warn("[Mood] Sync failed:", err));

    } catch (err) {
      console.warn("Chat failed", err);
      this._hideLoadingIndicator(loadingEl);
      this.appendMessage("assistant", "Backend error. Please try again.");
      this.setStatus("Error");
      this._setAvatarMode("idle", { source: "chat-error" });
    } finally {
      this.setInputDisabled(false);
      if (this.chatInput) this.chatInput.focus();
      this._chatBusy = false;
      this._flushVoiceQueue();
    }
  }

  _migrateLegacyAttachments() {
    const pendingImage = window.SARAH_UI?.pendingImageData;
    if (pendingImage?.dataUrl) {
      this._addPendingAttachment({
        kind: "image",
        name: pendingImage.name || pendingImage.fileName || `pasted-${Date.now()}.png`,
        type: pendingImage.type || "image/png",
        size: pendingImage.size || 0,
        dataUrl: pendingImage.dataUrl,
      });
      window.SARAH_UI.pendingImageData = null;
      if (this.chatInput) this.chatInput.placeholder = "Ask Sarah anything...";
    }

    if (this.attachedImage?.dataUrl) {
      this._addPendingAttachment({
        kind: "image",
        name: this.attachedImage.name || this.attachedImage.fileName || "uploaded-image.png",
        type: this.attachedImage.type || "image/png",
        size: this.attachedImage.size || 0,
        dataUrl: this.attachedImage.dataUrl,
      });
      this.attachedImage = null;
    }

    if (this.attachedSnippet) {
      this._addPendingAttachment({
        kind: "text",
        name: "code-snippet.txt",
        type: "text/plain",
        size: this.attachedSnippet.length,
        text: this.attachedSnippet,
      });
      this.attachedSnippet = null;
      const snippetStatus = document.getElementById("snippet-status");
      if (snippetStatus) snippetStatus.textContent = "";
    }
  }

  _dataUrlToBlob(dataUrl) {
    const [header, base64Data] = dataUrl.split(",");
    const mimeType = header?.split(";")[0]?.split(":")[1] || "image/png";
    const byteCharacters = atob(base64Data || "");
    const byteNumbers = new Array(byteCharacters.length);
    for (let i = 0; i < byteCharacters.length; i++) {
      byteNumbers[i] = byteCharacters.charCodeAt(i);
    }
    return new Blob([new Uint8Array(byteNumbers)], { type: mimeType });
  }

  async _analyzeImageAttachment(image, prompt) {
    const blob = this._dataUrlToBlob(image.dataUrl);
    const formData = new FormData();
    formData.append("file", blob, image.name || "attached-image.png");
    formData.append("mode", "general");

    const response = await fetch(`${API_BASE}/vision/analyze`, {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Vision API error: ${response.status} ${response.statusText} - ${errorText}`);
    }

    const data = await response.json();
    return data.analysis || data.description || data.summary || data.error || "Image analyzed.";
  }

  _buildAttachmentMessage(prompt, imageSummaries = [], textAttachments = []) {
    const lines = [prompt?.trim() || "Please review the attached content."];

    for (const item of imageSummaries) {
      lines.push(
        "",
        `[Image Attachment: ${item.image.name || "image"}]`,
        `Visual summary:\n${item.summary || "No visual summary returned."}`
      );
    }

    for (const att of textAttachments) {
      const rawText = att.text || "";
      const cap = 20000;
      const clipped = rawText.length > cap
        ? `${rawText.slice(0, cap)}\n\n[Attachment truncated at ${cap} characters]`
        : rawText;
      lines.push("", `[Text Attachment: ${att.name || "text"}]`, "```", clipped, "```");
    }

    return lines.join("\n");
  }

  async _ensureActiveConversation() {
    if (this.activeConversationId) return;

    this.activeConversationId = await this.backend.createConversation("Main chat");
    localStorage.setItem("sarah_last_conversation_id", this.activeConversationId.toString());
    await this.syncTimezoneToBackend();
  }

  async _sendChatWithAttachments(text, attachments) {
    const prompt = text?.trim() || "Analyze the attached content.";
    const imageAttachments = attachments.filter((att) => att.kind === "image" && att.dataUrl);
    const textAttachments = attachments.filter((att) => att.kind === "text" && att.text);

    try {
      await this._ensureActiveConversation();
    } catch (err) {
      console.warn("[Attachment Chat] Conversation setup failed:", err);
    }

    const displayText = attachments.length === 1
      ? `${prompt}\n\n[1 attachment]`
      : `${prompt}\n\n[${attachments.length} attachments]`;
    const firstImage = imageAttachments[0];
    this.appendMessage("user", displayText, firstImage?.dataUrl || null);
    window.SARAH_AVATAR_SYSTEM?.onUserMessage?.(prompt);
    const userBubble = this.chatLog?.lastElementChild;

    this.setInputDisabled(true);
    this.setStatus("Analyzing attachments...");
    const loadingEl = this._showLoadingIndicator();

    try {
      const imageSummaries = [];
      for (const image of imageAttachments) {
        this.setStatus(`Analyzing ${image.name || "image"}...`);
        const summary = await this._analyzeImageAttachment(image, prompt);
        imageSummaries.push({ image, summary });
      }

      const finalMessage = this._buildAttachmentMessage(prompt, imageSummaries, textAttachments);
      this.setStatus("Thinking...");
      this._setAvatarMode("thinking", { source: "attachment-chat" });
      const { resp, bubble, speech } = await this._streamAssistantReply(finalMessage, { loadingEl });

      this._hideLoadingIndicator(loadingEl);

      if (userBubble && resp?.user_message_id) {
        userBubble.dataset.messageId = resp.user_message_id;
        this._addActionsToExistingBubble(userBubble, "user", displayText, resp.user_message_id);
      }

      const reply = this._finalizeAssistantReply(resp, bubble);
      window.SARAH_AVATAR_SYSTEM?.onAIResponse?.(reply, {
        emotion: resp?.emotion,
        intensity: resp?.emotion_intensity,
        affinity: resp?.affinity_to_creator,
        source: "attachment-chat-response",
      });
      this._scrollToBottom();
      // Issue #3: re-sync from /api/context_info for cumulative tokens.
      this.syncContextInfo(this.activeConversationId)
        .catch(err => console.warn("[Token] Context sync failed:", err));
      this._clearPendingAttachments();

      this.setStatus("Ready");
      this._speakFinalReply(reply, resp, speech, "attachment-chat-complete");
      this.syncAvatarBaselineFromContext(this.activeConversationId)
        .catch(err => console.warn("[AvatarContext] Sync failed:", err));
      this.syncMoodFromBackend().catch(err => console.warn("[Mood] Sync failed:", err));
    } catch (err) {
      console.error("[Attachment Chat] Failed:", err);
      this._hideLoadingIndicator(loadingEl);
      this.appendMessage("assistant", `Failed to analyze attachment: ${err.message}`);
      this.setStatus("Error");
      this._setAvatarMode("idle", { source: "attachment-chat-error" });
    } finally {
      this.setInputDisabled(false);
      if (this.chatInput) this.chatInput.focus();
    }
  }

  async sendChatFromInput() {
    const text = (this.chatInput?.value || "").trim();
    this._migrateLegacyAttachments();

    const attachments = [...this.pendingAttachments];
    if (!text && !attachments.length) return;

    if (this.chatInput) this.chatInput.value = "";

    if (!attachments.length) {
      await this._sendChat(text);
      return;
    }

    if (attachments.some((att) => att.kind === "image")) {
      await this._sendChatWithAttachments(text, attachments);
      return;
    }

    const finalMessage = this._buildAttachmentMessage(
      text || "Please review the attached text.",
      [],
      attachments.filter((att) => att.kind === "text")
    );
    this._clearPendingAttachments();
    await this._sendChat(finalMessage);
  }

  async _sendImageWithOllama(prompt) {
    this._migrateLegacyAttachments();
    const attachments = [...this.pendingAttachments];
    if (!attachments.length) return;
    await this._sendChatWithAttachments(prompt || "Analyze this image", attachments);
  }
  async _handleSnippetAnalyze() {
    const snippetInput = document.getElementById("snippet-input");
    const snippetStatus = document.getElementById("snippet-status");

    console.log("[Snippet] ========== ANALYZE BUTTON CLICKED ==========");

    if (!snippetInput) {
      console.error("[Snippet] snippet-input element not found");
      return;
    }

    // Debug: Log the raw value
    console.log("[Snippet] Raw textarea value:", JSON.stringify(snippetInput.value));
    console.log("[Snippet] Textarea value length:", snippetInput.value.length);

    const snippet = snippetInput.value.trim();
    console.log("[Snippet] Trimmed snippet length:", snippet.length);
    console.log("[Snippet] First 200 chars:", snippet.substring(0, 200));

    if (!snippet) {
      if (snippetStatus) snippetStatus.textContent = "⚠️ Please type or paste some code first (Ctrl+V)";
      return;
    }

    if (snippet.length < 5) {
      if (snippetStatus) snippetStatus.textContent = "⚠️ Snippet too short - paste more code!";
      console.warn("[Snippet] Snippet too short:", snippet);
      return;
    }

    if (snippetStatus) snippetStatus.textContent = "🔍 Analyzing with Ollama (qwen3-vl)...";

    try {
      // Auto-detect language
      const language = this._detectLanguage(snippet);
      console.log("[Snippet] Detected language:", language);
      console.log("[Snippet] Snippet preview:", snippet.substring(0, 100));

      // Use Ollama text model for code analysis
      console.log("[Snippet] Sending to Ollama qwen3-vl...");

      const ollamaResponse = await fetch(`${API_BASE}/api/ollama/analyze-text`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: snippet,
          mode: "code",
          language: language
        })
      });

      console.log("[Snippet] Response status:", ollamaResponse.status);

      if (!ollamaResponse.ok) {
        const errorText = await ollamaResponse.text();
        console.error("[Snippet] Error response:", errorText);
        throw new Error(`Ollama API error: ${errorText.substring(0, 100)}`);
      }

      const result = await ollamaResponse.json();
      console.log("[Snippet] Result:", result);

      const analysisText = result.analysis || result.text;
      if (analysisText) {
        // Display the analysis result in chat
        this.appendMessage("assistant", `**Code Analysis** (via Ollama qwen3-vl)\n\n${analysisText}`);
        if (this.tts.isEnabled()) await this.tts.speak("I've analyzed your code snippet.");
        if (snippetStatus) snippetStatus.textContent = `✅ Analysis complete (${result.timing_ms || 0}ms)`;
      } else {
        throw new Error(result.error || result.detail || "No analysis returned");
      }

    } catch (err) {
      console.error("[Snippet] Analysis failed:", err);
      // Show user-friendly error
      let errorMsg = err.message;
      if (errorMsg.includes("timed out")) {
        errorMsg = "Ollama is still loading the model. Please try again in a moment.";
      } else if (errorMsg.includes("fetch")) {
        errorMsg = "Cannot connect to backend. Is it running on port 8907?";
      }
      if (snippetStatus) snippetStatus.textContent = `❌ ${errorMsg}`;
      this.appendMessage("assistant", `❌ Code analysis failed: ${errorMsg}`);
    }
  }

  async _handleSnippetAttach() {
    const snippetInput = document.getElementById("snippet-input");
    const snippetStatus = document.getElementById("snippet-status");

    if (!snippetInput) return;

    const snippet = snippetInput.value.trim();
    if (!snippet) {
      if (snippetStatus) snippetStatus.textContent = "⚠️ Please paste some code or text first";
      return;
    }

    if (snippetStatus) {
      snippetStatus.textContent = `📎 Sending snippet to Sarah...`;
    }

    // Detect language
    const language = this._detectLanguage(snippet);

    // Send snippet directly to Sarah as a message (no extra typing needed)
    const formattedMessage = `Here's some ${language} code I'd like you to look at:\n\n\`\`\`${language}\n${snippet}\n\`\`\``;

    // Display user message
    this.appendMessage("user", formattedMessage);
    this.setInputDisabled(true);
    this.setStatus("Thinking...");

    try {
      // Send to backend chat API
      const resp = await this.backend.chat(
        formattedMessage,
        true,  // from_creator
        this.activeConversationId
      );

      this.setStatus("Ready");
      this.setInputDisabled(false);

      // Display Sarah's response
      if (resp && resp.reply) {
        this.appendMessage("assistant", resp.reply);
        // Issue #3: re-sync from /api/context_info for cumulative tokens.
        this.syncContextInfo(this.activeConversationId)
          .catch(err => console.warn("[Token] Context sync failed:", err));
        this._speakFinalReply(this._sanitizeAssistantDisplayText(resp.reply), resp, null, "snippet-complete");
      }

      if (snippetStatus) snippetStatus.textContent = `✅ Sent to Sarah`;

      // Clear snippet input
      snippetInput.value = "";

    } catch (err) {
      console.error("[Snippet] Failed to send to Sarah:", err);
      if (snippetStatus) snippetStatus.textContent = `❌ Failed: ${err.message}`;
      this.setStatus("Ready");
      this.setInputDisabled(false);
    }
  }

  _detectLanguage(code) {
    const patterns = {
      'javascript': /\b(const|let|var|function|=>\s*{|console\.log)\b/,
      'typescript': /\b(interface|type\s+\w+\s*=|enum\s+\w+)\b/,
      'python': /\b(def|import|from|class|if __name__|print\()\b/,
      'java': /\b(public|private|class|void|static)\b/,
      'cpp': /\b(#include|std::|cout|cin|namespace)\b/,
      'csharp': /\b(using|namespace|public|private|class|void)\b/,
      'go': /\b(package|func|import|var|:=)\b/,
      'rust': /\b(fn|let|mut|impl|trait|use)\b/,
      'html': /<\/?\w+[^>]*>/,
      'css': /[.#]?\w+\s*{[^}]*}/,
      'json': /^\s*[{\[]/,
      'sql': /\b(SELECT|INSERT|UPDATE|DELETE|FROM|WHERE|JOIN)\b/i,
    };

    for (const [lang, pattern] of Object.entries(patterns)) {
      if (pattern.test(code)) {
        return lang;
      }
    }

    return 'text';
  }

  //---------------------------------------------------------------------------
  // Multimodal context → send context summary into same thread
  //---------------------------------------------------------------------------

  async sendMultimodalContext(kind, description, ocrText) {
    let text = `Attached ${kind} for analysis.`;

    if (description) {
      text += `\n\nVisual summary:\n${description}`;
    }
    if (ocrText) {
      text += `\n\nExtracted text:\n${ocrText}`;
    }

    if (!this.activeConversationId) {
      try {
        this.activeConversationId = await this.backend.createConversation(
          "Main chat"
        );
        // Save to localStorage for restore on next startup
        localStorage.setItem("sarah_last_conversation_id", this.activeConversationId.toString());
        console.log("[UI] Created and saved new conversation:", this.activeConversationId);
        // Sync timezone for new conversation (non-blocking)
        this.syncTimezoneToBackend().catch(err => console.warn("[Timezone] Sync failed:", err));
      } catch {}
    }

    this.appendMessage("user", text);
    window.SARAH_AVATAR_SYSTEM?.onUserMessage?.(text);
    this.setInputDisabled(true);
    this.setStatus("Analyzing attachment...");
    this._setAvatarMode("thinking", { source: "multimodal-chat" });

    try {
      const resp = await this.backend.chat(
        text,
        true,
        this.activeConversationId
      );
      const reply = this._sanitizeAssistantDisplayText(resp?.reply || "(no reply)");
      this.appendMessage("assistant", reply);
      window.SARAH_AVATAR_SYSTEM?.onAIResponse?.(reply, {
        emotion: resp?.emotion,
        intensity: resp?.emotion_intensity,
        affinity: resp?.affinity_to_creator,
        source: "multimodal-chat-response",
      });

      // Issue #3: re-sync from /api/context_info for cumulative tokens.
      console.log("[Token] Attachment response tokens:", resp?.tokens_used, "/", resp?.token_budget);
      this.syncContextInfo(this.activeConversationId)
        .catch(err => console.warn("[Token] Context sync failed:", err));

      // Re-enable input immediately for better responsiveness
      this.setInputDisabled(false);
      if (this.chatInput) this.chatInput.focus();
      this.setStatus("Ready");

      // Run TTS and mood sync in background (non-blocking)
      this._speakFinalReply(reply, resp, null, "multimodal-chat-complete");
      this.syncAvatarBaselineFromContext(this.activeConversationId)
        .catch(err => console.warn("[AvatarContext] Sync failed:", err));
      this.syncMoodFromBackend().catch(err => console.warn("[Mood] Sync failed:", err));

    } catch (err) {
      console.warn("Multimodal chat failed", err);
      this.appendMessage("assistant", "Error analyzing that attachment.");
      this.setStatus("Error");
      this._setAvatarMode("idle", { source: "multimodal-chat-error" });
    } finally {
      this.setInputDisabled(false);
    }
  }
}

// ============================================================================
// BOOTSTRAP — Initialize Backend, TTS, UI
// ============================================================================

document.addEventListener("DOMContentLoaded", () => {
  try {
    const backend = new SarahBackend();
    const tts = new SarahTTS(backend);
    window.SARAH_UI = new SarahUI(backend, tts);

    // Start vision status monitor
    startVisionStatusMonitor();

    // Direct button binding as backup (in case class binding fails)
    const btnLibrary = document.getElementById("btn-library");
    const btnProjects = document.getElementById("btn-projects");

    if (btnLibrary) {
      btnLibrary.onclick = (e) => {
        e.stopPropagation(); // Prevent click-outside handler from firing
        console.log("[Direct] Library button clicked");
        if (window.SARAH_UI) {
          window.SARAH_UI.openMorePanel("conversations");
        }
      };
      console.log("[SARAH] Library button bound directly");
    } else {
      console.error("[SARAH] btn-library element NOT FOUND in DOM!");
    }

    if (btnProjects) {
      btnProjects.onclick = (e) => {
        e.stopPropagation(); // Prevent click-outside handler from firing
        console.log("[Direct] Projects button clicked");
        if (window.SARAH_UI) {
          window.SARAH_UI.openMorePanel("projects");
        }
      };
      console.log("[SARAH] Projects button bound directly");
    } else {
      console.error("[SARAH] btn-projects element NOT FOUND in DOM!");
    }

    console.log("[SARAH] UI boot completed.");
  } catch (err) {
    console.error("[SARAH] UI Initialization failed:", err);
  }
});

// ===============================
// VISION STATUS MONITOR (REAL-TIME)
// ===============================
function startVisionStatusMonitor() {
  const statusDot = document.getElementById("vision-status-dot");
  const statusText = document.getElementById("vision-status-text");
  const statusMeta = document.getElementById("vision-status-meta");

  if (!statusDot || !statusText || !statusMeta) {
    console.warn("[VisionStatus] Status elements not found");
    return;
  }

  async function checkVisionHealth() {
    try {
      const response = await fetch(`${API_BASE}/health/vision`, {
        method: "GET",
        timeout: 3000
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const health = await response.json();
      console.log("[VisionStatus] Health response:", health);

      // Update UI based on health status and Ollama manager state
      const ollamaStatus = health.ollama_manager?.status || "unknown";

      if (health.vision_ready === true && health.provider === "openrouter") {
        // Ollama is unavailable, but images go to the OpenRouter vision model.
        statusDot.className = "vision-status-dot status-ready";
        statusText.textContent = "Vision: Ready";
        statusMeta.textContent = `cloud · ${String(health.model || "").split("/").pop()}`;
        statusMeta.title = `Local Ollama unavailable (${health.fallback_reason || "not running"}); using OpenRouter`;
      } else if (!health.ollama_reachable) {
        // Check if Ollama is starting
        if (ollamaStatus === "starting" || ollamaStatus === "unknown") {
          statusDot.className = "vision-status-dot status-warming";
          statusText.textContent = "Vision: Starting...";
          statusMeta.textContent = "Please wait";
        } else {
          // Ollama not reachable
          statusDot.className = "vision-status-dot status-offline";
          statusText.textContent = "Vision: Offline";
          statusMeta.textContent = health.ollama_manager?.status || "Connection failed";
        }
      } else if (health.vision_ready === true) {
        // Vision is ready - show GREEN status
        // Vision is ready - GREENLIGHT
        statusDot.className = "vision-status-dot status-ready";
        statusText.textContent = "Vision: Ready";
        statusMeta.textContent = health.model || "";
        console.log("[VisionStatus] Vision is READY - status green");
      } else if (!health.vision_ready) {
        // Model not available
        statusDot.className = "vision-status-dot status-connecting";
        statusText.textContent = "Vision: Model not found";
        statusMeta.textContent = health.last_error || "Run: ollama pull qwen3-vl:8b";
      }

    } catch (err) {
      // Backend not reachable
      statusDot.className = "vision-status-dot status-offline";
      statusText.textContent = "Backend Offline";
      statusMeta.textContent = "Start Sarah backend";
      console.warn("[VisionStatus] Health check failed:", err.message);
    }
  }

  // Show initial "checking" state (no more waiting for warmup)
  statusDot.className = "vision-status-dot status-checking";
  statusText.textContent = "Vision: Checking...";
  statusMeta.textContent = "Please wait";

  // Track if vision is ready for adaptive polling
  let visionReady = false;
  let pollTimerId = null;

  function isScreenTabVisible() {
    const tab = document.getElementById("screen-tab");
    if (!tab) return true;
    return !tab.classList.contains("hidden");
  }

  function nextVisionPollMs() {
    if (!visionReady) return 3000;
    return isScreenTabVisible() ? 30000 : 60000;
  }

  async function pollVisionLoop() {
    if (document.visibilityState === "visible") {
      const shouldPoll = isScreenTabVisible() || !visionReady;
      if (shouldPoll) {
        await checkVisionHealth();
        visionReady = statusDot.className.includes("status-ready");
      }
    }
    const delay = nextVisionPollMs();
    pollTimerId = setTimeout(pollVisionLoop, delay);
  }

  // Start health check after brief delay (allow backend to start)
  console.log("[VisionStatus] Starting health check in 1s...");
  setTimeout(async () => {
    await checkVisionHealth();
    visionReady = statusDot.className.includes("status-ready");
    pollTimerId = setTimeout(pollVisionLoop, nextVisionPollMs());
    console.log(`[VisionStatus] Monitor started (next poll in ${nextVisionPollMs()/1000}s)`);
  }, 1000);

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    if (pollTimerId) clearTimeout(pollTimerId);
    pollVisionLoop();
  });
}
