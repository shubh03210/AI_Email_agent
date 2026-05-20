"""
Tone Service
─────────────
Lightweight module for tone preset definitions and lookups.

Deliberately has NO langchain / LLM imports so it can be safely imported
in tests, task workers, and any module without pulling in the full LLM stack.
"""

from __future__ import annotations

# Maps tone preset name → style guidance injected into LLM system prompts.
# Add new presets here; the Config UI and schema validators reference the same keys.
TONE_PRESETS: dict[str, str] = {
    "formal": (
        "formal and authoritative — use complete sentences, precise vocabulary, "
        "avoid contractions, maintain appropriate professional distance"
    ),
    "friendly": (
        "warm and approachable — conversational register, use first names, "
        "light enthusiasm, contractions welcomed"
    ),
    "startup": (
        "modern startup culture — energetic and direct, use growth/impact language, "
        "avoid corporate jargon, feel like a human not an institution"
    ),
    "executive": (
        "concise and high-impact — lead with value, bullet-point friendly, "
        "respect the reader's time, results-oriented language"
    ),
    # backward compatibility
    "professional": (
        "professional and polished — standard business email, respectful and clear"
    ),
    "casual": (
        "casual and relaxed — friendly informal tone, keep it natural"
    ),
}


def get_tone_description(tone: str) -> str:
    """Return the style guidance string for a tone preset name (case-insensitive)."""
    return TONE_PRESETS.get(tone.lower(), TONE_PRESETS["formal"])
