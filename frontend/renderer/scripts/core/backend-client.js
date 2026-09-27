// Extracted from dashboard.js (improvement #7). Loaded as an ES module.
// SarahBackend: thin fetch wrapper over the Sarah HTTP API.
import { API_BASE, ENDPOINTS } from "./config.js";

// -----------------------------------------------------------------------------
// BACKEND WRAPPER
// -----------------------------------------------------------------------------

export class SarahBackend {
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

  async deleteMemory(memoryId) {
    const res = await fetch(`${this.base}/api/memories/${memoryId}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Delete memory failed");
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
