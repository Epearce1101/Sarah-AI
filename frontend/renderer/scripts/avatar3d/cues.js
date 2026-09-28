// Body language Sarah writes inline in her replies. They are her own
// feelings and movements (the body is her, not a puppet), stripped from the
// visible text and acted at the moment their words are spoken (or shown):
//
//   <feel>happy:0.7 | why</feel>                      how she feels (opens a reply)
//   <face>happy</face>  <face>surprised:0.6</face>   facial expression (+ intensity)
//   <look>chat</look>                                 where she looks
//   <point>chat</point>                               pointing at something
//   <gesture>wave</gesture>                           body gesture / animation
//   <motion>wave</motion>                             legacy alias of <gesture>
//   <silent/>                                         chose to say nothing
//
// Attribute forms (<face name="happy"/>) and bare legacy gesture tags
// (<wave/>, <nod>) are accepted too.

export const CUE_KINDS = ["feel", "face", "look", "point", "gesture"];

const PAIRED = /<(feel|face|look|point|gesture|motion)\b[^>]*>([^<]*)<\/\1\s*>/gi;
const SELF_CLOSING = /<(face|look|point|gesture|motion)\s+(?:name|value|to|at)\s*=\s*["']?([\w:.\- ]+?)["']?\s*\/?>/gi;
const SILENT = /<silent\s*\/?>/gi;
// A cue still being streamed in: "<fa", "<face>hap", "<face>happy</fa",
// "<gesture name=\"wa".
const PARTIAL_TAIL = /<(?:(?:feel|face|look|point|gesture|motion)\b[^>]*>[^<]*(?:<\/?[a-z]*)?|\/?[a-z]*(?:\s[^>]*)?)$/i;

function splitValue(raw, kind) {
  let text = String(raw).trim();
  let reason;
  if (kind === "feel") {
    const bar = text.indexOf("|");
    if (bar >= 0) { reason = text.slice(bar + 1).trim() || undefined; text = text.slice(0, bar); }
  }
  // "happy:0.7"; feelings may also be written "happy 0.7".
  const [value, amount] = text.trim().toLowerCase().split(kind === "feel" ? /\s*[:\s]\s*/ : /\s*:\s*/);
  const n = Number(amount);
  const out = { value: (value || "").trim().replace(/\s+/g, "_"), amount: Number.isFinite(n) && amount !== "" && amount !== undefined ? Math.max(0, Math.min(1, n)) : undefined };
  if (reason) out.reason = reason;
  return out;
}

/**
 * Split raw reply text into what is shown/spoken and the cues inside it.
 * `at` is the character offset in the returned `text` where the cue sits.
 * `bareGestures` (Set of names) turns legacy tags like <wave/> into cues.
 */
export function parseCues(raw, { bareGestures = null, streaming = false } = {}) {
  let source = String(raw || "");
  if (streaming) source = source.replace(PARTIAL_TAIL, "");

  const matches = [];
  for (const re of [PAIRED, SELF_CLOSING]) {
    re.lastIndex = 0;
    for (const m of source.matchAll(re)) {
      matches.push({ index: m.index, length: m[0].length, kind: m[1].toLowerCase(), value: m[2] });
    }
  }
  for (const m of source.matchAll(SILENT)) {
    matches.push({ index: m.index, length: m[0].length, kind: null, value: "" });
  }
  if (bareGestures && bareGestures.size) {
    const bare = /<\/?([a-z_][\w-]*)\s*\/?>/gi;
    for (const m of source.matchAll(bare)) {
      const name = m[1].toLowerCase();
      if (!bareGestures.has(name)) continue;
      if (matches.some((x) => m.index >= x.index && m.index < x.index + x.length)) continue;
      // Only opening/self-closing tags carry a cue; closing tags just vanish.
      matches.push({ index: m.index, length: m[0].length, kind: m[0][1] === "/" ? null : "gesture", value: name });
    }
  }
  matches.sort((a, b) => a.index - b.index);

  let text = "";
  const cues = [];
  let last = 0;
  // Removing a tag between two words would leave a double space; drop the
  // duplicate while joining so cue offsets stay exact.
  const append = (chunk) => {
    if (text.endsWith(" ") && chunk.startsWith(" ")) chunk = chunk.slice(1);
    text += chunk;
  };
  for (const m of matches) {
    if (m.index < last) continue; // overlapping forms of the same tag
    append(source.slice(last, m.index));
    last = m.index + m.length;
    if (!m.kind) continue;
    const { value, amount, reason } = splitValue(m.value, m.kind);
    if (!value) continue;
    const cue = { type: m.kind === "motion" ? "gesture" : m.kind, value, amount, at: text.length };
    if (reason) cue.reason = reason;
    cues.push(cue);
  }
  append(source.slice(last));
  return { text, cues };
}

export function stripCues(raw, options) {
  return parseCues(raw, options).text;
}
