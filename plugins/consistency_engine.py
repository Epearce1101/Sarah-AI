class ConsistencyEngine:
    """Ensures Sarah's responses stay in-character and affectionate."""

    def __init__(self, base_persona: str = "affectionate"):
        self.base_persona = base_persona

    def adjust(self, text: str, creator_title: str = "Creator") -> str:
        # Ensure she addresses the Creator at least once
        if creator_title not in text:
            text += f"\n\nI'm here with you, {creator_title} 💜"
        return text
