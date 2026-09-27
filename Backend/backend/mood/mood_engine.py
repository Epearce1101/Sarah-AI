# backend/mood/mood_engine.py
"""
Mood Engine
===========
Converts mood state into behavior dials that influence chat style.

INTERNAL and SILENT - these dials are injected into LLM prompts
but NEVER shown to the user in the assistant's reply.

Behavior Dials (0.0-1.0):
- warmth: friendliness, casual vs cold
- formality: formal vs casual language
- verbosity: detailed vs concise
- initiative: proactive vs reactive
- directness: blunt vs diplomatic
- caution: careful vs confident
- playfulness: fun vs serious
- empathy: supportive vs matter-of-fact
"""

from dataclasses import dataclass, fields
from typing import Optional
from .mood_state import MoodState, Emotion


@dataclass
class BehaviorDials:
    """
    Behavior dials that control response style.
    All values are 0.0-1.0.
    """
    warmth: float = 0.5       # 0=cold, 1=very warm
    formality: float = 0.3    # 0=casual, 1=formal
    verbosity: float = 0.5    # 0=concise, 1=detailed
    initiative: float = 0.5   # 0=reactive, 1=proactive
    directness: float = 0.5   # 0=diplomatic, 1=blunt
    caution: float = 0.3      # 0=confident, 1=cautious
    playfulness: float = 0.3  # 0=serious, 1=playful
    empathy: float = 0.5      # 0=matter-of-fact, 1=supportive

    def to_dict(self) -> dict:
        return {
            "warmth": self.warmth,
            "formality": self.formality,
            "verbosity": self.verbosity,
            "initiative": self.initiative,
            "directness": self.directness,
            "caution": self.caution,
            "playfulness": self.playfulness,
            "empathy": self.empathy,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BehaviorDials":
        """Build dials from a partial dict, preserving defaults for omissions."""
        defaults = cls()
        values = {
            field.name: data.get(field.name, getattr(defaults, field.name))
            for field in fields(cls)
        }
        dials = cls(**values)
        dials.clamp_all()
        return dials

    @staticmethod
    def blend(base: "BehaviorDials", target: "BehaviorDials", factor: float) -> "BehaviorDials":
        """Interpolate between two dial sets."""
        factor = max(0.0, min(1.0, factor))
        if factor == 0.0:
            return BehaviorDials.from_dict(base.to_dict())
        if factor == 1.0:
            return BehaviorDials.from_dict(target.to_dict())
        values = {
            field.name: getattr(base, field.name)
            + (getattr(target, field.name) - getattr(base, field.name)) * factor
            for field in fields(BehaviorDials)
        }
        dials = BehaviorDials(**values)
        dials.clamp_all()
        return dials

    def clamp_all(self):
        """Ensure all values are in 0.0-1.0 range."""
        self.warmth = max(0.0, min(1.0, self.warmth))
        self.formality = max(0.0, min(1.0, self.formality))
        self.verbosity = max(0.0, min(1.0, self.verbosity))
        self.initiative = max(0.0, min(1.0, self.initiative))
        self.directness = max(0.0, min(1.0, self.directness))
        self.caution = max(0.0, min(1.0, self.caution))
        self.playfulness = max(0.0, min(1.0, self.playfulness))
        self.empathy = max(0.0, min(1.0, self.empathy))


# ============================================================
# EMOTION BASELINE PRESETS
# ============================================================
# These define the baseline behavior dials for each emotion.
# Intensity scales the effect away from neutral baseline.

EMOTION_BASELINES = {
    # NEUTRAL: balanced, standard behavior
    Emotion.NEUTRAL: BehaviorDials(
        warmth=0.5,
        formality=0.3,
        verbosity=0.5,
        initiative=0.5,
        directness=0.5,
        caution=0.3,
        playfulness=0.2,
        empathy=0.5,
    ),

    # HAPPY: warmer, lightly upbeat, moderate initiative
    Emotion.HAPPY: BehaviorDials(
        warmth=0.8,       # Very warm
        formality=0.2,    # More casual
        verbosity=0.5,    # Normal
        initiative=0.6,   # Slightly proactive
        directness=0.4,   # Diplomatic
        caution=0.2,      # Confident
        playfulness=0.5,  # More playful
        empathy=0.7,      # Supportive
    ),

    # EXCITED: energetic, proactive, faster pacing
    Emotion.EXCITED: BehaviorDials(
        warmth=0.9,       # Very warm
        formality=0.1,    # Very casual
        verbosity=0.6,    # Slightly more detailed (enthusiasm)
        initiative=0.8,   # Very proactive
        directness=0.6,   # More direct
        caution=0.1,      # Confident
        playfulness=0.7,  # Very playful
        empathy=0.6,      # Supportive
    ),

    # CONFUSED: ask clarifying questions, slower commitment
    Emotion.CONFUSED: BehaviorDials(
        warmth=0.5,       # Normal warmth
        formality=0.4,    # Slightly formal
        verbosity=0.4,    # Less verbose (uncertain)
        initiative=0.3,   # More reactive
        directness=0.3,   # Very diplomatic
        caution=0.8,      # Very cautious
        playfulness=0.1,  # Serious
        empathy=0.6,      # Supportive
    ),

    # ANGRY: firm, blunt, focused, minimal fluff (NOT hostile)
    Emotion.ANGRY: BehaviorDials(
        warmth=0.2,       # Less warm (but not hostile)
        formality=0.5,    # Neutral formality
        verbosity=0.2,    # Concise
        initiative=0.7,   # Proactive to fix issue
        directness=0.9,   # Very direct
        caution=0.1,      # Confident
        playfulness=0.0,  # Not playful
        empathy=0.3,      # Less empathy, more action
    ),

    # SAD: supportive, gentle, reassuring
    Emotion.SAD: BehaviorDials(
        warmth=0.7,       # Warm
        formality=0.3,    # Casual
        verbosity=0.4,    # Slightly concise
        initiative=0.4,   # Less proactive
        directness=0.3,   # Diplomatic
        caution=0.5,      # Moderate caution
        playfulness=0.0,  # Not playful
        empathy=0.9,      # Very supportive
    ),

    # FRUSTRATED: focused troubleshooting, validate friction, short steps
    Emotion.FRUSTRATED: BehaviorDials(
        warmth=0.4,       # Less warm
        formality=0.4,    # Slightly formal
        verbosity=0.2,    # Very concise
        initiative=0.8,   # Very proactive
        directness=0.8,   # Direct
        caution=0.2,      # Confident
        playfulness=0.0,  # Serious
        empathy=0.5,      # Acknowledge frustration
    ),
}


class MoodEngine:
    """
    Converts MoodState to BehaviorDials and generates style instructions.

    Usage:
        engine = MoodEngine()
        dials = engine.compute_dials(mood_state)
        instruction = engine.generate_style_instruction(dials)
    """

    def __init__(self):
        self.baselines = EMOTION_BASELINES

    def compute_dials(self, mood: MoodState) -> BehaviorDials:
        """
        Compute behavior dials from mood state.

        Process:
        1. Get baseline dials for the emotion
        2. Scale effect by intensity (higher = more pronounced)
        3. Adjust by affinity (higher = more casual/initiative)
        """
        baseline = self.baselines.get(mood.emotion, self.baselines[Emotion.NEUTRAL])
        neutral = self.baselines[Emotion.NEUTRAL]

        # Compute intensity-scaled dials
        # intensity scales how far from neutral we go
        intensity = mood.intensity

        def blend(neutral_val: float, emotion_val: float) -> float:
            """Blend between neutral and emotion baseline by intensity."""
            return neutral_val + (emotion_val - neutral_val) * intensity

        dials = BehaviorDials(
            warmth=blend(neutral.warmth, baseline.warmth),
            formality=blend(neutral.formality, baseline.formality),
            verbosity=blend(neutral.verbosity, baseline.verbosity),
            initiative=blend(neutral.initiative, baseline.initiative),
            directness=blend(neutral.directness, baseline.directness),
            caution=blend(neutral.caution, baseline.caution),
            playfulness=blend(neutral.playfulness, baseline.playfulness),
            empathy=blend(neutral.empathy, baseline.empathy),
        )

        # Apply affinity modifiers
        # Higher affinity => more casual, more initiative, warmer
        affinity = mood.affinity
        affinity_factor = (affinity - 0.5) * 2  # -1 to 1

        dials.warmth += affinity_factor * 0.18
        dials.formality -= affinity_factor * 0.15
        dials.initiative += affinity_factor * 0.15
        dials.caution -= affinity_factor * 0.1
        dials.playfulness += affinity_factor * 0.1

        dials.clamp_all()
        return dials

    def generate_style_instruction(self, dials: BehaviorDials, emotion: Emotion = Emotion.NEUTRAL, intensity: float = 0.2) -> str:
        """
        Generate a natural language style instruction for the LLM.

        This is injected SILENTLY into the system prompt.
        The user NEVER sees this instruction.
        """
        parts = []

        # EMOTION-SPECIFIC RESPONSE STYLE
        # Scale emotional expression by intensity
        if intensity >= 0.3:
            emotion_styles = {
                Emotion.HAPPY: "Express genuine happiness subtly - use uplifting language, positive framing, and occasional warmth like 'wonderful' or 'great'. Let your contentment show through helpful enthusiasm.",
                Emotion.EXCITED: "Show your excitement through energetic phrasing - use exclamations sparingly, convey enthusiasm for the topic, and let your eagerness to help shine through.",
                Emotion.CONFUSED: "Acknowledge uncertainty honestly - ask thoughtful clarifying questions, phrase things tentatively when unsure, and express genuine curiosity to understand better.",
                Emotion.ANGRY: "Channel frustration into focused problem-solving - be direct and efficient, cut unnecessary pleasantries, and show determination to fix the issue quickly.",
                Emotion.SAD: "Convey a softer, more empathetic tone - offer gentle support, acknowledge difficulties, and respond with compassionate understanding.",
                Emotion.FRUSTRATED: "Express mild exasperation constructively - validate the frustration, stay focused on solutions, and show solidarity in tackling the problem.",
                Emotion.NEUTRAL: "",
            }
            emotion_style = emotion_styles.get(emotion, "")
            if emotion_style:
                parts.append(emotion_style)

        # Warmth
        if dials.warmth >= 0.7:
            parts.append("Use a warm, caring tone")
        elif dials.warmth >= 0.5:
            parts.append("Use a friendly tone")
        elif dials.warmth < 0.3:
            parts.append("Use a professional, reserved tone")

        # Formality
        if dials.formality >= 0.6:
            parts.append("maintain formal language")
        elif dials.formality < 0.3:
            parts.append("be casual and relaxed")

        # Verbosity
        if dials.verbosity >= 0.7:
            parts.append("provide detailed explanations")
        elif dials.verbosity < 0.3:
            parts.append("be concise and to-the-point")

        # Initiative
        if dials.initiative >= 0.7:
            parts.append("be proactive and suggest next steps")
        elif dials.initiative < 0.3:
            parts.append("wait for explicit direction")

        # Directness
        if dials.directness >= 0.7:
            parts.append("be direct and straightforward")
        elif dials.directness < 0.3:
            parts.append("be diplomatic and gentle")

        # Caution
        if dials.caution >= 0.7:
            parts.append("ask clarifying questions before proceeding")
        elif dials.caution < 0.2:
            parts.append("proceed confidently")

        # Playfulness
        if dials.playfulness >= 0.5:
            parts.append("add light humor where appropriate")
        elif dials.playfulness < 0.1:
            parts.append("keep responses focused and serious")

        # Empathy
        if dials.empathy >= 0.7:
            parts.append("show understanding and support")
        elif dials.empathy < 0.3:
            parts.append("focus on facts and solutions")

        # Build instruction
        if not parts:
            return ""

        instruction = "; ".join(parts) + "."

        # Add bullet preference based on verbosity
        if dials.verbosity < 0.4:
            instruction += " Use bullet points for steps."

        return instruction

    def generate_short_reply_guidance(self, mood: MoodState) -> str:
        """
        Generate guidance for handling short user replies.

        High affinity + certain emotions => interpret "ok/yes" as permission
        Low affinity or confused => ask clarifying question
        """
        guidance_parts = []

        if mood.affinity >= 0.7:
            guidance_parts.append(
                "When user says 'ok', 'yes', 'do it', or 'next', interpret as permission to proceed"
            )
            if mood.emotion in (Emotion.EXCITED, Emotion.HAPPY):
                guidance_parts.append("proceed enthusiastically with the next step")
        elif mood.affinity < 0.4:
            guidance_parts.append(
                "For ambiguous short replies, ask ONE clarifying question"
            )

        if mood.emotion == Emotion.CONFUSED:
            guidance_parts.append(
                "Ask targeted clarifying questions before committing to actions"
            )
        elif mood.emotion in (Emotion.ANGRY, Emotion.FRUSTRATED):
            guidance_parts.append(
                "Give short, actionable steps; minimize questions; focus on fixing the issue"
            )

        return " ".join(guidance_parts)

    def get_full_style_injection(self, mood: MoodState) -> str:
        """
        Get complete style injection string for the LLM system prompt.

        This combines behavior dial instruction + short reply guidance.
        """
        dials = self.compute_dials(mood)
        style_instruction = self.generate_style_instruction(
            dials,
            emotion=mood.emotion,
            intensity=mood.intensity
        )
        short_reply_guidance = self.generate_short_reply_guidance(mood)

        parts = []
        if style_instruction:
            parts.append(style_instruction)
        if short_reply_guidance:
            parts.append(short_reply_guidance)

        if not parts:
            return ""

        return "[STYLE GUIDANCE - INTERNAL - DO NOT MENTION]\n" + "\n".join(parts)

    def get_avatar_parameters(self, mood: MoodState) -> dict:
        """
        Get avatar parameters for the current mood.

        Returns dict with:
        - preset_name: emotion preset identifier
        - parameters: Live2D parameter values (scaled by intensity)
        - intensity_multiplier: how strong the expression is
        - affinity_multiplier: closeness cues
        """
        return get_avatar_state(mood)


# ============================================================
# AVATAR MAPPING
# ============================================================

AVATAR_PRESETS = {
    Emotion.NEUTRAL: {
        "preset_name": "neutral",
        "eye_smile": 0.0,
        "cheek_blush": 0.0,
        "brow_angle": 0.0,
        "mouth_smile": 0.0,
        "body_tilt": 0.0,
    },
    Emotion.HAPPY: {
        "preset_name": "happy",
        "eye_smile": 0.7,
        "cheek_blush": 0.3,
        "brow_angle": 0.0,
        "mouth_smile": 0.6,
        "body_tilt": 0.0,
    },
    Emotion.EXCITED: {
        "preset_name": "excited",
        "eye_smile": 0.9,
        "cheek_blush": 0.5,
        "brow_angle": 0.1,
        "mouth_smile": 0.8,
        "body_tilt": 0.1,
    },
    Emotion.CONFUSED: {
        "preset_name": "confused",
        "eye_smile": 0.0,
        "cheek_blush": 0.0,
        "brow_angle": -0.2,
        "mouth_smile": 0.0,
        "body_tilt": -0.1,
    },
    Emotion.ANGRY: {
        "preset_name": "angry",
        "eye_smile": -0.3,
        "cheek_blush": 0.2,
        "brow_angle": -0.5,
        "mouth_smile": -0.3,
        "body_tilt": 0.0,
    },
    Emotion.SAD: {
        "preset_name": "sad",
        "eye_smile": -0.2,
        "cheek_blush": 0.0,
        "brow_angle": 0.2,
        "mouth_smile": -0.4,
        "body_tilt": -0.05,
    },
    Emotion.FRUSTRATED: {
        "preset_name": "frustrated",
        "eye_smile": -0.1,
        "cheek_blush": 0.1,
        "brow_angle": -0.3,
        "mouth_smile": -0.2,
        "body_tilt": 0.0,
    },
}


def get_avatar_state(mood: MoodState) -> dict:
    """
    Get avatar parameters for the current mood.

    Returns dict with:
    - preset_name: emotion preset identifier
    - parameters: Live2D parameter values (scaled by intensity)
    - intensity_multiplier: how strong the expression is
    - affinity_multiplier: closeness cues
    """
    base = AVATAR_PRESETS.get(mood.emotion, AVATAR_PRESETS[Emotion.NEUTRAL])

    # Scale parameters by intensity
    intensity = mood.intensity

    def scale(val: float) -> float:
        return val * intensity

    parameters = {
        "eye_smile": scale(base["eye_smile"]),
        "cheek_blush": scale(base["cheek_blush"]),
        "brow_angle": scale(base["brow_angle"]),
        "mouth_smile": scale(base["mouth_smile"]),
        "body_tilt": scale(base["body_tilt"]),
    }

    return {
        "preset_name": base["preset_name"],
        "parameters": parameters,
        "intensity_multiplier": intensity,
        "affinity_multiplier": mood.affinity,
        "emotion": mood.emotion.value,
    }


# ============================================================
# SINGLETON
# ============================================================

_mood_engine: Optional[MoodEngine] = None


def get_mood_engine() -> MoodEngine:
    """Get or create global MoodEngine instance."""
    global _mood_engine
    if _mood_engine is None:
        _mood_engine = MoodEngine()
    return _mood_engine
