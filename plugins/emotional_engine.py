from dataclasses import dataclass
import time
from typing import Literal


EmotionType = Literal["neutral", "happy", "affectionate", "concerned", "curious"]


@dataclass
class EmotionResult:
    emotion: EmotionType
    intensity: float


class EmotionalEngine:
    """Simple emotional engine with gentle decay."""

    def __init__(self, max_intensity: float = 1.0):
        self.emotion: EmotionType = "neutral"
        self.intensity: float = 0.2
        self.max_intensity = max_intensity
        self.last_update = time.time()

    def update_from_message(self, message: str, from_creator: bool = True):
        text = message.lower()

        if any(w in text for w in ["thank", "love", "appreciate", "good job", "proud"]):
            self._shift("happy", 0.2)
        elif any(w in text for w in ["error", "broken", "crash", "doesn't work", "failed"]):
            self._shift("concerned", 0.15)
        elif "?" in text:
            self._shift("curious", 0.1)
        else:
            self._soft_decay()

        if from_creator:
            self._shift("affectionate", 0.05)

    def blend_current(self) -> EmotionResult:
        intensity = max(0.0, min(self.intensity, self.max_intensity))
        return EmotionResult(emotion=self.emotion, intensity=intensity)

    def _shift(self, new_emotion: EmotionType, delta: float):
        self.emotion = new_emotion
        self.intensity = min(self.max_intensity, self.intensity + delta)
        self.last_update = time.time()

    def _soft_decay(self):
        if self.intensity > 0.2:
            self.intensity -= 0.02
        else:
            self.intensity = 0.2
        self.last_update = time.time()
