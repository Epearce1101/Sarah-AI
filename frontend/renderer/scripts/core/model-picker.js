// Chat-model picker (improvement #8): opens from the header model label,
// lists OpenRouter's catalog (free models first), and switches the model at
// runtime via POST /api/llm_model. The choice is persisted by the backend.

function formatContext(tokens) {
  const n = Number(tokens) || 0;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 ? 1 : 0)}M ctx`;
  if (n >= 1000) return `${Math.round(n / 1000)}k ctx`;
  return n ? `${n} ctx` : "";
}

function formatPrice(model) {
  if (model.free) return "free";
  const p = model.prompt_per_million;
  const c = model.completion_per_million;
  if (p == null || c == null) return "variable price";
  return `$${p}/$${c} per M`;
}

export function filterModels(models, query, freeOnly) {
  const terms = String(query || "").toLowerCase().split(/\s+/).filter(Boolean);
  return models.filter((m) => {
    if (freeOnly && !m.free) return false;
    const hay = `${m.id} ${m.name}`.toLowerCase();
    return terms.every((t) => hay.includes(t));
  });
}

export class SarahModelPicker {
  constructor({ anchor, backend, onChanged }) {
    this.anchor = anchor;
    this.backend = backend;
    this.onChanged = onChanged;
    this.models = [];
    this.current = "";
    this.fallbacks = [];
    this.open = false;
    this.el = null;
    this._onDocClick = (ev) => {
      if (this.open && !this.el.contains(ev.target) && ev.target !== this.anchor) this.close();
    };
    this._onKey = (ev) => {
      if (this.open && ev.key === "Escape") this.close();
    };
  }

  _build() {
    const el = document.createElement("div");
    el.className = "model-picker";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-label", "Choose chat model");

    const controls = document.createElement("div");
    controls.className = "model-picker-controls";
    this.searchEl = document.createElement("input");
    this.searchEl.className = "model-picker-search";
    this.searchEl.type = "search";
    this.searchEl.placeholder = "Search models...";
    const freeLabel = document.createElement("label");
    freeLabel.className = "model-picker-filter";
    this.freeEl = document.createElement("input");
    this.freeEl.type = "checkbox";
    this.freeEl.checked = true;
    freeLabel.append(this.freeEl, " Free only");
    controls.append(this.searchEl, freeLabel);

    this.listEl = document.createElement("div");
    this.listEl.className = "model-picker-list";
    this.noteEl = document.createElement("div");
    this.noteEl.className = "model-picker-note";

    el.append(controls, this.listEl, this.noteEl);
    this.searchEl.addEventListener("input", () => this._renderList());
    this.freeEl.addEventListener("change", () => this._renderList());
    this.searchEl.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        const first = this.listEl.querySelector(".model-picker-row");
        if (first) this._choose(first.dataset.modelId);
      }
    });
    this.listEl.addEventListener("click", (ev) => {
      const row = ev.target.closest(".model-picker-row");
      if (row) this._choose(row.dataset.modelId);
    });
    document.body.appendChild(el);
    this.el = el;
  }

  _position() {
    const rect = this.anchor.getBoundingClientRect();
    const width = Math.min(460, window.innerWidth - 32);
    this.el.style.width = `${width}px`;
    this.el.style.top = `${Math.round(rect.bottom + 6)}px`;
    this.el.style.left = `${Math.round(Math.min(Math.max(16, rect.left), window.innerWidth - width - 16))}px`;
  }

  _renderList() {
    const visible = filterModels(this.models, this.searchEl.value, this.freeEl.checked);
    const frag = document.createDocumentFragment();
    for (const m of visible.slice(0, 200)) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "model-picker-row" + (m.id === this.current ? " is-current" : "");
      row.dataset.modelId = m.id;
      const name = document.createElement("span");
      name.className = "model-picker-name";
      name.textContent = m.name || m.id;
      const meta = document.createElement("span");
      meta.className = "model-picker-meta";
      meta.textContent = [m.id, formatPrice(m), formatContext(m.context_length), m.vision ? "vision" : ""]
        .filter(Boolean).join(" · ");
      row.append(name, meta);
      frag.appendChild(row);
    }
    if (!visible.length) {
      const empty = document.createElement("div");
      empty.className = "model-picker-empty";
      empty.textContent = this.models.length ? "No models match." : "Couldn't load OpenRouter's model list.";
      frag.appendChild(empty);
    }
    this.listEl.replaceChildren(frag);
    this.noteEl.textContent = this.fallbacks.length
      ? `If a model is unavailable, requests fall back to: ${this.fallbacks.join(", ")}`
      : "";
  }

  async _load() {
    this.listEl.textContent = "Loading models...";
    try {
      const data = await this.backend.listModels();
      this.models = data.models || [];
      this.current = data.current || "";
      this.fallbacks = data.fallbacks || [];
    } catch (err) {
      console.warn("[ModelPicker] Failed to load models:", err);
      this.models = [];
    }
    if (this.current && !this.models.find((m) => m.id === this.current && m.free)) {
      this.freeEl.checked = false; // don't hide a paid current model
    }
    this._renderList();
  }

  async _choose(modelId) {
    if (!modelId || modelId === this.current) {
      this.close();
      return;
    }
    this.listEl.setAttribute("aria-busy", "true");
    try {
      const status = await this.backend.setLLMModel(modelId);
      this.current = modelId;
      this.onChanged?.(status);
      this.close();
    } catch (err) {
      console.warn("[ModelPicker] Switch failed:", err);
      this.noteEl.textContent = `Couldn't switch: ${err.message}`;
    } finally {
      this.listEl.removeAttribute("aria-busy");
    }
  }

  async show() {
    if (!this.el) this._build();
    this.open = true;
    this.el.style.display = "flex";
    this._position();
    document.addEventListener("mousedown", this._onDocClick, true);
    document.addEventListener("keydown", this._onKey, true);
    this.searchEl.value = "";
    this.searchEl.focus();
    await this._load();
  }

  close() {
    this.open = false;
    if (this.el) this.el.style.display = "none";
    document.removeEventListener("mousedown", this._onDocClick, true);
    document.removeEventListener("keydown", this._onKey, true);
  }

  toggle() {
    return this.open ? this.close() : this.show();
  }
}
