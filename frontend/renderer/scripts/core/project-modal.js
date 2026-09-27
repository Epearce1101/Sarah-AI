// Extracted from dashboard.js (improvement #7). Loaded as an ES module.
// ProjectModal: the Projects panel (files, tree, git, analysis).
import { formatMountainTime, sarahPerfEnd, sarahPerfStart } from "./config.js";

// ============================================================================
// PROJECT MODAL MANAGER
// ============================================================================

export class ProjectModal {
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
