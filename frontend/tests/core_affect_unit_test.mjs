// Sentence tone -> face (renderer/scripts/avatar3d/affect.js).
import assert from "node:assert/strict";
import { affectCues, affectOf, sentences } from "../renderer/scripts/avatar3d/affect.js";

const face = (s) => affectOf(s)?.face ?? null;
assert.equal(face("Congrats, that's amazing!!"), "excited");
assert.equal(face("Oh no, I'm so sorry to hear that."), "sad");
assert.equal(face("Haha, you got me."), "laugh");
assert.equal(face("Hmm, let me think."), "thinking");
assert.equal(face("Thanks!"), "smile");
assert.equal(face("Here is the code."), null, "flat sentences leave her face alone");
assert.equal(face(""), null);

assert.deepEqual(sentences("One. Two! Three").map((s) => [s.text.trim(), s.at]), [["One.", 0], ["Two!", 5], ["Three", 10]]);

// Each sentence gets its tone at its own offset; her own tags win.
const text = "Wow! That is great. Hmm, maybe.";
const auto = affectCues(text);
assert.deepEqual(auto.map((c) => [c.value, c.at]), [["surprised", 0], ["smile", 5], ["thinking", 20]]);
assert.ok(auto.every((c) => c.auto && c.type === "face"));
const withOwn = affectCues(text, [{ type: "face", value: "sad", at: 5 }]);
assert.deepEqual(withOwn.map((c) => c.value), ["surprised", "thinking"]);
assert.deepEqual(affectCues(text, [{ type: "feel", value: "calm", at: 0 }]).map((c) => c.value), ["smile", "thinking"]);

console.log(JSON.stringify({ ok: true, checked: "affect" }));
