// Unit tests for the streaming pieces split out of dashboard.js:
// SarahBackend.chatStream (SSE parsing) and SarahTTS.createStream (sentence
// queue). Runs in plain Node — no Electron or backend needed.
import assert from "node:assert/strict";

globalThis.window = { PY_PORT: 8907 };
const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
};
globalThis.performance ??= { now: () => Date.now() };

const { SarahBackend } = await import("../renderer/scripts/core/backend-client.js");
const { SarahTTS } = await import("../renderer/scripts/core/tts.js");

const tick = () => new Promise((r) => setTimeout(r, 0));

function sseResponse(parts, { status = 200 } = {}) {
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      for (const p of parts) controller.enqueue(encoder.encode(p));
      controller.close();
    },
  });
  return { ok: status < 400, status, body };
}

// --- chatStream: deltas split across chunk boundaries, then done ------------
{
  const payload = JSON.stringify({ reply: "Hello there. <motion>wave</motion>", assistant_message_id: 7 });
  const wire = [
    'event: delta\ndata: {"text": "Hel"}\n\n',
    'event: delta\ndata: {"te',            // split mid-JSON
    'xt": "lo there."}\n\nevent: del',     // split mid-event
    'ta\ndata: {"text": " <motion>wave</motion>"}\n\n',
    `event: done\ndata: ${payload}\n\n`,
  ];
  let sent;
  globalThis.fetch = async (url, init) => {
    sent = { url, body: JSON.parse(init.body) };
    return sseResponse(wire);
  };
  const deltas = [];
  const final = await new SarahBackend().chatStream("hi", 12, { regenerate: true, onDelta: (t) => deltas.push(t) });
  assert.deepEqual(deltas, ["Hel", "lo there.", " <motion>wave</motion>"]);
  assert.equal(final.assistant_message_id, 7);
  assert.ok(sent.url.endsWith("/api/chat/stream"));
  assert.deepEqual(sent.body, { message: "hi", from_creator: true, conversation_id: 12, regenerate: true });
}

// --- chatStream: error event throws; 404 is marked safe to fall back --------
{
  globalThis.fetch = async () => sseResponse(['event: error\ndata: {"detail": "boom"}\n\n']);
  await assert.rejects(new SarahBackend().chatStream("x"), /boom/);

  globalThis.fetch = async () => sseResponse([], { status: 404 });
  const err = await new SarahBackend().chatStream("x").catch((e) => e);
  assert.equal(err.streamUnavailable, true);

  globalThis.fetch = async () => sseResponse([], { status: 500 });
  const err500 = await new SarahBackend().chatStream("x").catch((e) => e);
  assert.equal(err500.streamUnavailable, false, "a 500 may already have saved the turn; must not fall back");

  globalThis.fetch = async () => sseResponse(['event: delta\ndata: {"text": "partial"}\n\n']);
  await assert.rejects(new SarahBackend().chatStream("x"), /without a reply/);
}

// --- createStream: speaks sentences as they complete, in order, once --------
function fakeTTS() {
  const requested = [];
  const played = [];
  const tts = new SarahTTS({ tts: async (text) => { requested.push(text); return `url:${text}`; } });
  tts.setEnabled(true);
  tts._startClip = async (url) => { played.push(url.slice(4)); return Promise.resolve(); };
  tts._stopLive2DLipSync = () => {};
  return { tts, requested, played };
}

{
  const { tts, requested, played } = fakeTTS();
  const speech = tts.createStream();
  let text = "";
  for (const piece of ["Sure thing", "! Here is the first ", "sentence of the answer. ", "And a second one", " follows here."]) {
    text += piece;
    speech.push(text);
    await tick();
  }
  // The first sentence was requested before the reply finished.
  assert.equal(requested.length, 1, `expected 1 early clip, got ${JSON.stringify(requested)}`);
  assert.equal(requested[0], "Sure thing! Here is the first sentence of the answer.");
  speech.finish(text, { emotion: "happy", intensity: 0.6 });
  for (let i = 0; i < 5; i++) await tick();
  assert.deepEqual(played, [
    "Sure thing! Here is the first sentence of the answer.",
    "And a second one follows here.",
  ]);
}

// --- createStream: final text normalized differently still isn't repeated ---
{
  const { tts, played } = fakeTTS();
  const speech = tts.createStream();
  speech.push("First sentence is right here. Then more");
  await tick();
  speech.finish("First sentence is right here.\n\n\nThen more text.");
  for (let i = 0; i < 5; i++) await tick();
  assert.deepEqual(played, ["First sentence is right here.", "Then more text."]);
}

// --- stop() cancels a stream in flight --------------------------------------
{
  const { tts, played } = fakeTTS();
  const speech = tts.createStream();
  tts.stop();
  speech.push("This sentence should never be spoken at all. Nor this one.");
  speech.finish("This sentence should never be spoken at all. Nor this one.");
  for (let i = 0; i < 5; i++) await tick();
  assert.deepEqual(played, []);
}

// --- voice off -> no stream --------------------------------------------------
{
  const tts = new SarahTTS({ tts: async () => "u" });
  tts.setEnabled(false);
  assert.equal(tts.createStream(), null);
}

console.log(JSON.stringify({ ok: true, checked: "core-streaming" }));
