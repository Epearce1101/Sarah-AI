const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const renderer = path.join(ROOT, "renderer");

function read(rel) {
  return fs.readFileSync(path.join(ROOT, rel), "utf8");
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function hexToRgb(hex) {
  const raw = hex.replace("#", "");
  const value = raw.length === 3
    ? raw.split("").map((c) => c + c).join("")
    : raw;
  const num = Number.parseInt(value, 16);
  return [(num >> 16) & 255, (num >> 8) & 255, num & 255];
}

function luminance([r, g, b]) {
  const linear = [r, g, b].map((channel) => {
    const c = channel / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return (0.2126 * linear[0]) + (0.7152 * linear[1]) + (0.0722 * linear[2]);
}

function contrast(hexA, hexB) {
  const a = luminance(hexToRgb(hexA));
  const b = luminance(hexToRgb(hexB));
  const light = Math.max(a, b);
  const dark = Math.min(a, b);
  return (light + 0.05) / (dark + 0.05);
}

const forbiddenColor = /#[0-9A-Fa-f]{3,8}|rgba?\(|hsla?\(|linear-gradient|radial-gradient/;
const nonThemeFiles = [
  "renderer/styles/a1-components.css",
  "renderer/styles/styles.css",
  "renderer/styles/sarah-avatar.css",
  "renderer/index.html",
  "renderer/dashboard.js",
  // Modules split out of dashboard.js follow the same token contract.
  ...fs.readdirSync(path.join(renderer, "scripts", "core"))
    .filter((name) => name.endsWith(".js"))
    .map((name) => `renderer/scripts/core/${name}`),
];

for (const rel of nonThemeFiles) {
  const text = read(rel);
  assert(!forbiddenColor.test(text), `${rel} contains non-token color or gradient literals`);
}

const html = read("renderer/index.html");
assert(!/<style\b/i.test(html), "index.html still contains inline <style> blocks");
assert(!/\sonclick\s*=/i.test(html), "index.html still contains inline onclick handlers");

const styleAttrs = [...html.matchAll(/\sstyle="([^"]*)"/gi)].map((match) => match[1].trim());
for (const value of styleAttrs) {
  const allowed = /^width:\s*0%$/i.test(value)
    || /^display:\s*none;?$/i.test(value)
    || /^display:none;?$/i.test(value);
  assert(allowed, `index.html has non-state inline style: ${value}`);
}

const avatarCss = read("renderer/styles/sarah-avatar.css");
const forbiddenBroadSelectors = [
  /^body\b/m,
  /^html\b/m,
  /^\.top-bar\b/m,
  /^\.layout\b/m,
  /^\.sidebar\b/m,
  /^\.sidebar-btn\b/m,
  /^\.pill-btn\b/m,
  /^\.chat-log\b/m,
  /^\.chat-bubble\b/m,
  /^\.sv-modal\b/m,
  /^\.sv-window\b/m,
];
for (const selector of forbiddenBroadSelectors) {
  assert(!selector.test(avatarCss), `sarah-avatar.css still owns broad selector ${selector}`);
}

const themeCss = read("renderer/styles/theme.css");
const vars = new Map([...themeCss.matchAll(/--([A-Za-z0-9_-]+)\s*:\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
assert(vars.size >= 70, `theme.css should define the A1 token set, found ${vars.size}`);

const textMain = vars.get("text-main");
const textInvert = vars.get("text-invert");
const bgVoid = vars.get("bg-void");
const primary400 = vars.get("primary-400");
assert(contrast(textMain, bgVoid) >= 7, "text-main contrast on bg-void is below AAA text target");
assert(contrast(textInvert, primary400) >= 4.5, "text-invert contrast on primary-400 is below AA target");

console.log(JSON.stringify({
  ok: true,
  tokenCount: vars.size,
  inlineStyleAttrs: styleAttrs.length,
  textOnVoidContrast: Number(contrast(textMain, bgVoid).toFixed(2)),
  invertOnPrimaryContrast: Number(contrast(textInvert, primary400).toFixed(2)),
}, null, 2));
