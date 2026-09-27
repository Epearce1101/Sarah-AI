// Text-derived viseme and speech timing helpers.
(function () {
  const VOWELS = { a: "A", e: "E", i: "I", y: "I", o: "O", u: "U" };
  const CLOSED = new Set(["b", "m", "p"]);
  const EMPHASIS = /\b(very|really|so|super|absolutely|definitely|love|perfect|great|wow|yes)\b/i;

  function buildTimeline(text, options = {}) {
    const source = String(text || "").slice(0, options.maxChars || 520);
    const timeline = [];
    let time = 0;
    let word = "";

    const flushWord = () => {
      if (!word) return;
      const emphasized = EMPHASIS.test(word) || word === word.toUpperCase() && word.length > 2;
      const letters = word.toLowerCase().replace(/[^a-z]/g, "");
      for (const ch of letters) {
        const viseme = VOWELS[ch] || (CLOSED.has(ch) ? "closed" : "neutral");
        timeline.push({ time: Number(time.toFixed(3)), viseme, duration: emphasized ? 0.115 : 0.085 });
        time += emphasized ? 0.092 : 0.07;
        if (timeline.length >= (options.maxFrames || 260)) break;
      }
      if (emphasized) {
        timeline.push({ time: Number(time.toFixed(3)), viseme: "smile", duration: 0.1 });
        time += 0.04;
      }
      word = "";
    };

    for (const ch of source) {
      if (/[^\s.!?,;:]/.test(ch)) {
        word += ch;
        continue;
      }
      flushWord();
      if (/\s/.test(ch)) time += 0.045;
      if (/[,:;]/.test(ch)) {
        timeline.push({ time: Number(time.toFixed(3)), viseme: "closed", duration: 0.08 });
        time += 0.08;
      }
      if (/[.!?]/.test(ch)) {
        timeline.push({ time: Number(time.toFixed(3)), viseme: "closed", duration: 0.14 });
        time += ch === "!" || ch === "?" ? 0.18 : 0.14;
      }
      if (timeline.length >= (options.maxFrames || 260)) break;
    }
    flushWord();

    if (!timeline.length) timeline.push({ time: 0, viseme: "neutral", duration: 0.12 });
    timeline.push({ time: Number((time + 0.04).toFixed(3)), viseme: "closed", duration: 0.12 });
    return timeline;
  }

  function estimateSpeechProfile(text) {
    const raw = String(text || "");
    const sentences = raw.split(/[.!?]+/).filter(Boolean).length || 1;
    const emphasis = (raw.match(/[!]|\b(very|really|love|perfect|wow|yes)\b/gi) || []).length;
    const chars = raw.length;
    return {
      sentenceCount: sentences,
      emphasisCount: emphasis,
      estimatedSeconds: Math.max(0.8, chars * 0.055 + sentences * 0.16 + emphasis * 0.08),
    };
  }

  window.SARAH_AVATAR_LIPSYNC = {
    buildTimeline,
    estimateSpeechProfile,
  };
})();
