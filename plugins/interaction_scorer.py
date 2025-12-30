class InteractionScorer:
    """Scores how meaningful an interaction felt."""

    def score(self, message: str, emotion: str, intensity: float) -> float:
        base = 0.5
        text = message.lower()
        if any(w in text for w in ["thank", "love", "appreciate", "proud"]):
            base += 0.3
        if emotion in ["affectionate", "happy"]:
            base += 0.1
        base += min(max(intensity, 0.0), 1.0) * 0.1
        return min(1.0, base)
