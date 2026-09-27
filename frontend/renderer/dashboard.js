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

// -----------------------------------------------------------------------------
// API CONFIG
// -----------------------------------------------------------------------------

const API_PORT = window.PY_PORT || 8907;
const API_BASE = `http://127.0.0.1:${API_PORT}`;

const ENDPOINTS = {
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

const sleep = (ms) => new Promise((res) => setTimeout(res, ms));

function sarahPerfStart(label) {
  if (!window.SARAH_PERF) return null;
  const start = performance.now();
  console.log(`[PERF] ${label}.start ${start.toFixed(1)}ms`);
  return start;
}

function sarahPerfEnd(label, start) {
  if (!window.SARAH_PERF || start == null) return;
  const elapsed = performance.now() - start;
  console.log(`[PERF] ${label}.done ${elapsed.toFixed(1)}ms`);
}

// SQLite CURRENT_TIMESTAMP / Python utcnow().isoformat() are UTC but carry no
// zone marker, and `new Date("2026-01-01 12:00:00")` parses that as *local*
// time — shifting every displayed timestamp by the UTC offset. Tag bare
// timestamps as UTC before parsing.
function parseDbTimestamp(value) {
  if (value instanceof Date) return value;
  const text = String(value ?? "").trim();
  if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(text)) {
    return new Date(`${text.replace(" ", "T")}Z`);
  }
  return new Date(text);
}

// Format a DB timestamp in the machine's own timezone (the name predates
// that; it used to hardcode America/Denver).
function formatMountainTime(dateString, includeTime = true) {
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

// -----------------------------------------------------------------------------
// BACKEND WRAPPER
// -----------------------------------------------------------------------------

class SarahBackend {
  constructor() {
    this.base = API_BASE;
    this.endpoints = ENDPOINTS;
  }

  async health() {
    try {
      const res = await fetch(this.endpoints.health);
      if (!res.ok) throw new Error("Health check failed");
      return await res.json();
    } catch (err) {
      console.warn("[Backend] Health failed:", err);
      return { ok: false };
    }
  }

  async chat(message, fromCreator = true, conversationId = null, regenerate = false) {
    const payload = { message, from_creator: fromCreator };
    if (conversationId != null) payload.conversation_id = conversationId;
    if (regenerate) payload.regenerate = true;  // Skip saving user message

    console.log("[Backend] Chat request - conversation_id:", conversationId, "payload:", JSON.stringify(payload));

    const res = await fetch(this.endpoints.chat, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) throw new Error("Chat failed");
    const result = await res.json();
    console.log("[Backend] Chat response received");
    return result;
  }

  // Streams /api/chat/stream (Server-Sent Events): calls onDelta(text) for
  // each chunk as the model writes and resolves with the final payload, which
  // has the same shape as /api/chat. Errors thrown with `streamUnavailable`
  // mean the endpoint doesn't exist (older backend) and nothing was sent, so
  // falling back to chat() is safe; any other failure may already have saved
  // the user's turn and must not be retried blindly.
  async chatStream(message, conversationId = null, { regenerate = false, onDelta } = {}) {
    const payload = { message, from_creator: true };
    if (conversationId != null) payload.conversation_id = conversationId;
    if (regenerate) payload.regenerate = true;

    const res = await fetch(`${this.base}/api/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok || !res.body) {
      const err = new Error(`Chat stream failed: HTTP ${res.status}`);
      err.streamUnavailable = res.status === 404 || res.status === 405;
      throw err;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let final = null;
    for (;;) {
      const { value, done } = await reader.read();
      if (value) buffer += decoder.decode(value, { stream: true });
      let sep;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        let event = "message";
        const data = [];
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
        }
        if (!data.length) continue;
        const parsed = JSON.parse(data.join("\n"));
        if (event === "delta") onDelta?.(parsed.text || "");
        else if (event === "done") final = parsed;
        else if (event === "error") throw new Error(parsed.detail || "Chat stream error");
      }
      if (done) break;
    }
    if (!final) throw new Error("Chat stream ended without a reply");
    return final;
  }

  async getLLMMode() {
    const res = await fetch(this.endpoints.llmMode);
    if (!res.ok) throw new Error("Failed LLM mode");
    return res.json();
  }

  async setLLMMode(mode, localModel = null) {
    const payload = { mode };
    if (localModel) payload.local_model = localModel;

    const res = await fetch(this.endpoints.llmMode, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) throw new Error("Failed to set mode");
    return res.json();
  }

  async getContextInfo(conversationId = null) {
    const url = conversationId
      ? `${this.endpoints.contextInfo}/${conversationId}`
      : this.endpoints.contextInfo;
    const res = await fetch(url);
    if (!res.ok) throw new Error("Failed to get context info");
    return res.json();
  }

  async getAvatarContextWindow(conversationId) {
    if (conversationId == null) return null;
    const res = await fetch(`${this.endpoints.avatarContextWindow}/${conversationId}`);
    if (!res.ok) throw new Error("Failed to get avatar context window");
    return res.json();
  }

  async tts(text, opts = {}) {
    const body = { text };
    // Issue #18: forward Piper prosody knobs so emotion/intensity actually
    // affects how Sarah sounds. Backend defaults are used when fields are
    // absent (length_scale=1.0, noise_scale=0.6, noise_w=0.8).
    if (Number.isFinite(opts.length_scale)) body.length_scale = opts.length_scale;
    if (Number.isFinite(opts.noise_scale)) body.noise_scale = opts.noise_scale;
    if (Number.isFinite(opts.noise_w)) body.noise_w = opts.noise_w;

    const res = await fetch(this.endpoints.tts, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!res.ok) throw new Error("TTS failed");
    const blob = await res.blob();
    return URL.createObjectURL(blob);
  }

  async stt(audioB64, mimeType = "audio/webm") {
    console.log("[Backend] Sending STT request to:", this.endpoints.stt);
    console.log("[Backend] Audio size:", audioB64.length, "MIME:", mimeType);

    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();

      xhr.timeout = 60000; // 60 second timeout

      xhr.onload = () => {
        console.log("[Backend] STT response status:", xhr.status, xhr.statusText);
        if (xhr.status === 200) {
          try {
            const result = JSON.parse(xhr.responseText);
            console.log("[Backend] STT result:", result);
            resolve(result);
          } catch (err) {
            console.error("[Backend] Failed to parse STT response:", err);
            reject(new Error("Failed to parse response"));
          }
        } else {
          console.error("[Backend] STT error response:", xhr.responseText);
          reject(new Error("STT failed: " + xhr.statusText));
        }
      };

      xhr.onerror = () => {
        console.error("[Backend] STT request network error");
        reject(new Error("Network error"));
      };

      xhr.ontimeout = () => {
        console.error("[Backend] STT request timed out after 60 seconds");
        reject(new Error("Request timed out"));
      };

      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          console.log("[Backend] Upload progress:", Math.round((e.loaded / e.total) * 100) + "%");
        }
      };

      xhr.open("POST", this.endpoints.stt, true);
      xhr.setRequestHeader("Content-Type", "application/json");

      console.log("[Backend] Sending request...");
      xhr.send(JSON.stringify({ audio_b64: audioB64, mime_type: mimeType }));
    });
  }

  async checkWake() {
    try {
      const res = await fetch(this.endpoints.wake);
      if (!res.ok) return { wake: false };
      const data = await res.json();
      if (data.wake) {
        console.log("[Backend] Wake response:", JSON.stringify(data));
      }
      return data;
    } catch (err) {
      console.warn("[Backend] Wake poll failed:", err);
      return { wake: false, error: err?.message };
    }
  }

  async getWakeDiagnostics() {
    try {
      const res = await fetch(this.endpoints.wakeDiagnostics);
      if (!res.ok) return { ok: false, error: `HTTP ${res.status}` };
      return await res.json();
    } catch (err) {
      console.warn("[Backend] Wake diagnostics failed:", err);
      return { ok: false, error: err?.message || "unreachable" };
    }
  }

  // Conversations
  async listConversations() {
    const res = await fetch(this.endpoints.conversations);
    if (!res.ok) throw new Error("List conversations failed");
    const data = await res.json();
    return data.conversations || [];
  }

  async createConversation(title = null) {
    const res = await fetch(this.endpoints.conversations, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    if (!res.ok) throw new Error("Create conversation failed");
    const data = await res.json();
    return data.conversation_id || data.id;
  }

  async getConversationMessages(conversationId, limit = 200) {
    const res = await fetch(
      `${this.endpoints.conversations}/${conversationId}/messages?limit=${limit}`
    );
    if (!res.ok) throw new Error("Messages failed");
    const data = await res.json();
    return data.messages || [];
  }

  // 🔥 DELETE a conversation
  async deleteConversation(conversationId) {
    const res = await fetch(
      `${this.endpoints.conversations}/${conversationId}`,
      {
        method: "DELETE",
      }
    );
    if (!res.ok) throw new Error("Failed to delete conversation");
    return res.json();
  }

  // 🔥 RENAME a conversation
  async renameConversation(conversationId, title) {
    const res = await fetch(
      `${this.endpoints.conversations}/${conversationId}/title`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      }
    );
    if (!res.ok) throw new Error("Failed to rename conversation");
    return res.json();
  }

  // Message actions
  async updateMessage(messageId, content) {
    const res = await fetch(`${this.base}/api/messages/${messageId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    if (!res.ok) throw new Error("Failed to update message");
    return res.json();
  }

  async deleteMessage(messageId) {
    const res = await fetch(`${this.base}/api/messages/${messageId}`, {
      method: "DELETE",
    });
    if (!res.ok) throw new Error("Failed to delete message");
    return res.json();
  }

  async deleteMessagesAfter(conversationId, messageId) {
    const res = await fetch(
      `${this.endpoints.conversations}/${conversationId}/messages/after/${messageId}`,
      { method: "DELETE" }
    );
    if (!res.ok) throw new Error("Failed to delete messages");
    return res.json();
  }

  async searchMessages(query, limit = 50) {
    const url = new URL(`${this.base}/api/messages/search`);
    url.searchParams.set("q", query);
    url.searchParams.set("limit", limit);
    const res = await fetch(url);
    if (!res.ok) throw new Error("Search failed");
    return res.json();
  }

  async pinMessage(messageId, pinned = true) {
    const res = await fetch(`${this.base}/api/messages/${messageId}/pin`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pinned }),
    });
    if (!res.ok) throw new Error("Failed to pin message");
    return res.json();
  }

  async getPinnedMessages(limit = 50) {
    const res = await fetch(`${this.base}/api/messages/pinned?limit=${limit}`);
    if (!res.ok) throw new Error("Failed to get pinned messages");
    return res.json();
  }

  // Skills
  async listSkills() {
    const res = await fetch(this.endpoints.skills);
    if (!res.ok) throw new Error("Failed to list skills");
    const data = await res.json();
    return data.skills || [];
  }

  async discoverSkills() {
    const res = await fetch(`${this.endpoints.skills}/discover`, {
      method: "POST",
    });
    if (!res.ok) throw new Error("Failed to discover skills");
    return res.json();
  }

  async setSkillEnabled(slug, enabled) {
    const action = enabled ? "enable" : "disable";
    const res = await fetch(
      `${this.endpoints.skills}/${encodeURIComponent(slug)}/${action}`,
      { method: "POST" }
    );
    if (!res.ok) throw new Error(`Failed to ${action} skill`);
    return res.json();
  }

  async getSkillManifest(slug) {
    const res = await fetch(
      `${this.endpoints.skills}/${encodeURIComponent(slug)}/manifest`
    );
    if (!res.ok) throw new Error("Failed to get skill manifest");
    return res.json();
  }

  async registerSkill(payload) {
    const res = await fetch(`${this.endpoints.skills}/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error("Failed to register skill");
    return res.json();
  }

  // Mood State
  async getMood(conversationId) {
    const res = await fetch(
      `${this.endpoints.conversations}/${conversationId}/mood`
    );
    if (!res.ok) throw new Error("Get mood failed");
    return res.json();
  }

  async updateMood(conversationId, moodUpdate) {
    const res = await fetch(
      `${this.endpoints.conversations}/${conversationId}/mood`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(moodUpdate),
      }
    );
    if (!res.ok) throw new Error("Update mood failed");
    return res.json();
  }

  async resetMood(conversationId) {
    const res = await fetch(
      `${this.endpoints.conversations}/${conversationId}/mood/reset`,
      { method: "POST" }
    );
    if (!res.ok) throw new Error("Reset mood failed");
    return res.json();
  }

  // Timezone
  async getTimezone(conversationId) {
    const res = await fetch(
      `${this.endpoints.conversations}/${conversationId}/timezone`
    );
    if (!res.ok) throw new Error("Get timezone failed");
    return res.json();
  }

  async setTimezone(conversationId, timezone) {
    const res = await fetch(
      `${this.endpoints.conversations}/${conversationId}/timezone`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ timezone }),
      }
    );
    if (!res.ok) throw new Error("Set timezone failed");
    return res.json();
  }

  async getLocalTime(timezone = null) {
    const url = new URL(`${this.base}/api/time/local`);
    if (timezone) url.searchParams.set("timezone", timezone);
    const res = await fetch(url);
    if (!res.ok) throw new Error("Get local time failed");
    return res.json();
  }

  // Memories
  async listMemories(keyword = "", limit = 50) {
    const url = new URL(this.endpoints.memories);
    if (keyword) url.searchParams.set("keyword", keyword);
    else url.searchParams.set("limit", limit);

    const res = await fetch(url);
    if (!res.ok) throw new Error("Memories failed");
    const data = await res.json();
    return data.memories || [];
  }

  async listPinnedMemories(limit = 100) {
    const url = new URL(this.endpoints.pinnedMemories);
    url.searchParams.set("limit", limit);
    const res = await fetch(url);
    if (!res.ok) throw new Error("Pinned memories failed");
    const data = await res.json();
    return data.memories || [];
  }

  async setMemoryPinned(memoryId, pinned) {
    const res = await fetch(
      `${this.base}/api/memories/${memoryId}/${pinned ? "pin" : "unpin"}`,
      { method: "POST" }
    );
    if (!res.ok) throw new Error("Pin update failed");
    return res.json();
  }

  // Projects
  async listProjects() {
    const res = await fetch(this.endpoints.projects);
    if (!res.ok) throw new Error("List projects failed");
    const data = await res.json();
    return data.projects || [];
  }

  async createProject(name, description = null, rootPath = null) {
    const res = await fetch(this.endpoints.projects, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description, root_path: rootPath }),
    });
    if (!res.ok) throw new Error("Create project failed");
    const data = await res.json();
    return data.project_id;
  }

  async getProject(projectId) {
    const res = await fetch(`${this.endpoints.projects}/${projectId}`);
    if (!res.ok) throw new Error("Get project failed");
    return res.json();
  }

  async deleteProject(projectId) {
    const res = await fetch(`${this.endpoints.projects}/${projectId}`, {
      method: "DELETE",
    });
    if (!res.ok) throw new Error("Delete project failed");
    return res.json();
  }

  async renameProject(projectId, newName) {
    const res = await fetch(`${this.endpoints.projects}/${projectId}/rename`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: newName }),
    });
    if (!res.ok) throw new Error("Rename project failed");
    return res.json();
  }

  async analyzeProject(projectId) {
    const res = await fetch(`${this.endpoints.projects}/${projectId}/analyze`);
    if (!res.ok) throw new Error("Analyze project failed");
    const data = await res.json();
    return data.analysis || {};
  }

  async getProjectFiles(projectId) {
    const res = await fetch(`${this.endpoints.projects}/${projectId}/files`);
    if (!res.ok) throw new Error("Get project files failed");
    const data = await res.json();
    return data.files || [];
  }

  async uploadFileToProject(projectId, fileData) {
    const res = await fetch(`${this.endpoints.projects}/${projectId}/files`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fileData),
    });
    if (!res.ok) throw new Error("Upload file failed");
    const data = await res.json();
    return data.file_id;
  }

  async deleteFile(projectId, fileId) {
    const res = await fetch(
      `${this.endpoints.projects}/${projectId}/files/${fileId}`,
      { method: "DELETE" }
    );
    if (!res.ok) throw new Error("Delete file failed");
    return res.json();
  }

  async getProjectContext(projectId) {
    const res = await fetch(
      `${this.endpoints.projects}/${projectId}/context`
    );
    if (!res.ok) throw new Error("Get project context failed");
    const data = await res.json();
    return data.context || "";
  }

  // Git operations
  async getGitStatus(projectId) {
    const res = await fetch(
      `${this.endpoints.projects}/${projectId}/git/status`
    );
    if (!res.ok) throw new Error("Get git status failed");
    return res.json();
  }

  async getRecentCommits(projectId, limit = 10) {
    const res = await fetch(
      `${this.endpoints.projects}/${projectId}/git/commits?limit=${limit}`
    );
    if (!res.ok) throw new Error("Get commits failed");
    const data = await res.json();
    return data.commits || [];
  }

  async getGitDiff(projectId, filePath = null) {
    const url = new URL(
      `${this.endpoints.projects}/${projectId}/git/diff`
    );
    if (filePath) url.searchParams.set("file_path", filePath);

    const res = await fetch(url);
    if (!res.ok) throw new Error("Get git diff failed");
    const data = await res.json();
    return data.diff || "";
  }

  async gitAdd(projectId, files = null) {
    const res = await fetch(
      `${this.endpoints.projects}/${projectId}/git/add`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ files }),
      }
    );
    if (!res.ok) throw new Error("Git add failed");
    return res.json();
  }

  async gitCommit(projectId, message) {
    const res = await fetch(
      `${this.endpoints.projects}/${projectId}/git/commit`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      }
    );
    if (!res.ok) throw new Error("Git commit failed");
    return res.json();
  }

  async gitPush(projectId, remote = "origin", branch = null) {
    const res = await fetch(
      `${this.endpoints.projects}/${projectId}/git/push`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ remote, branch }),
      }
    );
    if (!res.ok) throw new Error("Git push failed");
    return res.json();
  }

  async gitPull(projectId, remote = "origin", branch = null) {
    const res = await fetch(
      `${this.endpoints.projects}/${projectId}/git/pull`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ remote, branch }),
      }
    );
    if (!res.ok) throw new Error("Git pull failed");
    return res.json();
  }

  async gitStash(projectId, action = "push", message = null) {
    const res = await fetch(
      `${this.endpoints.projects}/${projectId}/git/stash`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, message }),
      }
    );
    if (!res.ok) throw new Error("Git stash failed");
    return res.json();
  }

  async gitBranches(projectId) {
    const res = await fetch(
      `${this.endpoints.projects}/${projectId}/git/branches`
    );
    if (!res.ok) throw new Error("Get branches failed");
    return res.json();
  }

  async gitCheckout(projectId, branch, create = false) {
    const res = await fetch(
      `${this.endpoints.projects}/${projectId}/git/checkout`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ branch, create }),
      }
    );
    if (!res.ok) throw new Error("Git checkout failed");
    return res.json();
  }

  // File tree and metadata
  async scanProjectFolder(rootPath) {
    const res = await fetch(
      `${this.endpoints.projects}/scan?root_path=${encodeURIComponent(rootPath)}`,
      { method: "POST" }
    );
    if (!res.ok) throw new Error("Scan project failed");
    return res.json();
  }

  async getFileTree(projectId) {
    const res = await fetch(
      `${this.endpoints.projects}/${projectId}/tree`
    );
    if (!res.ok) throw new Error("Get file tree failed");
    const data = await res.json();
    return data.tree || {};
  }

  // Current file tracking
  async setCurrentFile(projectId, fileId) {
    const res = await fetch(
      `${this.endpoints.projects}/${projectId}/current_file/${fileId}`,
      { method: "POST" }
    );
    if (!res.ok) throw new Error("Set current file failed");
    return res.json();
  }

  async getCurrentFile(projectId) {
    const res = await fetch(
      `${this.endpoints.projects}/${projectId}/current_file`
    );
    if (!res.ok) throw new Error("Get current file failed");
    const data = await res.json();
    return data.file || null;
  }

  // Enhanced file operations
  async createFile(projectId, fileName, content = "", filePath = null) {
    const res = await fetch(
      `${this.endpoints.projects}/${projectId}/files/create`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_name: fileName, content, file_path: filePath }),
      }
    );
    if (!res.ok) throw new Error("Create file failed");
    const data = await res.json();
    return data.file_id;
  }

  async updateFile(projectId, fileId, content) {
    const res = await fetch(
      `${this.endpoints.projects}/${projectId}/files/${fileId}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      }
    );
    if (!res.ok) throw new Error("Update file failed");
    return res.json();
  }

  async renameFile(projectId, fileId, newName) {
    const res = await fetch(
      `${this.endpoints.projects}/${projectId}/files/${fileId}/rename`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ new_name: newName }),
      }
    );
    if (!res.ok) throw new Error("Rename file failed");
    return res.json();
  }

  // Conversation tagging
  async getProjectConversations(projectId) {
    const res = await fetch(
      `${this.endpoints.projects}/${projectId}/conversations`
    );
    if (!res.ok) throw new Error("Get project conversations failed");
    const data = await res.json();
    return data.conversations || [];
  }

  async tagConversation(projectId, conversationId) {
    const res = await fetch(
      `${this.endpoints.projects}/${projectId}/conversations/tag`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: conversationId }),
      }
    );
    if (!res.ok) throw new Error("Tag conversation failed");
    return res.json();
  }

  async untagConversation(projectId, conversationId) {
    const res = await fetch(
      `${this.endpoints.projects}/${projectId}/conversations/${conversationId}`,
      { method: "DELETE" }
    );
    if (!res.ok) throw new Error("Untag conversation failed");
    return res.json();
  }

  async getConversationProjects(conversationId) {
    const res = await fetch(
      `${this.base}/api/conversations/${conversationId}/projects`
    );
    if (!res.ok) throw new Error("Get conversation projects failed");
    const data = await res.json();
    return data.projects || [];
  }
}

// -----------------------------------------------------------------------------
// TTS
// -----------------------------------------------------------------------------

class SarahTTS {
  constructor(backend) {
    this.backend = backend;
    this.voiceEnabled =
      localStorage.getItem("sarah.voice.enabled") === "false" ? false : true;
    this.currentAudio = null;
    this.audioContext = null;
    this.lipSyncFrame = null;
    this.lipSyncSource = null;
    this.lipSyncAnalyser = null;
    this.visemeTimeline = [];
    // Half-duplex gate: when TTS is playing (or in the post-playback tail),
    // the renderer must not poll backend wake or process any STT capture —
    // otherwise the mic picks up Sarah's own voice and self-replies fire.
    this._ttsPlaying = false;
    this._ttsMuteUntil = 0;
    this._ttsTailMs = 1500;
    this._streamGeneration = 0;
  }

  isEnabled() {
    return this.voiceEnabled;
  }

  setEnabled(enabled) {
    this.voiceEnabled = enabled;
    localStorage.setItem("sarah.voice.enabled", enabled ? "true" : "false");
  }

  isMuting() {
    return this._ttsPlaying || Date.now() < this._ttsMuteUntil;
  }

  stop() {
    this._streamGeneration += 1; // invalidates any createStream() in flight
    if (this.currentAudio) {
      this.currentAudio.pause();
      this.currentAudio = null;
    }
    this._ttsPlaying = false;
    this._ttsMuteUntil = Date.now() + this._ttsTailMs;
    this._stopLive2DLipSync();
  }

  _stopLive2DLipSync() {
    if (this.lipSyncFrame) {
      cancelAnimationFrame(this.lipSyncFrame);
      this.lipSyncFrame = null;
    }

    try {
      this.lipSyncSource?.disconnect();
    } catch {
      // MediaElementAudioSourceNode can throw if already disconnected.
    }
    try {
      this.lipSyncAnalyser?.disconnect();
    } catch {
      // Analyzer cleanup is best-effort.
    }

    this.lipSyncSource = null;
    this.lipSyncAnalyser = null;
    window.SARAH_LIVE2D?.clearVoiceLevel?.();
    window.SARAH_LIVE2D?.clearVisemeTimeline?.();
    window.SARAH_LIVE2D?.setAvatarMode?.("idle", { source: "tts-stop" });
  }

  _buildApproxVisemeTimeline(text) {
    const modularTimeline = window.SARAH_AVATAR_SYSTEM?.buildVisemeTimeline?.(text, {
      source: "tts",
    });
    if (Array.isArray(modularTimeline) && modularTimeline.length) {
      return modularTimeline;
    }

    const timeline = [];
    const source = String(text || "").toLowerCase().slice(0, 360);
    const vowelMap = {
      a: "A",
      e: "E",
      i: "I",
      y: "I",
      o: "O",
      u: "U",
    };
    let time = 0;

    for (const ch of source) {
      if (/\s/.test(ch)) {
        time += 0.05;
        continue;
      }

      const viseme = vowelMap[ch] || ("bmp".includes(ch) ? "closed" : "neutral");
      timeline.push({ time, viseme, duration: 0.09 });
      time += /[.!?]/.test(ch) ? 0.16 : 0.075;

      if (timeline.length >= 220) break;
    }

    if (!timeline.length) {
      timeline.push({ time: 0, viseme: "neutral", duration: 0.12 });
    }
    timeline.push({ time: time + 0.04, viseme: "closed", duration: 0.12 });
    return timeline;
  }

  async _startLive2DLipSync(audio, text = "") {
    if (!window.SARAH_LIVE2D?.setVoiceLevel) return;
    const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextCtor) return;

    try {
      this.audioContext = this.audioContext || new AudioContextCtor();
      if (this.audioContext.state === "suspended") {
        await this.audioContext.resume();
      }

      const source = this.audioContext.createMediaElementSource(audio);
      const analyser = this.audioContext.createAnalyser();
      analyser.fftSize = 1024;
      source.connect(analyser);
      analyser.connect(this.audioContext.destination);

      const data = new Uint8Array(analyser.fftSize);
      this.lipSyncSource = source;
      this.lipSyncAnalyser = analyser;

      const tick = () => {
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i += 1) {
          const v = (data[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / data.length);
        window.SARAH_LIVE2D?.setVoiceLevel?.(Math.min(1, rms * 5));
        if (!audio.ended && !audio.paused) {
          this.lipSyncFrame = requestAnimationFrame(tick);
        }
      };

      audio.addEventListener("play", () => {
        window.SARAH_LIVE2D?.setAvatarMode?.("speaking", { source: "tts" });
        window.SARAH_LIVE2D?.applyVisemeTimeline?.(
          this._buildApproxVisemeTimeline(text),
          { source: "tts-text" }
        );
        this.lipSyncFrame = requestAnimationFrame(tick);
      }, { once: true });
      audio.addEventListener("ended", () => this._stopLive2DLipSync(), { once: true });
      audio.addEventListener("pause", () => {
        if (!audio.ended) window.SARAH_LIVE2D?.setVoiceLevel?.(0);
      });
    } catch (err) {
      console.warn("[TTS] Live2D lip sync unavailable:", err);
      this._stopLive2DLipSync();
    }
  }

  // Issue #18: map emotion/intensity → Piper prosody (length_scale, noise_w).
  // Higher intensity → faster + more variation. "sad" pulls toward slower +
  // quieter, "joy/excited" toward faster + livelier.
  _prosodyFor(emotion, intensity) {
    const i = Math.max(0, Math.min(1, Number(intensity) || 0));
    let length = 1.0 - (i - 0.5) * 0.18;        // i=0 → 1.09, i=1 → 0.91
    let noiseW = 0.7 + i * 0.35;                // i=0 → 0.70, i=1 → 1.05
    let noiseScale = 0.55 + i * 0.18;           // i=0 → 0.55, i=1 → 0.73

    const e = String(emotion || "").toLowerCase();
    if (/sad|melanchol|tired|down/.test(e))      { length += 0.06; noiseW -= 0.08; }
    else if (/joy|happy|excited|playful/.test(e)){ length -= 0.04; noiseW += 0.05; }
    else if (/angry|annoyed|frustrat/.test(e))   { length -= 0.03; noiseScale += 0.05; }
    else if (/calm|gentle|warm/.test(e))         { length += 0.02; noiseW -= 0.03; }

    return {
      length_scale: Math.max(0.7, Math.min(1.3, length)),
      noise_scale: Math.max(0.3, Math.min(0.9, noiseScale)),
      noise_w: Math.max(0.4, Math.min(1.2, noiseW)),
    };
  }

  _ttsOptions(opts = {}) {
    return (opts.emotion != null || opts.intensity != null)
      ? this._prosodyFor(opts.emotion, opts.intensity)
      : undefined;
  }

  // Start playing one synthesized clip. Resolves once playback has started
  // with a promise that settles when the clip ends (or is stopped/fails).
  async _startClip(url, text) {
    const audio = new Audio(url);
    // Issue #28: greetings ("Hello") were getting clipped at the start
    // because audio.play() fired before the element had decoded its first
    // PCM samples. Force eager preload and wait until the buffer has enough
    // data to play through (readyState >= HAVE_FUTURE_DATA) before starting
    // playback. Capped at 250ms so a slow decode never deadlocks the call.
    audio.preload = "auto";
    this.currentAudio = audio;
    audio.addEventListener("play", () => {
      this._ttsPlaying = true;
    }, { once: true });
    const release = () => {
      this._ttsPlaying = false;
      this._ttsMuteUntil = Date.now() + this._ttsTailMs;
    };
    audio.addEventListener("ended", release, { once: true });
    audio.addEventListener("error", release, { once: true });
    const ended = new Promise((resolve) => {
      for (const evt of ["ended", "error", "pause"]) {
        audio.addEventListener(evt, () => resolve(), { once: true });
      }
    });
    await this._startLive2DLipSync(audio, text);
    await new Promise((resolve) => {
      if (audio.readyState >= 3) {
        resolve();
        return;
      }
      const done = () => {
        audio.removeEventListener("canplay", done);
        audio.removeEventListener("canplaythrough", done);
        resolve();
      };
      audio.addEventListener("canplay", done, { once: true });
      audio.addEventListener("canplaythrough", done, { once: true });
      setTimeout(done, 250);
    });
    const tPlay = performance.now();
    await audio.play();
    if (window.B6_TIMING) console.log(`[TIMING] stage=audio.playback_start ms=${Math.round(performance.now() - tPlay)}`);
    return ended;
  }

  async speak(text, opts = {}) {
    if (!this.voiceEnabled) return;
    this.stop();

    const t0 = performance.now();
    try {
      const url = await this.backend.tts(text, this._ttsOptions(opts));
      if (window.B6_TIMING) console.log(`[TIMING] stage=tts.response_complete ms=${Math.round(performance.now() - t0)} text_len=${text.length}`);
      await this._startClip(url, text);
    } catch (err) {
      console.warn("TTS failed:", err);
      this._ttsPlaying = false;
      this._ttsMuteUntil = Date.now() + this._ttsTailMs;
    }
  }

  // Speak a reply while it is still being written. push(preview) takes the
  // whole cleaned text so far; each finished sentence is synthesized as soon
  // as it appears (the next one while the current one plays) and clips play
  // in order. finish(finalText, voiceOpts) speaks whatever is left. Returns
  // null when voice is off. Any stop() (new reply, voice toggled) cancels it.
  createStream(opts = {}) {
    if (!this.voiceEnabled) return null;
    this.stop();
    const generation = this._streamGeneration;
    const norm = (s) => String(s || "").replace(/\s+/g, " ").trim();
    const boundary = /[.!?…]+["')\]]*(?=\s)|\n{2,}/g;
    let voiceOpts = opts;
    let spokenText = "";
    let pending = "";
    const queue = [];
    let playing = false;
    const stale = () => generation !== this._streamGeneration;

    const pump = async () => {
      playing = true;
      while (queue.length && !stale()) {
        const { text, urlPromise } = queue.shift();
        const url = await urlPromise;
        if (!url || stale()) continue;
        try {
          const ended = await this._startClip(url, text);
          await ended;
        } catch (err) {
          console.warn("[TTS] Stream clip failed:", err);
        }
      }
      playing = false;
    };

    const enqueue = (segment) => {
      const text = norm(segment);
      if (!text || stale()) return;
      const urlPromise = this.backend.tts(text, this._ttsOptions(voiceOpts)).catch((err) => {
        console.warn("[TTS] Stream synthesis failed:", err);
        return null;
      });
      queue.push({ text, urlPromise });
      if (!playing) pump();
    };

    // Cut complete sentences off `pending`; the first clip may be short so
    // speech starts quickly, later ones are batched to fewer requests.
    const drain = () => {
      for (;;) {
        const minLen = spokenText ? 60 : 20;
        boundary.lastIndex = 0;
        let cut = -1;
        let m;
        while ((m = boundary.exec(pending))) {
          const end = m.index + m[0].length;
          if (end >= minLen) { cut = end; break; }
        }
        if (cut < 0) return;
        const segment = pending.slice(0, cut);
        pending = pending.slice(cut);
        spokenText += segment;
        enqueue(segment);
      }
    };

    return {
      push: (preview) => {
        if (stale()) return;
        const full = String(preview || "");
        const consumed = spokenText.length + pending.length;
        if (full.length > consumed && full.startsWith(spokenText + pending)) {
          pending += full.slice(consumed);
          drain();
        }
      },
      finish: (finalText, finalOpts = {}) => {
        if (stale()) return;
        if (finalOpts.emotion != null || finalOpts.intensity != null) voiceOpts = finalOpts;
        const finalNorm = norm(finalText);
        const spokenNorm = norm(spokenText);
        if (finalNorm.startsWith(spokenNorm)) {
          enqueue(finalNorm.slice(spokenNorm.length));
        } else if (!spokenNorm) {
          enqueue(finalNorm);
        }
        // Otherwise the cleaned final text diverged from what was already
        // spoken; skip the tail rather than repeat or garble it.
        pending = "";
      },
      cancel: () => {
        if (!stale()) this.stop();
      },
    };
  }
}

// -----------------------------------------------------------------------------
// Issue #26 — Slash-command palette
// Lightweight popover that opens when the user types "/" at the start of the
// chat input and filters as they keep typing. Selecting a command (Enter / Tab
// / click) runs its action and clears the input.
// -----------------------------------------------------------------------------
class SarahSlashPalette {
  constructor(input, commands) {
    this.input = input;
    this.commands = commands || [];
    this.filtered = [];
    this.selectedIdx = 0;
    this.open = false;
    this._buildDom();
    this._bind();
  }

  _buildDom() {
    const el = document.createElement("div");
    // All visual styling lives in `.slash-palette` (a1-components.css) so the
    // palette inherits theme tokens. Only runtime geometry is set inline below.
    el.className = "slash-palette";
    document.body.appendChild(el);
    this.el = el;
  }

  _bind() {
    this.input.addEventListener("input", () => this._sync());
    this.input.addEventListener("keydown", (e) => this._onKeydown(e), true);
    this.input.addEventListener("blur", () => setTimeout(() => this._hide(), 120));
    document.addEventListener("click", (e) => {
      if (this.open && !this.el.contains(e.target) && e.target !== this.input) {
        this._hide();
      }
    });
  }

  _sync() {
    const value = this.input.value || "";
    if (!value.startsWith("/")) {
      this._hide();
      return;
    }
    const query = value.slice(1).toLowerCase().trim();
    this.filtered = this.commands.filter((c) => {
      if (!query) return true;
      if (c.name.toLowerCase().includes(query)) return true;
      return (c.aliases || []).some((a) => a.toLowerCase().includes(query));
    });
    if (!this.filtered.length) {
      this._hide();
      return;
    }
    this.selectedIdx = 0;
    this._render();
    this._show();
  }

  _onKeydown(e) {
    if (!this.open) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      e.stopPropagation();
      this.selectedIdx = (this.selectedIdx + 1) % this.filtered.length;
      this._render();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      e.stopPropagation();
      this.selectedIdx = (this.selectedIdx - 1 + this.filtered.length) % this.filtered.length;
      this._render();
    } else if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      e.stopPropagation();
      this._select(this.selectedIdx);
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      this._hide();
    }
  }

  _select(idx) {
    const cmd = this.filtered[idx];
    if (!cmd) return;
    this._hide();
    this.input.value = "";
    try {
      Promise.resolve(cmd.run()).catch((err) => console.warn("[Slash] failed:", err));
    } catch (err) {
      console.warn("[Slash] Command failed:", err);
    }
  }

  _render() {
    this.el.innerHTML = this.filtered
      .map((c, i) => {
        const sel = i === this.selectedIdx ? " is-selected" : "";
        const aliasText = (c.aliases && c.aliases.length) ? `  (${c.aliases.map(a => "/" + a).join(", ")})` : "";
        return `<div data-idx="${i}" class="slash-palette-row${sel}">
          <div class="slash-palette-name">/${c.name}<span class="slash-palette-alias">${aliasText}</span></div>
          <div class="slash-palette-desc">${c.description || ""}</div>
        </div>`;
      })
      .join("");
    this.el.querySelectorAll("div[data-idx]").forEach((row) => {
      row.addEventListener("mousedown", (e) => {
        e.preventDefault();
        this._select(parseInt(row.dataset.idx, 10));
      });
    });
  }

  _show() {
    this.open = true;
    const r = this.input.getBoundingClientRect();
    this.el.style.left = `${Math.max(8, r.left)}px`;
    this.el.style.bottom = `${Math.max(8, window.innerHeight - r.top + 6)}px`;
    this.el.style.width = `${Math.max(r.width, 320)}px`;
    this.el.style.display = "block";
  }

  _hide() {
    this.open = false;
    this.el.style.display = "none";
  }
}

// -----------------------------------------------------------------------------
// MULTIMODAL ATTACHMENT REGISTRY
// -----------------------------------------------------------------------------

window.SARAH_MULTIMODAL = window.SARAH_MULTIMODAL || {};

function registerAttachment(kind, payload) {
  const id = `att_${Date.now()}_${Math.random().toString(16).slice(2)}`;
  window.SARAH_MULTIMODAL[id] = { kind, payload, ts: Date.now() };
  return id;
}

function openAttachment(id) {
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

  _knownMotionNames() {
    return new Set([
      "wave",
      "arms_up",
      "thinking_pose",
      "spin",
      "shrug",
      "point",
      "hand_to_chest",
      "lean_in",
      "step_back",
      "nod",
      "shake_head",
      "jump",
      "crouch",
      "blush",
      ...(window.SARAH_AVATAR_SYSTEM?.listGestures?.() || []),
    ]);
  }

  // Cleanup for text that is still streaming in: hides motion tags (complete
  // or half-typed) and <think> blocks WITHOUT dispatching gestures. The final
  // reply goes through _sanitizeAssistantDisplayText exactly once.
  _previewAssistantText(raw) {
    const known = this._knownMotionNames();
    return String(raw || "")
      .replace(/<think\b[^>]*>[\s\S]*?(?:<\/think>|$)/gi, "")
      .replace(/<motion\b[^>]*>[^<]*(?:<\/motion>)?/gi, "")
      .replace(/<(\/?)([\w_-]+)\s*\/?>/gi, (match, _closing, tagName) =>
        known.has(String(tagName || "").toLowerCase()) ? "" : match
      )
      .replace(/<[^>\n]*$/, "")
      .trimStart();
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

    const onDelta = (text) => {
      raw += text;
      const preview = this._previewAssistantText(raw);
      if (!bubble) {
        if (!preview.trim()) return;
        this._hideLoadingIndicator(loadingEl);
        bubble = this.appendMessage("assistant", "…");
        textEl = bubble?.querySelector(".chat-bubble-text");
        bubble?.classList.add("is-streaming");
      }
      if (textEl) textEl.textContent = preview;
      this._scrollToBottom();
      speech?.push(preview);
    };

    try {
      const resp = await this.backend.chatStream(message, this.activeConversationId, { regenerate, onDelta });
      bubble?.classList.remove("is-streaming");
      return { resp, bubble, speech };
    } catch (err) {
      speech?.cancel();
      bubble?.classList.remove("is-streaming");
      if (!err?.streamUnavailable || bubble) throw err;
      console.warn("[Chat] Streaming endpoint unavailable, using /api/chat:", err);
      const resp = await this.backend.chat(message, true, this.activeConversationId, regenerate);
      return { resp, bubble: null, speech: null };
    }
  }

  // Put the final reply into the streamed bubble (or a new one), dispatching
  // its motion tags once. Returns the visible reply text.
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

  // Voice for the finished reply: complete the TTS stream if one ran,
  // otherwise speak the whole reply (or return the avatar to idle).
  _speakFinalReply(reply, resp, speech, source) {
    const voice = { emotion: resp?.emotion, intensity: resp?.emotion_intensity };
    this._lastReplyVoice = voice;
    if (speech) {
      speech.finish(reply, voice);
    } else if (this.tts.isEnabled()) {
      this.tts.speak(reply, voice).catch((err) => console.warn("[TTS] Failed:", err));
    } else {
      this._setAvatarMode("idle", { source });
    }
  }

  _sanitizeAssistantDisplayText(text) {
    // Issue #33: extract <motion name="..."> / <motion>name</motion>
    // tokens before stripping. Each tag dispatches to the avatar system
    // and is removed from the visible reply.
    const raw = String(text || "");
    const knownMotionNames = this._knownMotionNames();
    const dispatchMotion = (candidate) => {
      const name = String(candidate || "").trim().toLowerCase();
      if (!name || !knownMotionNames.has(name)) return;
      console.log("[Sarah/MotionTag] dispatch", name, "from reply");
      const ok = window.SARAH_AVATAR_SYSTEM?.triggerGesture?.(name, 0.85);
      if (!ok) console.warn("[Sarah/MotionTag] triggerGesture returned false for", name);
    };
    raw.replace(/<motion(?:\s+name=["']?([\w_-]+)["']?)?\s*(?:\/>|>([\w_-]+)<\/motion>)/gi, (_match, attr, body) => {
      dispatchMotion(attr || body);
      return "";
    });
    raw.replace(/<(\/?)([\w_-]+)\s*\/?>/gi, (_match, closing, tagName) => {
      if (!closing) dispatchMotion(tagName);
      return "";
    });

    let cleaned = raw
      .replace(/<motion(?:\s+name=["']?[\w_-]+["']?)?\s*(?:\/>|>[\w_-]+<\/motion>)/gi, "")
      .replace(/<(\/?)([\w_-]+)\s*\/?>/gi, (match, _closing, tagName) =>
        knownMotionNames.has(String(tagName || "").toLowerCase()) ? "" : match
      )
      .replace(/<think\b[^>]*>[\s\S]*?<\/think>/gi, "")
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

      if (!health.ollama_reachable) {
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
      title.textContent = `#${mem.id} - ${mem.role}`;

      const pinned = (mem.importance ?? 0) >= 5 || (mem.tags || "").includes("pinned");
      const pinBtn = document.createElement("button");
      pinBtn.className = "memory-pin-btn";
      pinBtn.textContent = pinned ? "Unpin" : "Pin";
      pinBtn.dataset.memoryAction = "pin-memory";
      pinBtn.dataset.id = String(mem.id);
      pinBtn.dataset.pinned = String(pinned);

      const body = document.createElement("div");
      body.className = "memory-card-body";
      body.textContent = mem.content;

      const meta = document.createElement("div");
      meta.className = "memory-card-meta";
      meta.textContent = mem.created_at ? parseDbTimestamp(mem.created_at).toLocaleString() : "";

      head.appendChild(title);
      head.appendChild(pinBtn);
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
          "Scanning the OpenClaw workspace for SKILL.md files..."
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
        if (this.tts.isEnabled()) await this.tts.speak(resp.reply);
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
      if (this.tts.isEnabled()) {
        this.tts.speak(reply, { emotion: resp?.emotion, intensity: resp?.emotion_intensity })
          .catch(err => console.warn("[TTS] Failed:", err));
      } else {
        this._setAvatarMode("idle", { source: "multimodal-chat-complete" });
      }
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
// PROJECT MODAL MANAGER
// ============================================================================

class ProjectModal {
  constructor(backend, uiManager) {
    this.backend = backend;
    this.uiManager = uiManager;
    this.currentProjectId = null;
    this.currentTab = "files";

    this.modal = document.getElementById("project-modal");
    this.title = document.getElementById("project-modal-title");
    this.subtitle = document.getElementById("project-modal-subtitle");
    this.closeBtn = document.getElementById("project-modal-close");

    // Views
    this.createView = document.getElementById("project-view-create");
    this.manageView = document.getElementById("project-view-manage");

    // Create form
    this.nameInput = document.getElementById("project-name-input");
    this.descInput = document.getElementById("project-desc-input");
    this.cancelBtn = document.getElementById("project-cancel-btn");
    this.createBtn = document.getElementById("project-create-btn");

    // Manage view tabs
    this.tabs = document.querySelectorAll(".project-tab");
    this.filesTab = document.getElementById("project-tab-files");
    this.chatsTab = document.getElementById("project-tab-chats");
    this.settingsTab = document.getElementById("project-tab-settings");

    // Files
    this.uploadFileBtn = document.getElementById("project-upload-file-btn");
    this.createFileBtn = document.getElementById("project-create-file-btn");
    this.treeViewBtn = document.getElementById("project-tree-view-btn");
    this.fileSearchInput = document.getElementById("project-file-search");
    this.filesList = document.getElementById("project-files-list");
    this.isTreeView = false;
    this.allFiles = [];
    this._fileById = new Map();

    // Chats
    this.tagChatBtn = document.getElementById("project-tag-chat-btn");
    this.chatsList = document.getElementById("project-chats-list");

    // Settings
    this.infoName = document.getElementById("project-info-name");
    this.infoFileCount = document.getElementById("project-info-file-count");
    this.infoSize = document.getElementById("project-info-size");
    this.infoCreated = document.getElementById("project-info-created");
    this.infoLastAccessed = document.getElementById("project-info-last-accessed");
    this.analyzeBtn = document.getElementById("project-analyze-btn");
    this.deleteBtn = document.getElementById("project-delete-btn");

    this._bindEvents();
  }

  _bindEvents() {
    this.closeBtn?.addEventListener("click", () => this.close());
    this.cancelBtn?.addEventListener("click", () => this.close());
    this.createBtn?.addEventListener("click", () => this.handleCreate());

    // Tab switching
    this.tabs.forEach(tab => {
      tab.addEventListener("click", () => {
        const tabName = tab.getAttribute("data-tab");
        this.switchTab(tabName);
      });
    });

    // File actions
    if (this.uploadFileBtn) {
      this.uploadFileBtn.addEventListener("click", (e) => {
        console.log("[ProjectModal] Upload button clicked (event fired)");
        e.preventDefault();
        e.stopPropagation();
        this.handleFileUpload();
      });
      console.log("[ProjectModal] Upload file button connected, ID:", this.uploadFileBtn.id);
    } else {
      console.error("[ProjectModal] Upload file button not found in DOM");
    }

    if (this.createFileBtn) {
      this.createFileBtn.addEventListener("click", () => this.handleFileCreate());
    } else {
      console.error("[ProjectModal] Create file button not found in DOM");
    }

    if (this.treeViewBtn) {
      this.treeViewBtn.addEventListener("click", () => this.toggleTreeView());
    } else {
      console.warn("[ProjectModal] Tree view button not found in DOM");
    }

    // File search
    this.fileSearchInput?.addEventListener("input", (e) => {
      this.filterFiles(e.target.value);
    });

    this.filesList?.addEventListener("click", (e) => this._handleFileListClick(e));

    // Chat actions
    this.tagChatBtn?.addEventListener("click", () => this.handleTagChat());

    // Settings actions
    this.analyzeBtn?.addEventListener("click", () => this.handleAnalyzeProject());
    // Delete button is rebound dynamically in _bindDeleteButton() when settings tab opens.

    // Enter key in name input
    this.nameInput?.addEventListener("keypress", (e) => {
      if (e.key === "Enter") this.handleCreate();
    });
  }

  _handleFileListClick(e) {
    const actionBtn = e.target.closest("[data-file-action]");
    if (actionBtn) {
      e.stopPropagation();
      const file = this._fileById.get(String(actionBtn.dataset.id));
      if (!file) return;

      if (actionBtn.dataset.fileAction === "edit") {
        this.handleFileEdit(file.id, file.file_name, file.content);
      } else if (actionBtn.dataset.fileAction === "delete") {
        this.handleFileDelete(file.id, file.file_name);
      }
      return;
    }

    const folder = e.target.closest("[data-folder-path]");
    if (!folder || !this.filesList?.contains(folder)) return;

    const fullPath = folder.dataset.folderPath;
    if (!this.expandedFolders) this.expandedFolders = new Set();
    if (this.expandedFolders.has(fullPath)) {
      this.expandedFolders.delete(fullPath);
    } else {
      this.expandedFolders.add(fullPath);
    }
    this.renderFiles(this.allFiles);
  }

  open(mode = "create", projectId = null) {
    this.currentProjectId = projectId;
    this.modal.classList.add("show");

    if (mode === "create") {
      this.title.textContent = "Create a personal project";
      this.subtitle.textContent = "Organize files, code, and conversations";
      this.createView.style.display = "block";
      this.manageView.style.display = "none";
      this.nameInput.value = "";
      this.descInput.value = "";
      this.nameInput.focus();
    } else if (mode === "manage" && projectId) {
      this.createView.style.display = "none";
      this.manageView.style.display = "block";
      this.switchTab("files");
      this.loadProjectData(projectId);
    }
  }

  close() {
    this.modal.classList.remove("show");
    this.currentProjectId = null;
  }

  switchTab(tabName) {
    this.currentTab = tabName;

    // Update tab buttons
    this.tabs.forEach(tab => {
      tab.classList.toggle("active", tab.getAttribute("data-tab") === tabName);
    });

    // Show correct content
    this.filesTab.style.display = tabName === "files" ? "block" : "none";
    this.chatsTab.style.display = tabName === "chats" ? "block" : "none";
    this.settingsTab.style.display = tabName === "settings" ? "block" : "none";

    // Bind delete and rename buttons when settings tab is shown
    if (tabName === "settings") {
      setTimeout(() => {
        this._bindDeleteButton();
        this._bindRenameButton();
      }, 100);
    }

    // Load data for tab
    if (this.currentProjectId) {
      if (tabName === "files") this.loadFiles();
      else if (tabName === "chats") this.loadChats();
      else if (tabName === "settings") this.loadSettings();
    }
  }

  _bindDeleteButton() {
    // Bind delete button dynamically when settings tab is shown
    const deleteBtn = document.getElementById("project-delete-btn");
    if (deleteBtn) {
      console.log("[ProjectModal] Delete button found in settings tab");
      // Remove any existing listeners by cloning the button
      const newDeleteBtn = deleteBtn.cloneNode(true);
      deleteBtn.parentNode.replaceChild(newDeleteBtn, deleteBtn);

      newDeleteBtn.addEventListener("click", () => {
        console.log("[ProjectModal] Delete button clicked!");
        this.handleDeleteProject();
      });
    } else {
      console.warn("[ProjectModal] Delete button not found in settings tab!");
    }
  }

  _bindRenameButton() {
    const renameBtn = document.getElementById("project-rename-btn");
    if (renameBtn) {
      console.log("[ProjectModal] Rename button found in settings tab");
      const newRenameBtn = renameBtn.cloneNode(true);
      renameBtn.parentNode.replaceChild(newRenameBtn, renameBtn);

      newRenameBtn.addEventListener("click", () => {
        console.log("[ProjectModal] Rename button clicked!");
        this.handleRenameProject();
      });
    } else {
      console.warn("[ProjectModal] Rename button not found in settings tab!");
    }
  }

  async handleRenameProject() {
    const currentName = this.infoName?.textContent || "";
    const renameBtn = document.getElementById("project-rename-btn");

    // Check if already in edit mode
    if (this.infoName.querySelector("input")) return;

    // Create inline input
    const input = document.createElement("input");
    input.type = "text";
    input.value = currentName;
    input.className = "project-rename-input";

    // Save original content
    const originalContent = this.infoName.textContent;
    this.infoName.textContent = "";
    this.infoName.appendChild(input);
    input.focus();
    input.select();

    // Hide rename button while editing
    if (renameBtn) renameBtn.style.display = "none";

    const finishRename = async (save) => {
      const newName = input.value.trim();
      input.remove();

      if (save && newName && newName !== originalContent) {
        try {
          await this.backend.renameProject(this.currentProjectId, newName);
          this.infoName.textContent = newName;

          // Update the modal title
          if (this.title) {
            this.title.textContent = newName;
          }

          // Refresh projects list in sidebar
          await this.uiManager.refreshProjects();
        } catch (err) {
          console.error("Failed to rename project:", err);
          this.infoName.textContent = originalContent;
        }
      } else {
        this.infoName.textContent = originalContent;
      }

      // Show rename button again
      if (renameBtn) renameBtn.style.display = "";
    };

    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        finishRename(true);
      } else if (e.key === "Escape") {
        finishRename(false);
      }
    });

    input.addEventListener("blur", () => {
      // Small delay to allow click events to fire first
      setTimeout(() => finishRename(true), 100);
    });
  }

  async handleCreate() {
    const name = this.nameInput.value.trim();
    if (!name) {
      alert("Please enter a project name");
      return;
    }

    const description = this.descInput.value.trim() || null;

    try {
      const projectId = await this.backend.createProject(name, description, null);
      this.close();
      await this.uiManager.refreshProjects();

      // Open the project in manage mode
      this.open("manage", projectId);
    } catch (err) {
      console.error("Failed to create project:", err);
      alert("Failed to create project: " + err.message);
    }
  }

  async loadProjectData(projectId) {
    try {
      const data = await this.backend.getProject(projectId);
      if (data.ok && data.project) {
        const p = data.project;
        this.title.textContent = p.name;
        this.subtitle.textContent = p.description || "Manage files and conversations";
        // Issue #31: cache root_path so the Edit action can resolve absolute
        // paths for VS Code without re-fetching the project record.
        this.currentProjectRoot = p.root_path || "";

        // Load current tab data
        if (this.currentTab === "files") await this.loadFiles();
        else if (this.currentTab === "chats") await this.loadChats();
        else if (this.currentTab === "settings") await this.loadSettings();
      }
    } catch (err) {
      console.warn("Failed to load project data:", err);
    }
  }

  async loadFiles() {
    try {
      const files = await this.backend.getProjectFiles(this.currentProjectId);
      this.allFiles = files || [];

      if (!this.allFiles || this.allFiles.length === 0) {
        this._fileById = new Map();
        const empty = document.createElement("div");
        empty.className = "project-empty-state";
        empty.textContent = "No files yet. Upload or create files to get started.";
        this.filesList.replaceChildren(empty);
        return;
      }

      // Clear search if switching tabs
      if (this.fileSearchInput) {
        this.fileSearchInput.value = "";
      }

      this.renderFiles(this.allFiles);
    } catch (err) {
      console.warn("Failed to load files:", err);
      this._fileById = new Map();
      const error = document.createElement("div");
      error.className = "project-empty-state";
      error.textContent = "Failed to load files";
      this.filesList.replaceChildren(error);
    }
  }

  renderFiles(files) {
    const perf = sarahPerfStart("project.files.render");
    this._fileById = new Map((files || []).map((file) => [String(file.id), file]));

    if (this.isTreeView) {
      this.renderFileTree(files);
    } else {
      this.renderFileList(files);
    }

    sarahPerfEnd("project.files.render", perf);
  }

  renderFileList(files) {
    const fragment = document.createDocumentFragment();
    (files || []).forEach(file => {
      const item = document.createElement("div");
      item.className = "project-file-item";

      const sizeKB = (file.file_size / 1024).toFixed(2);

      item.innerHTML = `
        <div class="project-file-info">
          <div class="project-file-name">${file.file_name}</div>
          <div class="project-file-meta">${file.file_path || file.file_name} • ${sizeKB} KB</div>
        </div>
        <div class="project-file-actions">
          <button class="project-file-action-btn edit" title="Edit" data-id="${file.id}">✏️</button>
          <button class="project-file-action-btn delete" title="Delete" data-id="${file.id}">🗑️</button>
        </div>
      `;

      item.querySelector(".edit").dataset.fileAction = "edit";
      item.querySelector(".delete").dataset.fileAction = "delete";
      fragment.appendChild(item);
    });
    this.filesList.replaceChildren(fragment);
  }

  renderFileTree(files) {
    // Build tree structure from file paths
    const tree = {};

    (files || []).forEach(file => {
      const path = file.file_path || file.file_name;
      const parts = path.split(/[\/\\]/);

      let current = tree;
      parts.forEach((part, idx) => {
        if (idx === parts.length - 1) {
          // This is a file
          if (!current._files) current._files = [];
          current._files.push(file);
        } else {
          // This is a directory
          if (!current[part]) current[part] = {};
          current = current[part];
        }
      });
    });

    // Initialize expanded folders state if needed
    if (!this.expandedFolders) {
      this.expandedFolders = new Set();
    }

    const fragment = document.createDocumentFragment();
    this.renderTreeNode(tree, fragment, 0, "");
    this.filesList.replaceChildren(fragment);
  }

  renderTreeNode(node, container, depth, pathPrefix) {
    const entries = Object.keys(node).filter(k => k !== '_files').sort();

    // Render directories first
    entries.forEach(dirName => {
      const fullPath = pathPrefix ? `${pathPrefix}/${dirName}` : dirName;
      const isExpanded = this.expandedFolders.has(fullPath);

      // Create folder container
      const folderContainer = document.createElement("div");
      folderContainer.className = "project-tree-folder-container";

      // Create folder header (clickable)
      const dirItem = document.createElement("div");
      dirItem.className = "project-tree-folder";
      dirItem.style.paddingLeft = (depth * 16 + 8) + "px";
      dirItem.dataset.folderPath = fullPath;

      const childCount = this._countTreeChildren(node[dirName]);

      dirItem.innerHTML = `
        <span class="project-tree-chevron">${isExpanded ? '▼' : '▶'}</span>
        <span class="project-tree-icon">${isExpanded ? '📂' : '📁'}</span>
        <span class="project-tree-label">${dirName}</span>
        <span class="project-tree-count">(${childCount})</span>
      `;

      folderContainer.appendChild(dirItem);

      // Create contents container (collapsible)
      const contentsContainer = document.createElement("div");
      contentsContainer.className = "project-tree-contents";
      contentsContainer.style.display = isExpanded ? "block" : "none";

      // Recursively render children
      this.renderTreeNode(node[dirName], contentsContainer, depth + 1, fullPath);

      folderContainer.appendChild(contentsContainer);
      container.appendChild(folderContainer);
    });

    // Render files in this directory
    if (node._files) {
      node._files.sort((a, b) => a.file_name.localeCompare(b.file_name)).forEach(file => {
        const fileItem = document.createElement("div");
        fileItem.className = "project-file-item project-tree-file";
        fileItem.style.paddingLeft = (depth * 16 + 8) + "px";

        const sizeKB = (file.file_size / 1024).toFixed(2);
        const ext = file.file_name.split('.').pop().toLowerCase();
        const fileIcon = this._getFileIcon(ext);

        fileItem.innerHTML = `
          <div class="project-file-info">
            <div class="project-file-name">
              <span class="project-tree-icon">${fileIcon}</span>
              ${file.file_name}
            </div>
            <div class="project-file-meta">${sizeKB} KB</div>
          </div>
          <div class="project-file-actions">
            <button class="project-file-action-btn edit" title="Edit" data-id="${file.id}">✏️</button>
            <button class="project-file-action-btn delete" title="Delete" data-id="${file.id}">🗑️</button>
          </div>
        `;

        fileItem.querySelector(".edit").dataset.fileAction = "edit";
        fileItem.querySelector(".delete").dataset.fileAction = "delete";

        container.appendChild(fileItem);
      });
    }
  }

  _countTreeChildren(node) {
    let count = 0;
    if (node._files) count += node._files.length;
    Object.keys(node).filter(k => k !== '_files').forEach(key => {
      count += this._countTreeChildren(node[key]);
    });
    return count;
  }

  _getFileIcon(ext) {
    const icons = {
      js: '📜', ts: '📜', jsx: '📜', tsx: '📜',
      py: '🐍', rb: '💎', go: '🔷', rs: '🦀',
      html: '🌐', css: '🎨', scss: '🎨', less: '🎨',
      json: '📋', yaml: '📋', yml: '📋', xml: '📋', toml: '📋',
      md: '📝', txt: '📄', rtf: '📄',
      sh: '💻', bash: '💻', zsh: '💻', bat: '💻', ps1: '💻',
      sql: '🗃️', db: '🗃️',
      env: '🔒', gitignore: '🔒',
      jpg: '🖼️', jpeg: '🖼️', png: '🖼️', gif: '🖼️', svg: '🖼️',
      pdf: '📕', doc: '📘', docx: '📘', xls: '📊', xlsx: '📊',
    };
    return icons[ext] || '📄';
  }

  toggleTreeView() {
    this.isTreeView = !this.isTreeView;
    this.treeViewBtn.textContent = this.isTreeView ? "📋 List View" : "🌳 Tree View";
    this.renderFiles(this.allFiles);
  }

  filterFiles(searchTerm) {
    const term = searchTerm.toLowerCase().trim();

    if (!term) {
      this.renderFiles(this.allFiles);
      return;
    }

    const filtered = this.allFiles.filter(file => {
      const fileName = (file.file_name || "").toLowerCase();
      const filePath = (file.file_path || "").toLowerCase();
      return fileName.includes(term) || filePath.includes(term);
    });

    this.renderFiles(filtered);
  }

  async loadChats() {
    try {
      const conversations = await this.backend.getProjectConversations(this.currentProjectId);

      if (!conversations || conversations.length === 0) {
        this.chatsList.innerHTML = '<div class="project-empty-state">No chats tagged. Tag conversations to keep project context.</div>';
        return;
      }

      this.chatsList.innerHTML = "";

      conversations.forEach(convo => {
        const item = document.createElement("div");
        item.className = "project-chat-item";

        const datetime = formatMountainTime(convo.last_active_at, true);

        item.innerHTML = `
          <div class="project-chat-info">
            <div class="project-chat-title">${convo.title || `Chat #${convo.id}`}</div>
            <div class="project-chat-meta">Last active: ${datetime}</div>
          </div>
          <div class="project-chat-actions">
            <button class="project-chat-action-btn delete" title="Untag" data-id="${convo.id}">✕</button>
          </div>
        `;

        // Untag button
        item.querySelector(".delete").addEventListener("click", () => {
          this.handleUntagChat(convo.id, convo.title);
        });

        this.chatsList.appendChild(item);
      });
    } catch (err) {
      console.warn("Failed to load chats:", err);
      this.chatsList.innerHTML = '<div class="project-empty-state">Failed to load chats</div>';
    }
  }

  async loadSettings() {
    try {
      const data = await this.backend.getProject(this.currentProjectId);

      if (data.ok && data.project) {
        const p = data.project;
        this.infoName.textContent = p.name;
        this.infoFileCount.textContent = p.file_count || 0;

        // Format size in human-readable format
        const totalBytes = p.total_size || 0;
        let sizeText = "0 B";
        if (totalBytes >= 1024 * 1024) {
          sizeText = (totalBytes / (1024 * 1024)).toFixed(2) + " MB";
        } else if (totalBytes >= 1024) {
          sizeText = (totalBytes / 1024).toFixed(2) + " KB";
        } else {
          sizeText = totalBytes + " B";
        }
        this.infoSize.textContent = sizeText;

        this.infoCreated.textContent = formatMountainTime(p.created_at, false);
        this.infoLastAccessed.textContent = formatMountainTime(p.last_accessed_at, true);
      }
    } catch (err) {
      console.warn("Failed to load settings:", err);
    }
  }

  async handleFileUpload() {
    console.log("[ProjectModal] handleFileUpload called");
    console.log("[ProjectModal] Current project ID:", this.currentProjectId);

    // Check if a project is currently open
    if (!this.currentProjectId) {
      alert("Please open a project first before uploading files.");
      console.error("[ProjectModal] Cannot upload files: No project selected");
      return;
    }

    // Check if native file dialog is available
    if (!window.sarahFiles) {
      console.error("[ProjectModal] sarahFiles bridge not available");
      alert("File dialog not available. Please restart the application.");
      return;
    }

    // Show upload type selection buttons
    const uploadType = await this._showUploadTypeDialog();
    if (!uploadType) {
      console.log("[ProjectModal] User cancelled upload type selection");
      return;
    }

    console.log("[ProjectModal] User selected upload type:", uploadType);

    let result;
    try {
      if (uploadType === "folder") {
        result = await window.sarahFiles.selectFolder();
      } else {
        result = await window.sarahFiles.selectFiles(uploadType === "multiple");
      }
    } catch (err) {
      console.error("[ProjectModal] Dialog error:", err);
      alert("Failed to open file dialog: " + err.message);
      return;
    }

    if (result.canceled || !result.files || result.files.length === 0) {
      console.log("[ProjectModal] No files selected or dialog canceled");
      return;
    }

    console.log("[ProjectModal] Files selected:", result.files.length);

    let successCount = 0;
    let failCount = 0;

    for (const file of result.files) {
      try {
        console.log(`[ProjectModal] Uploading: ${file.path}`);
        await this.backend.uploadFileToProject(this.currentProjectId, {
          file_name: file.name,
          file_path: file.path,
          content: file.content,
          file_type: "text/plain",
          file_size: file.size,
        });
        console.log(`[ProjectModal] Successfully uploaded ${file.name}`);
        successCount++;
      } catch (err) {
        console.error(`[ProjectModal] Failed to upload ${file.name}:`, err);
        failCount++;
      }
    }

    // Play success chime
    this.playUploadCompleteChime();

    alert(`Uploaded ${successCount} file(s)${failCount > 0 ? `, ${failCount} failed` : ''}`);
    console.log(`[ProjectModal] Upload complete. Success: ${successCount}, Failed: ${failCount}`);
    await this.loadFiles();
  }

  _showUploadTypeDialog() {
    return new Promise((resolve) => {
      // Create modal overlay (z-index must be higher than project-modal's 9999)
      const overlay = document.createElement("div");
      overlay.className = "confirm-popup-backdrop upload-type-backdrop";
      overlay.innerHTML = `
        <div class="confirm-popup-window upload-type-window">
          <div class="confirm-popup-title">Upload Files</div>
          <div class="confirm-popup-text">Choose what to upload:</div>
          <div class="upload-type-options">
            <button class="pill-btn-accent upload-type-option" data-type="folder">
              📁 Upload Folder
            </button>
            <button class="pill-btn-accent upload-type-option upload-type-option-alt" data-type="multiple">
              📄 Upload Multiple Files
            </button>
            <button class="pill-btn-accent upload-type-option upload-type-option-soft" data-type="single">
              📃 Upload Single File
            </button>
            <button class="confirm-popup-cancel upload-type-cancel" data-type="cancel">
              Cancel
            </button>
          </div>
        </div>
      `;

      overlay.addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-type]");
        if (btn) {
          const type = btn.getAttribute("data-type");
          overlay.remove();
          resolve(type === "cancel" ? null : type);
        }
      });

      document.body.appendChild(overlay);
    });
  }

  playUploadCompleteChime() {
    // Create an audio context and play a pleasant chime sound
    try {
      const audioContext = new (window.AudioContext || window.webkitAudioContext)();

      // Play three ascending notes for a pleasant chime
      const notes = [523.25, 659.25, 783.99]; // C5, E5, G5
      const now = audioContext.currentTime;

      notes.forEach((frequency, index) => {
        const oscillator = audioContext.createOscillator();
        const gainNode = audioContext.createGain();

        oscillator.connect(gainNode);
        gainNode.connect(audioContext.destination);

        oscillator.frequency.value = frequency;
        oscillator.type = "sine";

        // Envelope for smooth sound
        const startTime = now + (index * 0.15);
        gainNode.gain.setValueAtTime(0, startTime);
        gainNode.gain.linearRampToValueAtTime(0.3, startTime + 0.05);
        gainNode.gain.exponentialRampToValueAtTime(0.01, startTime + 0.4);

        oscillator.start(startTime);
        oscillator.stop(startTime + 0.4);
      });
    } catch (err) {
      console.warn("Failed to play upload chime:", err);
    }
  }

  async readFileAsText(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = (e) => resolve(e.target.result);
      reader.onerror = (e) => reject(e);
      reader.readAsText(file);
    });
  }

  async handleFileCreate() {
    const fileName = prompt("Enter file name (e.g., script.py, notes.md):");
    if (!fileName || !fileName.trim()) return;

    try {
      await this.backend.createFile(this.currentProjectId, fileName.trim(), "");
      await this.loadFiles();
    } catch (err) {
      console.error("Failed to create file:", err);
      alert("Failed to create file: " + err.message);
    }
  }

  async handleFileEdit(fileId, fileName, content) {
    // Issue #31: open in VS Code instead of an in-app prompt(). Resolve the
    // absolute path from the cached project root + the file's stored path.
    const file = this._fileById?.get?.(String(fileId));
    const relPath = file?.file_path || fileName;
    const root = this.currentProjectRoot || "";
    let absPath = relPath;
    if (root && relPath && !/^([a-zA-Z]:[\\/]|[\\/]{2}|\/)/.test(relPath)) {
      const sep = root.includes("\\") ? "\\" : "/";
      absPath = root.replace(/[\\/]+$/, "") + sep + relPath.replace(/^[\\/]+/, "");
    }

    if (window.sarahFiles?.openInVSCode) {
      try {
        const res = await window.sarahFiles.openInVSCode(absPath);
        if (res?.ok) return;
        console.warn("[Projects] VS Code launch failed:", res);
        alert(`Could not open in VS Code (${res?.error || "unknown"}). Path: ${absPath}`);
        return;
      } catch (err) {
        console.warn("[Projects] VS Code IPC error:", err);
      }
    }

    // Fallback: legacy in-app prompt edit if VS Code bridge is unavailable.
    const newContent = prompt(`Edit file: ${fileName}`, content || "");
    if (newContent === null) return;
    try {
      await this.backend.updateFile(this.currentProjectId, fileId, newContent);
      await this.loadFiles();
    } catch (err) {
      console.error("Failed to update file:", err);
      alert("Failed to update file: " + err.message);
    }
  }

  async handleFileDelete(fileId, fileName) {
    if (!confirm(`Delete file "${fileName}"?\nThis action cannot be undone.`)) return;

    try {
      await this.backend.deleteFile(this.currentProjectId, fileId);
      await this.loadFiles();
    } catch (err) {
      console.error("Failed to delete file:", err);
      alert("Failed to delete file: " + err.message);
    }
  }

  async handleTagChat() {
    if (!this.uiManager.activeConversationId) {
      alert("No active conversation. Start a chat first.");
      return;
    }

    try {
      await this.backend.tagConversation(this.currentProjectId, this.uiManager.activeConversationId);
      await this.loadChats();
    } catch (err) {
      console.error("Failed to tag conversation:", err);
      alert("Failed to tag conversation: " + err.message);
    }
  }

  async handleUntagChat(conversationId, title) {
    if (!confirm(`Remove "${title || 'this conversation'}" from project?`)) return;

    try {
      await this.backend.untagConversation(this.currentProjectId, conversationId);
      await this.loadChats();
    } catch (err) {
      console.error("Failed to untag conversation:", err);
      alert("Failed to untag conversation: " + err.message);
    }
  }

  async handleDeleteProject() {
    console.log("[ProjectModal] handleDeleteProject called");
    console.log("[ProjectModal] Current project ID:", this.currentProjectId);

    if (!confirm("⚠️ WARNING: This will permanently delete this project and all its files.\n\nAre you sure you want to delete this project?")) {
      console.log("[ProjectModal] Deletion cancelled by user");
      return;
    }

    try {
      console.log("[ProjectModal] Calling backend.deleteProject with ID:", this.currentProjectId);
      await this.backend.deleteProject(this.currentProjectId);
      console.log("[ProjectModal] Project deleted successfully");
      alert("Project deleted successfully!");
      this.close();
      await this.uiManager.refreshProjects();
    } catch (err) {
      console.error("Failed to delete project:", err);
      alert("Failed to delete project: " + err.message);
    }
  }

  async handleAnalyzeProject() {
    console.log("[ProjectModal] handleAnalyzeProject called");
    console.log("[ProjectModal] Current project ID:", this.currentProjectId);

    const resultDiv = document.getElementById("project-analysis-result");
    const textPre = document.getElementById("project-analysis-text");
    const analyzeBtn = document.getElementById("project-analyze-btn");

    if (!resultDiv || !textPre || !analyzeBtn) {
      alert("Error: Analysis UI elements not found");
      return;
    }

    try {
      // Show loading state
      analyzeBtn.textContent = "⏳ Analyzing...";
      analyzeBtn.disabled = true;
      textPre.textContent = "Analyzing project structure, dependencies, git history, and more...\n\nThis may take a few moments...";
      resultDiv.style.display = "block";

      // Call backend analysis
      const analysis = await this.backend.analyzeProject(this.currentProjectId);

      // Format and display the analysis
      let output = "";

      // AI Summary (most important part)
      if (analysis.ai_summary) {
        output += analysis.ai_summary + "\n\n";
      }

      // Dependencies details
      if (analysis.dependencies) {
        const deps = analysis.dependencies;
        if (deps.npm && deps.npm.dependencies) {
          output += "═══════════════════════════════════════\n";
          output += "NPM DEPENDENCIES:\n";
          output += "═══════════════════════════════════════\n";
          const depsObj = deps.npm.dependencies;
          Object.keys(depsObj).slice(0, 10).forEach(key => {
            output += `  ${key}: ${depsObj[key]}\n`;
          });
          if (Object.keys(depsObj).length > 10) {
            output += `  ... and ${Object.keys(depsObj).length - 10} more\n`;
          }
          output += "\n";
        }

        if (deps.python && deps.python.requirements) {
          output += "═══════════════════════════════════════\n";
          output += "PYTHON REQUIREMENTS:\n";
          output += "═══════════════════════════════════════\n";
          deps.python.requirements.slice(0, 15).forEach(req => {
            output += `  ${req}\n`;
          });
          if (deps.python.requirements.length > 15) {
            output += `  ... and ${deps.python.requirements.length - 15} more\n`;
          }
          output += "\n";
        }
      }

      // Git history
      if (analysis.git_history && analysis.git_history.length > 0) {
        output += "═══════════════════════════════════════\n";
        output += "RECENT GIT COMMITS:\n";
        output += "═══════════════════════════════════════\n";
        analysis.git_history.slice(0, 5).forEach(commit => {
          output += `  ${commit.hash} - ${commit.message}\n`;
          output += `  by ${commit.author} on ${commit.date}\n\n`;
        });
      }

      // README preview
      if (analysis.readme_content) {
        output += "═══════════════════════════════════════\n";
        output += "README:\n";
        output += "═══════════════════════════════════════\n";
        output += analysis.readme_content.substring(0, 1000);
        if (analysis.readme_content.length > 1000) {
          output += "\n... (truncated)";
        }
        output += "\n\n";
      }

      textPre.textContent = output;
      analyzeBtn.textContent = "✅ Analysis Complete";

      // Also send to chat as context
      if (window.SARAH_UI && analysis.ai_summary) {
        window.SARAH_UI.appendMessage(
          "assistant",
          `📊 Project Analysis Complete:\n\n${analysis.ai_summary}\n\nI now have a deep understanding of this project's structure, dependencies, and purpose. Ask me anything about it!`
        );
      }

    } catch (err) {
      console.error("Failed to analyze project:", err);
      textPre.textContent = `Error analyzing project: ${err.message}`;
      analyzeBtn.textContent = "❌ Analysis Failed";
    } finally {
      analyzeBtn.disabled = false;
      setTimeout(() => {
        if (analyzeBtn.textContent !== "🔍 Analyze Project") {
          analyzeBtn.textContent = "🔍 Analyze Project";
        }
      }, 3000);
    }
  }
}

// ============================================================================
// LIVE2D AVATAR HOOKS (if needed later)
// ============================================================================
const PIXI = window.PIXI;
const Live2DModel = window.Live2DModel;

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

      if (!health.ollama_reachable) {
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
