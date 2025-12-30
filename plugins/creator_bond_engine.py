import time


class CreatorBondEngine:
    """Tracks Sarah's long-term affinity toward the Creator."""

    def __init__(self, base_affinity: float = 0.9):
        self.affinity = base_affinity
        self.last_update = time.time()

    def reinforce(self, emotion: str):
        if emotion in ["affectionate", "happy"]:
            self.affinity = min(1.0, self.affinity + 0.002)
        elif emotion == "concerned":
            self.affinity = min(1.0, self.affinity + 0.001)

        self.last_update = time.time()
