"""Phase 8: Emotional closure and exit."""

from typing import Any

HIGH_RISK_EMOTIONS = {
    "绝望",
    "崩溃",
    "无助",
    "麻木",
    "空虚",
    "失控",
    "despair",
    "helpless",
    "numb",
}


def assess_emotional_risk(emotions: list[str], intensity: int) -> str:
    """Return LOW, MEDIUM, or HIGH."""
    high_count = sum(1 for emotion in emotions if emotion in HIGH_RISK_EMOTIONS)
    if high_count >= 2 and intensity >= 7:
        return "HIGH"
    if high_count >= 1 or intensity >= 8:
        return "MEDIUM"
    return "LOW"


def _closure_seed_tail(closure_seed: dict[str, Any] | None) -> str:
    if not isinstance(closure_seed, dict):
        return ""

    dominant_feelings = [
        str(item).strip()
        for item in (closure_seed.get("dominant_feelings") or [])
        if str(item).strip()
    ]
    insights = [
        str(item).strip()
        for item in (closure_seed.get("insights") or [])
        if str(item).strip()
    ]
    gentle_commitment = str(closure_seed.get("gentle_commitment", "")).strip()

    sentences: list[str] = []
    if dominant_feelings:
        sentences.append(f"Dominant feelings observed: {', '.join(dominant_feelings)}.")
    if insights:
        sentences.append(f"One reflection to keep: {insights[0]}")
    if gentle_commitment:
        sentences.append(gentle_commitment)
    return " ".join(sentences).strip()


def generate_closing_message(risk: str, dilemma: str, closure_seed: dict[str, Any] | None = None) -> str:
    """Generate closing message, preferring reflection-trace seed when available."""
    trace_tail = _closure_seed_tail(closure_seed)

    if risk == "HIGH":
        base = (
            "Your current state may need additional support. Council is a reflection tool and "
            "cannot replace professional care. If you need immediate support, please call a local "
            "crisis hotline right away."
        )
        return f"{base} {trace_tail}".strip() if trace_tail else base

    if risk == "MEDIUM":
        base = (
            "Your emotions feel intense right now, and that is understandable. You do not need "
            "to make a final decision immediately. Consider getting extra support if this persists."
        )
        return f"{base} {trace_tail}".strip() if trace_tail else base

    base = (
        f"For '{dilemma}', you have listened to multiple inner voices and made the conflict clearer. "
        "You do not need to force a final answer right now."
    )
    return f"{base} {trace_tail}".strip() if trace_tail else base
