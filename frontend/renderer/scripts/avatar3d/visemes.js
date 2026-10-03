// Mouth shapes from the sound of her voice.
//
// Vowels differ by their first two formants: F1 rises as the jaw opens
// (i/u ~300 Hz, a ~850 Hz) and F2 rises as the tongue moves forward
// (u/o ~900 Hz, i ~2600 Hz). We look for humps in a smoothed spectrum and
// pick the viseme whose typical (F1, F2) they match best:
//
//            back (low F2)   front (high F2)
//   closed       ou              ee
//   mid          oh              ih
//   open             aa
//
// Input is an AnalyserNode's float spectrum (dB per bin); output is the five
// viseme weights, scaled by loudness. Pure, so it can be unit tested.

export const VISEMES = ["aa", "ih", "ou", "ee", "oh"];

// Typical formants of a female voice, Hz.
const VOWELS = { aa: [850, 1500], ih: [560, 2100], ee: [320, 2600], oh: [560, 950], ou: [340, 850] };
const CAPS = { aa: 1, ih: 0.7, ou: 0.75, ee: 0.65, oh: 0.85 };

function smoothedPower(db, binHz, maxHz) {
  const n = Math.min(db.length, Math.ceil(maxHz / binHz) + 3);
  const lin = new Float64Array(n);
  for (let i = 0; i < n; i++) lin[i] = Number.isFinite(db[i]) ? 10 ** (db[i] / 10) : 0;
  // ~230 Hz box blur merges the voice's harmonics into formant humps.
  const r = Math.max(1, Math.round(115 / binHz));
  const out = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    let s = 0, c = 0;
    for (let j = Math.max(0, i - r); j <= Math.min(n - 1, i + r); j++) { s += lin[j]; c++; }
    out[i] = s / c;
  }
  return out;
}

// Local maxima (humps) of the smoothed spectrum between lo and hi Hz.
function humps(power, binHz, lo, hi) {
  const out = [];
  const i0 = Math.max(1, Math.floor(lo / binHz));
  const i1 = Math.min(power.length - 2, Math.ceil(hi / binHz));
  for (let i = i0; i <= i1; i++) {
    if (power[i] >= power[i - 1] && power[i] > power[i + 1]) out.push({ hz: i * binHz, p: power[i] });
  }
  return out;
}

/**
 * How well the spectrum matches each vowel: the best pair of humps
 * (formant candidates) near that vowel's F1 and F2, weighted by how strong
 * both humps are. Matching pairs rather than picking "the" F1 copes with
 * the voice's own low harmonics, which also make humps near 200-450 Hz.
 */
export function vowelScores(db, binHz) {
  const power = smoothedPower(db, binHz, 3200);
  const all = humps(power, binHz, 250, 3000);
  const scores = Object.fromEntries(VISEMES.map((k) => [k, 0]));
  if (!all.length) return scores;
  const top = Math.max(...all.map((h) => h.p));
  const strong = all.filter((h) => h.p >= top * 0.01).map((h) => ({ hz: h.hz, w: Math.sqrt(h.p / top) }));
  for (const k of VISEMES) {
    const [v1, v2] = VOWELS[k];
    for (const a of strong) {
      for (const b of strong) {
        if (b.hz < a.hz + 200) continue;
        const d = ((a.hz - v1) / 170) ** 2 + ((b.hz - v2) / 450) ** 2;
        scores[k] = Math.max(scores[k], Math.exp(-d) * a.w * b.w);
      }
    }
  }
  return scores;
}

/**
 * @param {Float32Array|number[]} db  analyser.getFloatFrequencyData output
 * @param {number} binHz              sampleRate / fftSize
 * @param {number} volume             0..1 loudness (mouth opens with it)
 */
export function visemesFromSpectrum(db, binHz, volume) {
  const out = { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 };
  if (!(volume > 0.04)) return out;
  const raw = vowelScores(db, binHz);
  // Favour the clearest match so the mouth reads as one vowel, not a blur.
  let total = 0;
  for (const k of VISEMES) { raw[k] = raw[k] ** 2; total += raw[k]; }
  // Louder sound opens the mouth more; the jaw (aa) always carries some of it.
  const amount = Math.min(1, volume * 1.8);
  for (const k of VISEMES) out[k] = Math.min(CAPS[k], total > 1e-9 ? (raw[k] / total) * amount : 0);
  out.aa = Math.max(out.aa, amount * (total > 1e-9 ? 0.25 : 0.6));
  return out;
}

// ---------------------------------------------------------------------------
// Timelines from her voice engine (the X-Sarah-Visemes header on /api/tts):
// [[start_ms, code, length_ms], ...] with codes a e i o u (vowels), m (lips
// closed: m b p), f (f v), c (other consonants), _ (silence).
// ---------------------------------------------------------------------------

const CODE_SHAPES = {
  a: { aa: 1 },
  e: { ee: 0.85, aa: 0.15 },
  i: { ih: 1 },
  o: { oh: 1 },
  u: { ou: 1 },
  m: {},                       // lips together
  f: { ih: 0.3 },              // lower lip to teeth, mouth nearly shut
  c: { aa: 0.25, ih: 0.15 },   // most consonants: slightly open
  _: {},
};

/** The header value -> [{t, c, d}] in seconds (null if absent or malformed). */
export function parseTimeline(header) {
  if (!header) return null;
  try {
    const rows = JSON.parse(header);
    if (!Array.isArray(rows)) return null;
    const out = [];
    for (const r of rows) {
      if (!Array.isArray(r) || r.length < 3 || !Number.isFinite(r[0]) || !Number.isFinite(r[2])) continue;
      out.push({ t: r[0] / 1000, c: CODE_SHAPES[r[1]] ? r[1] : "c", d: Math.max(0.01, r[2] / 1000) });
    }
    return out.length ? out : null;
  } catch {
    return null;
  }
}

function findEntry(timeline, t) {
  let lo = 0, hi = timeline.length - 1;
  if (hi < 0 || t < timeline[0].t) return -1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (timeline[mid].t <= t) lo = mid; else hi = mid - 1;
  }
  return lo;
}

/**
 * Viseme weights at playback time `t` (seconds), scaled by `amount` (how
 * open: her current loudness). The mouth starts moving toward the next shape
 * in the last 35% of each sound, as real mouths do.
 */
export function timelineShape(timeline, t, amount) {
  const out = { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 };
  const k = findEntry(timeline, t);
  if (k < 0) return out;
  const cur = timeline[k];
  if (t > cur.t + cur.d + 0.06) return out; // after the last sound
  const p = (t - cur.t) / cur.d;
  const next = timeline[k + 1];
  const blend = next && p > 0.65 ? Math.min(1, (p - 0.65) / 0.35) * 0.5 : 0;
  for (const [shape, w] of Object.entries(CODE_SHAPES[cur.c] || {})) out[shape] += w * (1 - blend);
  if (blend) for (const [shape, w] of Object.entries(CODE_SHAPES[next.c] || {})) out[shape] += w * blend;
  for (const k2 of VISEMES) out[k2] = Math.min(CAPS[k2], out[k2] * amount);
  return out;
}
