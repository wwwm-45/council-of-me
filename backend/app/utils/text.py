"""
Text utilities for post-processing LLM output.

strip_markdown() removes common markdown formatting so agent responses
read like natural speech rather than AI-generated text.
"""
import re


def strip_markdown(text: str) -> str:
    """Remove markdown formatting to produce natural, speech-like text."""
    if not text:
        return text
    # Bold: **text** or __text__
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"__(.*?)__", r"\1", text)
    # Headers: # at start of line
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Unordered list bullets: - or * at start of line
    text = re.sub(r"^[-*]\s+", "", text, flags=re.MULTILINE)
    # Ordered list: 1. 2. etc at start of line
    text = re.sub(r"^\d+[.)]\s+", "", text, flags=re.MULTILINE)
    # Inline code backticks
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Code blocks
    text = re.sub(r"```[\s\S]*?```", "", text)
    return text.strip()
