// Entry point for renderer/avatar3d/index.html (bundled to dist/sarah-avatar.js).
//
// Settings come from avatar.config.json, and can be overridden in the URL:
//   index.html?model=models/other.vrm&framing=full&dev=1&wake=1
import { SarahAvatar } from './sarahAvatar.js';
import { SarahBridge } from './sarahBridge.js';
import { VRM_EMOTIONS } from './expressions.js';

export { SarahAvatar, SarahBridge };
window.SarahAvatarLib = { SarahAvatar, SarahBridge };

const DEFAULTS = {
  model: 'models/sarah.vrm',
  fallbackModel: 'models/AvatarSample_F.vrm',
  animations: 'animations/animations.json',
  framing: 'upper',
  api: '',
  pollNotifications: true,
  pollWake: false,
};

async function loadConfig() {
  let config = { ...DEFAULTS };
  try {
    const res = await fetch('avatar.config.json', { cache: 'no-store' });
    if (res.ok) config = { ...config, ...(await res.json()) };
  } catch { /* no config file: defaults are fine */ }
  const params = new URLSearchParams(location.search);
  for (const key of ['model', 'animations', 'framing', 'api']) {
    if (params.has(key)) config[key] = params.get(key);
  }
  if (params.has('wake')) config.pollWake = params.get('wake') !== '0';
  config.dev = params.has('dev') && params.get('dev') !== '0';
  return config;
}

function setStatus(text, isError = false) {
  const el = document.getElementById('status');
  if (!el) return;
  el.textContent = text;
  el.classList.toggle('error', isError);
  el.hidden = !text;
}

async function loadFirstModel(avatar, urls) {
  let lastError;
  for (const url of urls.filter(Boolean)) {
    try {
      await avatar.loadModel(url, (e) => {
        if (e.total) setStatus(`Loading Sarah… ${Math.round((e.loaded / e.total) * 100)}%`);
      });
      return url;
    } catch (err) {
      lastError = err;
    }
  }
  throw lastError || new Error('No model configured');
}

async function loadManifest(url) {
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  const data = await res.json();
  return Array.isArray(data) ? data : data.animations || [];
}

function buildDevPanel(avatar, bridge, report) {
  const panel = document.getElementById('dev-panel');
  if (!panel) return;
  panel.innerHTML = '';
  const section = (title) => {
    const h = document.createElement('h3');
    h.textContent = title;
    panel.appendChild(h);
    const row = document.createElement('div');
    row.className = 'row';
    panel.appendChild(row);
    return row;
  };
  const button = (row, label, onClick) => {
    const b = document.createElement('button');
    b.textContent = label;
    b.addEventListener('click', onClick);
    row.appendChild(b);
  };

  const emotions = section('Emotion');
  for (const e of ['neutral', ...VRM_EMOTIONS]) button(emotions, e, () => avatar.setEmotion(e, 0.9));

  const anims = section(`Animations (${report.loaded.length} loaded, ${report.missing.length} missing)`);
  if (avatar.groupNames.length === 0) {
    anims.textContent = 'None yet: add Mixamo files to renderer/avatar3d/animations/ (see README).';
  }
  for (const g of avatar.groupNames) button(anims, g, () => avatar.play(g));

  const talk = section('Talking');
  button(talk, 'mouth test (3 s)', () => avatar.fakeTalk(3000));
  button(talk, 'say hello (TTS)', () => bridge.say('Hello Creator! This is my new body.'));

  const chat = section('Chat with Sarah');
  const input = document.createElement('input');
  input.placeholder = 'Type a message and press Enter';
  input.addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter' || !input.value.trim()) return;
    const msg = input.value.trim();
    input.value = '';
    try { await bridge.chat(msg); } catch (err) { setStatus(`Chat failed: ${err.message}`, true); }
  });
  chat.appendChild(input);

  if (report.missing.length) {
    console.info('[avatar] animations not found (add the files to use them):',
      report.missing.map((m) => `${m.name}: ${m.error}`));
  }
}

async function start() {
  const canvas = document.getElementById('sarah-avatar');
  if (!canvas) return; // loaded as a library inside another page
  const config = await loadConfig();
  const avatar = new SarahAvatar(canvas, { framing: config.framing });
  const bridge = new SarahBridge(avatar, config);
  window.sarahAvatar = avatar;
  window.sarahBridge = bridge;

  setStatus('Loading Sarah…');
  try {
    await loadFirstModel(avatar, [config.model, config.fallbackModel]);
  } catch (err) {
    setStatus('No model found. Put your VRM at renderer/avatar3d/models/sarah.vrm, '
      + 'or run: python renderer/avatar3d/download_sample_model.py', true);
    console.error('[avatar] model load failed:', err);
    window.sarahAvatarReady = { ok: false, error: String(err) };
    return;
  }

  let report = { loaded: [], missing: [] };
  try {
    report = await avatar.loadAnimations(await loadManifest(config.animations),
      config.animations.replace(/[^/]*$/, ''));
  } catch (err) {
    console.warn('[avatar] no animation list:', err);
  }
  setStatus('');
  bridge.start();

  const panel = document.getElementById('dev-panel');
  const showPanel = (show) => {
    if (!panel) return;
    panel.hidden = !show;
    if (show) buildDevPanel(avatar, bridge, report);
  };
  showPanel(config.dev);
  window.addEventListener('keydown', (e) => {
    if (e.key.toLowerCase() === 'd' && !(e.target instanceof HTMLInputElement)) showPanel(panel.hidden);
  });

  window.sarahAvatarReady = { ok: true, ...report };
  window.dispatchEvent(new CustomEvent('sarah-avatar-ready', { detail: report }));
}

start();
