// The emotional tone of a sentence, so Sarah's face follows what she is
// saying the way anyone's does, whether or not she wrote a <face> tag.
// Ordered: the first matching tone wins. Deliberately small and
// conservative; her <feel> and <face> tags always take precedence.

const TONES = [
  ["laugh", 0.8, /\b(haha+|hehe+|ahaha|lol|lmao)\b|😂|🤣/i],
  ["cry", 0.7, /\b(i'm (so )?heartbroken|breaks my heart)\b|😭/i],
  ["excited", 0.8, /\b(yay+|woo+h?o*|omg|amazing|awesome|incredible|fantastic|congrat\w*|so excited|can't wait)\b|🎉|!!/i],
  ["shocked", 0.8, /\b(no way|what\?!|oh my god|holy)\b|😱/i],
  ["surprised", 0.7, /\b(wow|whoa|really\?|oh!|seriously\?)\b|😮/i],
  ["shy", 0.65, /\b(blush\w*|embarrass\w*|flatter\w*|stop it|you're making me)\b|☺️|😳/i],
  ["playful", 0.65, /\b(just kidding|jk|tease|teasing|gotcha|hmph)\b|😏|😜|😉/i],
  ["sad", 0.6, /\b(sorry to hear|so sorry|unfortunately|sad|miss(ed)? you|lonely|lost|passed away|sick)\b|😢|😞/i],
  ["worried", 0.55, /\b(worr(y|ied|ying)|be careful|hope (you're|you are|it's) ok|concern\w*|scary|afraid)\b/i],
  ["annoyed", 0.5, /\b(ugh|annoying|come on|seriously,)\b|😤|🙄/i],
  ["thinking", 0.55, /\b(hmm+|let me think|i wonder|not sure|perhaps|thinking about)\b|🤔/i],
  ["smile", 0.5, /\b(glad|happy|great|nice|good|thanks?|thank you|welcome|love|sweet|cute|aww+)\b|😊|🙂|❤|💕|♡/i],
];

/** { face, amount } for a sentence, or null when it's emotionally flat. */
export function affectOf(text) {
  const t = String(text || "");
  if (!t.trim()) return null;
  for (const [face, amount, re] of TONES) {
    if (re.test(t)) return { face, amount };
  }
  return null;
}

/** Split text into sentences with their start offsets. */
export function sentences(text) {
  const out = [];
  const re = /[^.!?…\n]+[.!?…]*["')\]]*\s*/g;
  let m;
  while ((m = re.exec(String(text || "")))) {
    if (m[0].trim()) out.push({ text: m[0], at: m.index });
    if (m[0].length === 0) re.lastIndex++;
  }
  return out;
}

/**
 * Face cues for each sentence whose tone is clear and that has no face/feel
 * cue of its own. `cues` are the explicit cues (offsets in `text`).
 */
export function affectCues(text, cues = []) {
  const explicit = cues.filter((c) => c.type === "face" || c.type === "feel");
  const result = [];
  for (const s of sentences(text)) {
    const end = s.at + s.text.length;
    if (explicit.some((c) => c.at >= s.at && c.at < end)) continue;
    const tone = affectOf(s.text);
    if (tone) result.push({ type: "face", value: tone.face, amount: tone.amount, at: s.at, auto: true });
  }
  return result;
}
