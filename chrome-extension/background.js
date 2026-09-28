// Sarah Browser Bridge: lets Sarah browse in this Chrome.
//
// Connects to Sarah's backend on this PC (ws://127.0.0.1:<port>/ws/chrome;
// the backend only accepts this extension's id) and runs what she asks:
// open a tab, read a page, click, type, scroll, screenshot... She works in
// her own tab ("her tab") unless told to use one of yours. Nothing is sent
// anywhere else.

const PORTS = [8907];
const PAGE_TEXT_LIMIT = 20000;
let ws = null;
let herTab = null;
let connecting = false;

// ---------------------------------------------------------------------------
// Connection (reconnects; the alarm revives a sleeping service worker)
// ---------------------------------------------------------------------------

async function ports() {
  try {
    const { port } = await chrome.storage.local.get("port");
    if (port) return [Number(port)];
  } catch {}
  return PORTS;
}

async function connect() {
  if (connecting || (ws && ws.readyState <= 1)) return;
  connecting = true;
  for (const port of await ports()) {
    try {
      await new Promise((resolve, reject) => {
        const sock = new WebSocket(`ws://127.0.0.1:${port}/ws/chrome`);
        sock.onopen = () => { ws = sock; resolve(); };
        sock.onerror = () => reject(new Error("no Sarah on " + port));
        sock.onclose = () => {
          if (ws === sock) { ws = null; setTimeout(connect, 5000); } // Sarah restarted: find her again
          setStatus(false);
        };
        sock.onmessage = (ev) => onMessage(ev.data);
      });
      send({ type: "hello", version: chrome.runtime.getManifest().version, ua: navigator.userAgent });
      setStatus(true);
      break;
    } catch {}
  }
  connecting = false;
}

function setStatus(on) {
  chrome.action.setBadgeText({ text: on ? "" : "off" });
  chrome.action.setBadgeBackgroundColor({ color: "#ff4f7a" });
  chrome.storage.local.set({ connected: on });
}

function send(obj) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
}

chrome.alarms.create("sarah-keepalive", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(() => { if (!ws) connect(); else send({ type: "ping" }); });
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
setInterval(() => send({ type: "ping" }), 20000); // keeps the worker awake while connected
connect();

async function onMessage(data) {
  let msg;
  try { msg = JSON.parse(data); } catch { return; }
  if (!msg.id || !msg.action) return;
  try {
    const fn = ACTIONS[msg.action];
    if (!fn) throw new Error(`unknown action ${msg.action}`);
    send({ id: msg.id, ok: true, result: await fn(msg.args || {}) });
  } catch (err) {
    send({ id: msg.id, ok: false, error: String(err?.message || err) });
  }
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function tabExists(id) {
  if (id == null) return false;
  try { await chrome.tabs.get(id); return true; } catch { return false; }
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  return tab;
}

// The tab to act on: an explicit tab_id, else her tab, else yours (active).
async function targetTab(args) {
  if (args.tab_id != null) return chrome.tabs.get(Number(args.tab_id));
  if (await tabExists(herTab)) return chrome.tabs.get(herTab);
  const tab = await activeTab();
  if (!tab) throw new Error("no open tab");
  return tab;
}

async function waitLoaded(tabId, timeout = 30000) {
  const end = Date.now() + timeout;
  await sleep(300);
  while (Date.now() < end) {
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === "complete") return tab;
    await sleep(250);
  }
  return chrome.tabs.get(tabId);
}

async function run(tabId, func, args = []) {
  try {
    const [res] = await chrome.scripting.executeScript({ target: { tabId }, func, args });
    return res?.result;
  } catch (err) {
    throw new Error(`can't work on this page (${err.message}). Chrome's own pages and the Web Store are off limits.`);
  }
}

// ---------------------------------------------------------------------------
// In-page helpers (run inside the page)
// ---------------------------------------------------------------------------

function pageSummary(maxChars) {
  const sel = 'a[href], button, input:not([type=hidden]), textarea, select, [role=button], [role=link], [role=tab], [role=menuitem], [role=checkbox], [role=switch], [role=option], [role=combobox], [role=textbox], [contenteditable=true], summary, label[for]';
  const out = [];
  let n = 0;
  document.querySelectorAll("[data-sarah-ref]").forEach((e) => e.removeAttribute("data-sarah-ref"));
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || st.visibility === "hidden" || st.display === "none") continue;
    n += 1;
    el.setAttribute("data-sarah-ref", String(n));
    const tag = el.tagName.toLowerCase();
    const kind = el.getAttribute("role") || (tag === "input" ? "input:" + (el.type || "text") : tag);
    const labelFor = el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`)?.innerText : "";
    const label = (el.getAttribute("aria-label") || labelFor || (tag === "input" || tag === "textarea" ? "" : el.innerText)
      || el.getAttribute("placeholder") || el.getAttribute("title") || el.getAttribute("alt") || el.name || "")
      .trim().replace(/\s+/g, " ").slice(0, 80);
    const href = tag === "a" ? (el.getAttribute("href") || "").slice(0, 120) : "";
    let state = "";
    if (["input", "textarea", "select"].includes(tag) && !["checkbox", "radio", "submit", "button"].includes(el.type)) {
      const v = tag === "select" ? el.selectedOptions[0]?.text : el.value;
      if (v) state += ` = '${String(v).slice(0, 60)}'`;
    }
    if (el.type === "checkbox" || el.type === "radio") state += el.checked ? " (checked)" : " (unchecked)";
    const aria = el.getAttribute("aria-checked") || el.getAttribute("aria-selected") || el.getAttribute("aria-expanded");
    if (aria) state += ` (${el.hasAttribute("aria-expanded") ? "expanded" : "selected"}=${aria})`;
    if (el.disabled) state += " (disabled)";
    const where = r.bottom < 0 || r.top > innerHeight ? " (off screen)" : "";
    out.push(`[${n}] ${kind}${label ? ' "' + label + '"' : ""}${state}${href ? " -> " + href : ""}${where}`);
    if (n >= 220) break;
  }
  let text = (document.body ? document.body.innerText : "").replace(/\n{3,}/g, "\n\n").trim();
  const limit = Math.max(500, Math.min(20000, maxChars || 5000));
  const more = text.length > limit;
  text = text.slice(0, limit) + (more ? "\n...[more text: read with a higher max_chars or scroll]" : "");
  return { url: location.href, title: document.title, text, elements: out };
}

function pageAct(action, ref, text, submit, direction, key) {
  const find = () => {
    if (ref != null) {
      const el = document.querySelector(`[data-sarah-ref="${ref}"]`);
      if (!el) throw new Error(`no element [${ref}] (read the page again for fresh numbers)`);
      return el;
    }
    const want = String(text || "").toLowerCase();
    const els = [...document.querySelectorAll("a, button, [role=button], [role=link], input, textarea, select, label, summary")];
    const el = els.find((e) => (e.innerText || e.value || e.getAttribute("aria-label") || "").toLowerCase().includes(want));
    if (!el) throw new Error(`nothing matching "${text}"`);
    return el;
  };
  const setValue = (el, value) => {
    el.focus();
    if (el.isContentEditable) {
      document.execCommand("selectAll", false, null);
      document.execCommand("insertText", false, value);
      return;
    }
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const pressKey = (el, k) => {
    const opts = { key: k, code: k === "Enter" ? "Enter" : k, keyCode: k === "Enter" ? 13 : 0, which: k === "Enter" ? 13 : 0, bubbles: true, cancelable: true };
    const go = el.dispatchEvent(new KeyboardEvent("keydown", opts));
    el.dispatchEvent(new KeyboardEvent("keypress", opts));
    el.dispatchEvent(new KeyboardEvent("keyup", opts));
    if (k === "Enter" && go && el.form) el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit();
  };
  if (action === "click") {
    const el = find();
    el.scrollIntoView({ block: "center" });
    el.click();
    return "clicked";
  }
  if (action === "type") {
    const el = ref != null ? find() : document.activeElement;
    if (!el || el === document.body) throw new Error("give ref of the box to type into");
    el.scrollIntoView({ block: "center" });
    setValue(el, text);
    if (submit) pressKey(el, "Enter");
    return "typed";
  }
  if (action === "select") {
    const el = find();
    const opt = [...el.options].find((o) => o.text.toLowerCase().includes(String(text).toLowerCase()));
    if (!opt) throw new Error(`no option "${text}"`);
    el.value = opt.value;
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return "selected " + opt.text;
  }
  if (action === "press") {
    pressKey(document.activeElement || document.body, key || "Enter");
    return "pressed " + (key || "Enter");
  }
  if (action === "scroll") {
    if (ref != null) { find().scrollIntoView({ block: "center" }); return "scrolled to it"; }
    window.scrollBy({ top: direction === "up" ? -innerHeight * 0.9 : innerHeight * 0.9 });
    return "scrolled";
  }
  if (action === "hover") {
    const el = find();
    el.scrollIntoView({ block: "center" });
    for (const t of ["pointerover", "mouseover", "pointerenter", "mouseenter", "mousemove"]) {
      el.dispatchEvent(new MouseEvent(t, { bubbles: t !== "mouseenter" && t !== "pointerenter" }));
    }
    return "hovering";
  }
  if (action === "check") {
    const el = find();
    const want = !/^(off|false|no|uncheck)/i.test(String(text || "on"));
    const now = el.type === "checkbox" || el.type === "radio" ? el.checked : el.getAttribute("aria-checked") === "true";
    if (now !== want) el.click();
    const after = el.type === "checkbox" || el.type === "radio" ? el.checked : el.getAttribute("aria-checked") === "true";
    return after === want ? `now ${want ? "checked" : "unchecked"}` : "tried, but it didn't change";
  }
  throw new Error("unknown page action " + action);
}

// Where an element is (for real mouse clicks).
function pageCenter(ref) {
  const el = document.querySelector(`[data-sarah-ref="${ref}"]`);
  if (!el) return null;
  el.scrollIntoView({ block: "center" });
  const r = el.getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
}

// Search the page: elements (with refs) and text lines matching the words.
function pageFind(query) {
  const words = String(query || "").toLowerCase().split(/\s+/).filter(Boolean);
  const hit = (s) => { s = (s || "").toLowerCase(); return words.length && words.every((w) => s.includes(w)); };
  const elements = [...document.querySelectorAll("[data-sarah-ref]")]
    .filter((el) => hit(el.innerText) || hit(el.value) || hit(el.getAttribute("aria-label")) || hit(el.getAttribute("placeholder")) || hit(el.getAttribute("href")))
    .slice(0, 25).map((el) => `[${el.getAttribute("data-sarah-ref")}] ${el.tagName.toLowerCase()} "${(el.innerText || el.value || el.getAttribute("aria-label") || "").trim().replace(/\s+/g, " ").slice(0, 80)}"`);
  const lines = (document.body?.innerText || "").split("\n").map((l) => l.trim()).filter((l) => l && hit(l)).slice(0, 25);
  return { query, elements, text: lines };
}

function pageHas(text, selector) {
  if (selector) return !!document.querySelector(selector);
  return (document.body?.innerText || "").toLowerCase().includes(String(text).toLowerCase());
}

function pageExtract(selector) {
  return [...document.querySelectorAll(selector)].slice(0, 300).map((e) => ({
    text: (e.innerText || e.textContent || "").trim().replace(/\s+/g, " ").slice(0, 300),
    href: e.href || e.getAttribute("href") || undefined,
    src: e.src || undefined,
  }));
}

function pageTables() {
  return [...document.querySelectorAll("table")].slice(0, 10).map((t) => [...t.rows].slice(0, 200)
    .map((r) => [...r.cells].map((c) => c.innerText.trim().replace(/\s+/g, " ").slice(0, 200))));
}

// ---------------------------------------------------------------------------
// Actions Sarah can ask for
// ---------------------------------------------------------------------------

const summary = async (tabId, maxChars) => ({ tab_id: tabId, ...(await run(tabId, pageSummary, [maxChars || 5000])) });

const ACTIONS = {
  async tabs() {
    const tabs = await chrome.tabs.query({});
    return tabs.map((t) => ({ tab_id: t.id, title: t.title, url: t.url, active: t.active, hers: t.id === herTab }));
  },

  async open({ url, new_tab = true, focus = true, max_chars }) {
    if (!/^https?:\/\//i.test(url || "")) url = "https://" + String(url || "").replace(/^\/+/, "");
    let tab;
    if (!new_tab && (await tabExists(herTab))) tab = await chrome.tabs.update(herTab, { url, active: focus });
    else tab = await chrome.tabs.create({ url, active: focus });
    herTab = tab.id;
    if (focus) await chrome.windows.update(tab.windowId, { focused: true });
    await waitLoaded(tab.id);
    await sleep(600);
    return summary(tab.id, max_chars);
  },

  // Work in one of Zero's tabs (the one they're on, or a tab_id).
  async use_tab({ tab_id, max_chars }) {
    const tab = tab_id != null ? await chrome.tabs.get(Number(tab_id)) : await activeTab();
    if (!tab) throw new Error("no tab to use");
    herTab = tab.id;
    return summary(tab.id, max_chars);
  },

  async read(args) {
    const tab = await targetTab(args);
    return summary(tab.id, args.max_chars);
  },

  async click(args) { return actThenRead("click", args); },
  async type(args) { return actThenRead("type", args); },
  async select(args) { return actThenRead("select", args); },
  async press(args) { return actThenRead("press", args); },
  async scroll(args) { return actThenRead("scroll", args); },

  async back(args) {
    const tab = await targetTab(args);
    await chrome.tabs.goBack(tab.id);
    await waitLoaded(tab.id);
    return summary(tab.id, args.max_chars);
  },

  async forward(args) {
    const tab = await targetTab(args);
    await chrome.tabs.goForward(tab.id);
    await waitLoaded(tab.id);
    return summary(tab.id, args.max_chars);
  },

  async extract(args) {
    if (!args.text) throw new Error("give a CSS selector in text, e.g. 'h3 a' or '.price'");
    const tab = await targetTab(args);
    const items = await run(tab.id, pageExtract, [args.text]);
    return { url: tab.url, selector: args.text, count: items.length, items };
  },

  async tables(args) {
    const tab = await targetTab(args);
    return { url: tab.url, tables: await run(tab.id, pageTables) };
  },

  // A JPEG of the tab (it's brought to the front first: Chrome can only
  // capture what's showing).
  async screenshot(args) {
    const tab = await targetTab(args);
    await chrome.tabs.update(tab.id, { active: true });
    await chrome.windows.update(tab.windowId, { focused: true });
    await sleep(350);
    const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "jpeg", quality: 60 });
    return { url: tab.url, title: tab.title, jpeg_base64: dataUrl.split(",")[1] };
  },

  async hover(args) { return actThenRead("hover", args); },
  async check(args) { return actThenRead("check", args); },

  // Search the page for words: matching elements (with refs) and text lines.
  async find(args) {
    const tab = await targetTab(args);
    await run(tab.id, pageSummary, [500]); // fresh refs
    return run(tab.id, pageFind, [args.text || ""]);
  },

  // Wait until some text (or a CSS selector in `selector`) shows up.
  async wait_for(args) {
    const tab = await targetTab(args);
    const end = Date.now() + Math.min(60, Number(args.timeout) || 15) * 1000;
    while (Date.now() < end) {
      if (await run(tab.id, pageHas, [args.text || "", args.selector || ""]).catch(() => false)) {
        return { found: true, ...(await summary(tab.id, args.max_chars)) };
      }
      await sleep(500);
    }
    return { found: false, note: `"${args.text || args.selector}" didn't appear`, ...(await summary(tab.id, 1500)) };
  },

  // Several form fields at once: fields = [{ref, text}] (text "on"/"off" for checkboxes).
  async fill(args) {
    const tab = await targetTab(args);
    const results = [];
    for (const f of args.fields || []) {
      const kind = await run(tab.id, (ref) => {
        const el = document.querySelector(`[data-sarah-ref="${ref}"]`);
        return el ? (el.tagName === "SELECT" ? "select" : (el.type === "checkbox" || el.type === "radio" || el.getAttribute("role") === "checkbox") ? "check" : "type") : null;
      }, [Number(f.ref)]);
      if (!kind) { results.push(`[${f.ref}] not found`); continue; }
      results.push(`[${f.ref}] ` + await run(tab.id, pageAct, [kind, Number(f.ref), String(f.text ?? ""), false, "down", ""]));
    }
    if (args.submit) await run(tab.id, pageAct, ["press", null, "", false, "down", "Enter"]);
    await sleep(900);
    await waitLoaded(tab.id, 15000);
    return { filled: results, ...(await summary(tab.id, args.max_chars)) };
  },

  // Only ever closes her own tab.
  async close() {
    if (!(await tabExists(herTab))) return "No tab of mine to close.";
    await chrome.tabs.remove(herTab);
    herTab = null;
    return "Closed my tab.";
  },
};

// Real (trusted) input through Chrome's debugger protocol, for sites that
// ignore events made by page scripts. Chrome shows a "debugging" bar while
// it's attached; it's detached right after.
async function withDebugger(tabId, fn) {
  const target = { tabId };
  await chrome.debugger.attach(target, "1.3");
  try {
    return await fn((method, params) => chrome.debugger.sendCommand(target, method, params));
  } finally {
    try { await chrome.debugger.detach(target); } catch {}
  }
}

async function trustedClick(tabId, ref) {
  const pt = await run(tabId, pageCenter, [ref]);
  if (!pt) throw new Error(`no element [${ref}]`);
  await sleep(150);
  await withDebugger(tabId, async (send) => {
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: pt.x, y: pt.y });
    await send("Input.dispatchMouseEvent", { type: "mousePressed", x: pt.x, y: pt.y, button: "left", clickCount: 1 });
    await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: pt.x, y: pt.y, button: "left", clickCount: 1 });
  });
}

async function trustedType(tabId, ref, text, submit) {
  const pt = await run(tabId, pageCenter, [ref]);
  if (!pt) throw new Error(`no element [${ref}]`);
  await withDebugger(tabId, async (send) => {
    await send("Input.dispatchMouseEvent", { type: "mousePressed", x: pt.x, y: pt.y, button: "left", clickCount: 1 });
    await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: pt.x, y: pt.y, button: "left", clickCount: 1 });
    await send("Input.dispatchKeyEvent", { type: "keyDown", key: "a", code: "KeyA", modifiers: 2, windowsVirtualKeyCode: 65 });
    await send("Input.dispatchKeyEvent", { type: "keyUp", key: "a", code: "KeyA", modifiers: 2, windowsVirtualKeyCode: 65 });
    await send("Input.insertText", { text });
    if (submit) {
      await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, text: "\r" });
      await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
    }
  });
}

// What the page looks like, coarsely: did an action change anything?
function pageSignature() {
  const body = document.body ? document.body.innerText : "";
  const field = document.activeElement && "value" in document.activeElement ? document.activeElement.value : "";
  return `${location.href}|${document.title}|${body.length}|${body.slice(0, 200)}|${field}|${scrollY}`;
}

async function actThenRead(action, args) {
  const tab = await targetTab(args);
  const ref = args.ref != null ? Number(args.ref) : null;
  const before = await run(tab.id, pageSignature);
  const note = await run(tab.id, pageAct, [action, ref, args.text ?? "", !!args.submit, args.direction || "down", args.key || ""]);
  await sleep(action === "scroll" ? 500 : 900);
  // A click may have opened a new tab: follow it.
  const [newest] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  let id = tab.id;
  if (newest && newest.id !== tab.id && newest.openerTabId === tab.id) { herTab = newest.id; id = newest.id; }
  await waitLoaded(id, 15000);
  let did = note;
  let changed = id !== tab.id || (await run(id, pageSignature)) !== before;
  // Some sites ignore scripted clicks/typing: do it again as real input.
  if (!changed && id === tab.id && ref != null && (action === "click" || action === "type")) {
    try {
      if (action === "click") await trustedClick(tab.id, ref);
      else await trustedType(tab.id, ref, String(args.text ?? ""), !!args.submit);
      await sleep(1000);
      await waitLoaded(tab.id, 15000);
      changed = (await run(tab.id, pageSignature)) !== before;
      did += " (then again as real mouse/keyboard input)";
    } catch (err) {
      did += ` (real input failed: ${err.message})`;
    }
  }
  const result = { did, changed, ...(await summary(id, args.max_chars)) };
  if (!changed) {
    result.warning = "Nothing on the page changed after that. It probably didn't work: check before going on.";
  }
  return result;
}
