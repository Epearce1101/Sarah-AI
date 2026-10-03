// The see-through screen overlay: Sarah's marks (a hand-drawn circle, arrow,
// underline or box around what she's talking about, with a short label) and,
// in pet mode, her speech bubble. Coordinates arrive in this window's own
// pixels (DIPs); the main process routes each mark to the right display.

const canvas = document.getElementById("marks");
const ctx = canvas.getContext("2d");
const bubble = document.getElementById("bubble");
const INK = "#ff4fd8";
const DRAW_MS = 450;
const FADE_MS = 400;
let marks = [];
let raf = 0;

function resize() {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(innerWidth * dpr);
  canvas.height = Math.round(innerHeight * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
addEventListener("resize", resize);
resize();

// Small, repeatable wobble so circles look drawn by hand.
function wobble(seed, t) {
  return Math.sin(t * 3 + seed) * 0.03 + Math.sin(t * 7 + seed * 2.3) * 0.015;
}

function strokeStyle(alpha) {
  ctx.globalAlpha = alpha;
  ctx.strokeStyle = INK;
  ctx.fillStyle = INK;
  ctx.lineWidth = 4;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.shadowColor = "rgba(255, 79, 216, 0.85)";
  ctx.shadowBlur = 12;
}

function drawCircle(m, p) {
  const [x, y, w, h] = m.rect;
  const pad = m.approx ? 26 : 12;
  const cx = x + w / 2, cy = y + h / 2;
  const rx = w / 2 + pad, ry = h / 2 + pad;
  const start = -2.2;
  const sweep = (Math.PI * 2 + 0.45) * p;   // a little overshoot, like a pen
  ctx.beginPath();
  for (let a = 0; a <= sweep; a += 0.04) {
    const k = 1 + wobble(m.seed, a);
    const px = cx + Math.cos(start + a) * rx * k;
    const py = cy + Math.sin(start + a) * ry * k;
    if (a === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
  }
  ctx.stroke();
  return { top: cy - ry, bottom: cy + ry, left: cx - rx, right: cx + rx };
}

function drawUnderline(m, p) {
  const [x, y, w, h] = m.rect;
  const yy = y + h + 6;
  ctx.beginPath();
  for (let i = 0; i <= 40 * p; i++) {
    const t = i / 40;
    const px = x - 4 + (w + 8) * t;
    const py = yy + Math.sin(t * Math.PI * 3 + m.seed) * 2;
    if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
  }
  ctx.stroke();
  return { top: y, bottom: yy + 4, left: x, right: x + w };
}

function drawBox(m, p) {
  const [x, y, w, h] = m.rect;
  const pad = m.approx ? 14 : 6;
  ctx.globalAlpha *= Math.min(1, p * 1.5);
  ctx.beginPath();
  ctx.roundRect(x - pad, y - pad, w + pad * 2, h + pad * 2, 8);
  ctx.stroke();
  return { top: y - pad, bottom: y + h + pad, left: x - pad, right: x + w + pad };
}

function drawArrow(m, p) {
  const [x, y, w, h] = m.rect;
  const tx = x + w / 2, ty = y + h / 2;
  // Come in from whichever side has room, 110 px away.
  const fromLeft = tx > innerWidth / 2;
  const fromAbove = ty > 140;
  const sx = tx + (fromLeft ? -110 : 110), sy = ty + (fromAbove ? -90 : 90);
  // Stop at the edge of the thing, not its middle.
  const ex = tx + Math.max(-w / 2 - 6, Math.min(w / 2 + 6, (sx - tx) * 0.5));
  const ey = ty + Math.max(-h / 2 - 6, Math.min(h / 2 + 6, (sy - ty) * 0.5));
  const cx = sx + (ex - sx) * p, cy = sy + (ey - sy) * p;
  ctx.beginPath();
  ctx.moveTo(sx, sy);
  ctx.quadraticCurveTo((sx + cx) / 2 + 18, (sy + cy) / 2 - 18, cx, cy);
  ctx.stroke();
  if (p > 0.85) {
    const ang = Math.atan2(cy - sy, cx - sx);
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx - Math.cos(ang - 0.45) * 16, cy - Math.sin(ang - 0.45) * 16);
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx - Math.cos(ang + 0.45) * 16, cy - Math.sin(ang + 0.45) * 16);
    ctx.stroke();
  }
  return { top: Math.min(sy, ty) - 6, bottom: Math.max(sy, ty), left: Math.min(sx, tx), right: Math.max(sx, tx) };
}

const DRAW = { circle: drawCircle, underline: drawUnderline, box: drawBox, arrow: drawArrow };

function placeLabel(m, bounds) {
  if (!m.label) return;
  if (!m.el) {
    m.el = document.createElement("div");
    m.el.className = "label";
    m.el.textContent = m.label;
    document.body.appendChild(m.el);
    requestAnimationFrame(() => m.el.classList.add("show"));
  }
  const r = m.el.getBoundingClientRect();
  const above = bounds.top - r.height - 8 > 4;
  const left = Math.max(6, Math.min(innerWidth - r.width - 6, (bounds.left + bounds.right) / 2 - r.width / 2));
  m.el.style.left = `${left}px`;
  m.el.style.top = `${above ? bounds.top - r.height - 8 : bounds.bottom + 8}px`;
}

function frame(now) {
  ctx.clearRect(0, 0, innerWidth, innerHeight);
  marks = marks.filter((m) => {
    if (now < m.until) return true;
    m.el?.remove();
    return false;
  });
  for (const m of marks) {
    const p = Math.min(1, (now - m.born) / DRAW_MS);
    const fade = Math.min(1, Math.max(0, (m.until - now) / FADE_MS));
    strokeStyle(fade);
    const bounds = (DRAW[m.style] || drawCircle)(m, p);
    if (m.el) m.el.style.opacity = String(fade);
    placeLabel(m, bounds);
  }
  raf = marks.length ? requestAnimationFrame(frame) : 0;
}

function addMarks({ marks: list = [], seconds = 8 } = {}) {
  const now = performance.now();
  for (const m of list) {
    marks.push({ ...m, born: now, until: now + seconds * 1000, seed: Math.random() * 10 });
  }
  if (!raf) raf = requestAnimationFrame(frame);
}

function clearMarks() {
  for (const m of marks) m.el?.remove();
  marks = [];
  ctx.clearRect(0, 0, innerWidth, innerHeight);
}

let bubbleTimer = 0;
function showBubble({ text = "", x, y, seconds } = {}) {
  const words = String(text || "").trim();
  if (!words) {
    bubble.classList.remove("show");
    return;
  }
  const same = bubble.textContent === words && bubble.classList.contains("show");
  bubble.textContent = words.length > 260 ? `${words.slice(0, 257)}…` : words;
  // Keep it on screen: above her head, nudged in from the edges.
  const half = Math.min(140, bubble.offsetWidth / 2 || 140);
  bubble.style.left = `${Math.max(half + 6, Math.min(innerWidth - half - 6, x))}px`;
  bubble.style.top = `${Math.max(bubble.offsetHeight + 12, y)}px`;
  bubble.classList.add("show");
  if (same) return; // just following her as she moves
  clearTimeout(bubbleTimer);
  const ms = (seconds || Math.min(12, 2.5 + words.length * 0.055)) * 1000;
  bubbleTimer = setTimeout(() => bubble.classList.remove("show"), ms);
}

window.sarahOverlay?.onMarks(addMarks);
window.sarahOverlay?.onClear(clearMarks);
window.sarahOverlay?.onBubble(showBubble);
window.SARAH_OVERLAY = { addMarks, clearMarks, showBubble }; // for testing
