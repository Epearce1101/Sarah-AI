// Reading a face: expressions from blendshapes, nods/shakes from head angles.
import assert from "node:assert/strict";
import { expressionOf, headAngles, swings } from "../renderer/scripts/core/face-logic.js";

const bs = (o) => Object.entries(o).map(([categoryName, score]) => ({ categoryName, score }));
assert.equal(expressionOf(bs({ mouthSmileLeft: 0.8, mouthSmileRight: 0.7 })), "smiling");
assert.equal(expressionOf(bs({ jawOpen: 0.6, browInnerUp: 0.7 })), "surprised");
assert.equal(expressionOf(bs({ mouthFrownLeft: 0.5, mouthFrownRight: 0.4 })), "frowning");
assert.equal(expressionOf(bs({ mouthSmileLeft: 0.1, jawOpen: 0.1 })), "neutral");
assert.equal(expressionOf(bs({ jawOpen: 0.6, browInnerUp: 0.7, mouthSmileLeft: 0.9, mouthSmileRight: 0.9 })), "smiling",
  "laughing with an open mouth is a smile, not surprise");

// Identity rotation -> level head; a roll about the camera axis shows up as roll.
const id = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
const level = headAngles(id);
assert.ok(Math.abs(level.pitch) < 1e-6 && Math.abs(level.yaw) < 1e-6 && Math.abs(level.roll) < 1e-6);
const a = (20 * Math.PI) / 180;
const rolled = headAngles([Math.cos(a), Math.sin(a), 0, 0, -Math.sin(a), Math.cos(a), 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
assert.ok(Math.abs(rolled.roll - 20) < 1e-6 && Math.abs(rolled.pitch) < 1e-6);

// Nodding: pitch goes down, up, down within a second.
const t0 = 1000;
const nod = [0, 8, -2, 9, 0].map((pitch, i) => ({ t: t0 + i * 200, pitch }));
assert.ok(swings(nod, "pitch", 6, t0 + 900) >= 2);
const still = [0, 1, 2, 1, 0].map((pitch, i) => ({ t: t0 + i * 200, pitch }));
assert.equal(swings(still, "pitch", 6, t0 + 900), 0);

console.log(JSON.stringify({ ok: true, checked: "face-logic" }));
