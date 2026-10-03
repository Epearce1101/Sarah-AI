// Lip-sync: synthetic vowel spectra (two formant peaks) should pick the
// matching VRM mouth shape.
import assert from "node:assert/strict";
import { visemesFromSpectrum, VISEMES } from "../renderer/scripts/avatar3d/visemes.js";

const binHz = 48000 / 1024;
function vowel(f1, f2) {
  const db = new Float32Array(512);
  for (let i = 0; i < db.length; i++) {
    const f = i * binHz;
    const peak = (c, w) => Math.exp(-(((f - c) / w) ** 2));
    const power = 1e-9 + peak(f1, 120) * 1e-3 + peak(f2, 220) * 4e-4 + peak(220, 60) * 2e-4; // + pitch
    db[i] = 10 * Math.log10(power);
  }
  return db;
}
const top = (w) => VISEMES.reduce((a, b) => (w[a] >= w[b] ? a : b));
const cases = { aa: [850, 1500], ee: [300, 2600], ih: [560, 2100], ou: [330, 850], oh: [560, 950] };
for (const [want, [f1, f2]] of Object.entries(cases)) {
  const w = visemesFromSpectrum(vowel(f1, f2), binHz, 0.5);
  assert.equal(top(w), want, `${want}: got ${JSON.stringify(w)}`);
}
// Silence closes the mouth.
const quiet = visemesFromSpectrum(vowel(850, 1500), binHz, 0.01);
assert.ok(VISEMES.every((k) => quiet[k] === 0));
// Louder opens wider.
const soft = visemesFromSpectrum(vowel(850, 1500), binHz, 0.2);
const loud = visemesFromSpectrum(vowel(850, 1500), binHz, 0.5);
assert.ok(loud.aa > soft.aa);
// Every weight stays within 0..1, even with -Infinity bins.
const db = vowel(500, 2000); db[3] = -Infinity;
const w = visemesFromSpectrum(db, binHz, 1);
assert.ok(VISEMES.every((k) => w[k] >= 0 && w[k] <= 1));
console.log("visemes ok");

// Voice-engine timelines.
import { parseTimeline, timelineShape } from "../renderer/scripts/avatar3d/visemes.js";
const tl = parseTimeline('[[0,"m",80],[80,"a",200],[280,"u",150],[430,"_",100]]');
assert.equal(tl.length, 4);
assert.equal(parseTimeline("nope"), null);
assert.equal(parseTimeline(null), null);
const at = (t) => timelineShape(tl, t, 1);
assert.ok(VISEMES.every((k) => at(0.04)[k] === 0), "lips closed on m");
assert.ok(at(0.15).aa > 0.9, "open on a");
assert.ok(at(0.33).ou > 0.6 && at(0.33).aa === 0, "rounded on u");
const mid = at(0.26); // late in "a": already moving toward "u"
assert.ok(mid.aa > mid.ou && mid.ou > 0.2, `coarticulation ${JSON.stringify(mid)}`);
assert.ok(VISEMES.every((k) => at(0.7)[k] === 0), "closed after the end");
assert.ok(timelineShape(tl, 0.15, 0.4).aa < 0.45, "quieter -> less open");
console.log("timelines ok");
