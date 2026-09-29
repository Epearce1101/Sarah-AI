// Live voice: echo filter (renderer/scripts/core/live-voice.js).
import assert from "node:assert/strict";

globalThis.window = { PY_PORT: 8907 };
const { isEcho, wakeWord } = await import("../renderer/scripts/core/live-voice.js");

const said = "That depends, what are you in the mood for? Something quick or comforting?";
assert.equal(isEcho("what are you in the mood for", said), true, "her own words leaking back");
assert.equal(isEcho("something quick", said), true);
assert.equal(isEcho("I want pasta tonight", said), false, "the user's answer is not an echo");
assert.equal(isEcho("quick", said), true, "a single word she just said");
assert.equal(isEcho("pasta", said), false);
assert.equal(isEcho("anything", ""), false, "nothing said recently: never an echo");
assert.equal(isEcho("", said), true, "empty is dropped");

console.log(JSON.stringify({ ok: true, checked: "live-voice" }));

// Wake word: her name starts (or is anywhere in) a request.
assert.deepEqual(wakeWord("Hey Sarah, what's the time?"), { heard: true, rest: "what's the time?" });
assert.deepEqual(wakeWord("Sara open YouTube"), { heard: true, rest: "open YouTube" });
assert.deepEqual(wakeWord("okay sarah."), { heard: true, rest: "" });
assert.deepEqual(wakeWord("What do you think, Sarah?"), { heard: true, rest: "What do you think, Sarah?" });
assert.equal(wakeWord("I got shaders loading.").heard, false);
assert.equal(wakeWord("Saratoga springs").heard, false, "not inside other words");