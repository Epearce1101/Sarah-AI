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
