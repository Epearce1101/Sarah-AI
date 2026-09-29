// Reading a face (pure functions, no camera): MediaPipe's blendshape scores
// -> an expression, and head angles over time -> nod / shake / tilt.

// blendshapes: [{categoryName, score}] -> "smiling" | "surprised" | "frowning" | "neutral"
export function expressionOf(blendshapes) {
  const s = {};
  for (const b of blendshapes || []) s[b.categoryName] = b.score;
  const avg = (a, b) => ((s[a] || 0) + (s[b] || 0)) / 2;
  const smile = avg("mouthSmileLeft", "mouthSmileRight");
  const frown = avg("mouthFrownLeft", "mouthFrownRight");
  const browDown = avg("browDownLeft", "browDownRight");
  const wide = avg("eyeWideLeft", "eyeWideRight");
  if ((s.jawOpen || 0) > 0.35 && ((s.browInnerUp || 0) > 0.4 || wide > 0.3) && smile < 0.4) return "surprised";
  if (smile > 0.5) return "smiling";
  if (frown > 0.35 || (browDown > 0.55 && smile < 0.2)) return "frowning";
  return "neutral";
}

// A 4x4 column-major transform (MediaPipe facialTransformationMatrixes) -> degrees.
export function headAngles(m) {
  const d = m?.data || m;
  if (!d || d.length < 16) return null;
  const deg = 180 / Math.PI;
  return {
    pitch: Math.atan2(d[6], d[10]) * deg,   // nodding
    yaw: Math.asin(Math.max(-1, Math.min(1, -d[2]))) * deg,  // shaking
    roll: Math.atan2(d[1], d[0]) * deg,     // tilting
  };
}

// Back-and-forth movements in a series of values within the last `windowMs`:
// how many direction changes bigger than `min` degrees.
export function swings(samples, key, min, now, windowMs = 1400) {
  const recent = samples.filter((p) => now - p.t <= windowMs);
  let changes = 0;
  let dir = 0;
  let anchor = recent[0]?.[key];
  for (const p of recent) {
    const delta = p[key] - anchor;
    if (Math.abs(delta) < min) continue;
    const now2 = Math.sign(delta);
    if (dir && now2 !== dir) changes += 1;
    dir = now2;
    anchor = p[key];
  }
  return changes;
}
