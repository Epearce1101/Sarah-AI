// Maps Sarah's emotion words (from /api/chat) onto the 5 standard VRM
// expressions every VRM model has: happy, angry, sad, relaxed, surprised.

export const VRM_EMOTIONS = ['happy', 'angry', 'sad', 'relaxed', 'surprised'];

// emotion word -> [VRM expression, how strongly (0..1) relative to intensity]
const EMOTION_MAP = {
  neutral: [null, 0],
  happy: ['happy', 1], joy: ['happy', 1], joyful: ['happy', 1], excited: ['happy', 1],
  playful: ['happy', 0.8], proud: ['happy', 0.8], amused: ['happy', 0.7],
  affectionate: ['relaxed', 1], loving: ['relaxed', 1], calm: ['relaxed', 0.8],
  relaxed: ['relaxed', 1], content: ['relaxed', 0.8], shy: ['relaxed', 0.6],
  sad: ['sad', 1], concerned: ['sad', 0.6], worried: ['sad', 0.7], sorry: ['sad', 0.7],
  lonely: ['sad', 0.8], hurt: ['sad', 1],
  angry: ['angry', 1], annoyed: ['angry', 0.6], frustrated: ['angry', 0.8], jealous: ['angry', 0.5],
  surprised: ['surprised', 1], shocked: ['surprised', 1], curious: ['surprised', 0.4],
};

/**
 * @param {string} emotion e.g. "happy", "concerned"
 * @param {number} intensity 0..1 (Sarah's emotion_intensity)
 * @returns {{expression: string|null, weight: number}}
 */
export function emotionToExpression(emotion, intensity = 0.8) {
  const key = String(emotion || 'neutral').trim().toLowerCase();
  const [expression, strength] = EMOTION_MAP[key] || [null, 0];
  const level = Math.min(1, Math.max(0, Number(intensity) || 0));
  // Even a low-intensity emotion should be visible, so map 0..1 to 0.35..1
  const weight = expression ? Math.min(1, (0.35 + level * 0.65) * strength) : 0;
  return { expression, weight };
}
