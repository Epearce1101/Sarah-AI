// Unit tests for the stage-direction parser (renderer/scripts/avatar3d/cues.js).
import assert from "node:assert/strict";
import { parseCues, stripCues } from "../renderer/scripts/avatar3d/cues.js";

const view = (r) => r.cues.map((c) => [c.type, c.value, c.at, c.amount]);

// Paired tags: removed from text; offsets point at the following word.
{
  const r = parseCues("<face>happy</face>Hi! <gesture>wave</gesture>Look <point>chat</point>there.");
  assert.equal(r.text, "Hi! Look there.");
  assert.deepEqual(view(r), [["face", "happy", 0, undefined], ["gesture", "wave", 4, undefined], ["point", "chat", 9, undefined]]);
  assert.equal(r.text.slice(9), "there.");
}

// Removing a tag between two spaces doesn't leave a double space.
{
  const r = parseCues("one <look>left</look> two");
  assert.equal(r.text, "one two");
  assert.equal(r.cues[0].at, 4);
}

// Strength suffix, case and spacing are normalised; motion -> gesture.
{
  const r = parseCues("<Face> Surprised:0.4 </Face>Oh <motion>shake head</motion>no");
  assert.deepEqual(view(r), [["face", "surprised", 0, 0.4], ["gesture", "shake_head", 3, undefined]]);
  assert.equal(parseCues("<face>happy:7</face>x").cues[0].amount, 1);
}

// Attribute forms.
{
  const r = parseCues('a <gesture name="nod"/>b <face value=\'smug\'>c');
  assert.equal(r.text, "a b c");
  assert.deepEqual(r.cues.map((c) => c.value), ["nod", "smug"]);
}

// Legacy bare tags only when whitelisted; unknown tags are left alone.
{
  const bare = new Set(["wave", "nod"]);
  const r = parseCues("Hi <wave/> there <nod></nod> <b>bold</b> <div>", { bareGestures: bare });
  assert.equal(r.text, "Hi there <b>bold</b> <div>");
  assert.deepEqual(r.cues.map((c) => c.value), ["wave", "nod"]);
  assert.equal(parseCues("Hi <wave/>").text, "Hi <wave/>");
}

// Streaming: a half-typed tag at the end is hidden, not shown as text.
{
  for (const partial of ["Hello <", "Hello <fa", "Hello <face>hap", "Hello <face>happy</fa", "Hello <gesture name=\"wa"]) {
    const r = parseCues(partial, { streaming: true });
    assert.equal(r.text, "Hello ", partial);
  }
  // ...but a finished tag and a lone "<" in finished text survive.
  assert.equal(parseCues("a < b", { streaming: false }).text, "a < b");
  assert.equal(parseCues("x <face>sad</face>", { streaming: true }).cues.length, 1);
}

// Empty values are dropped; stripCues returns text only.
{
  assert.deepEqual(parseCues("<face></face>hi").cues, []);
  assert.equal(stripCues("<look>user</look>Hey"), "Hey");
}

console.log(JSON.stringify({ ok: true, checked: "cues" }));
