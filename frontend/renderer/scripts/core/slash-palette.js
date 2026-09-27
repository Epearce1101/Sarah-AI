// Extracted from dashboard.js (improvement #7). Loaded as an ES module.
// SarahSlashPalette: the / command popover above the chat input.

// -----------------------------------------------------------------------------
// Issue #26 — Slash-command palette
// Lightweight popover that opens when the user types "/" at the start of the
// chat input and filters as they keep typing. Selecting a command (Enter / Tab
// / click) runs its action and clears the input.
// -----------------------------------------------------------------------------
export class SarahSlashPalette {
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
