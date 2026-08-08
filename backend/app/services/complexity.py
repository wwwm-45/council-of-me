"""Phase 2 complexity scoring and debate level assignment."""

from typing import Any


def _emotion_dimension(profile: dict[str, Any]) -> tuple[list[str], float]:
    emotions = profile.get("emotions")
    if isinstance(emotions, list):
        items = [str(item) for item in emotions if str(item).strip()]
        score = min(len(items) * 6, 25) if items else 12
        return items, score

    emotional_tone = profile.get("emotional_tone") or {}
    if isinstance(emotional_tone, dict):
        nested = emotional_tone.get("emotions", emotional_tone)
        if isinstance(nested, dict):
            items = [str(key) for key in nested.keys()]
            total = sum(float(value) for value in nested.values()) if nested else 0.0
            score = min(total * 10, 25) if nested else 12
            return items, score

    return [], 12


def calculate_complexity_dimensions(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return per-dimension breakdown with scores and items."""
    value_conflicts = profile.get("value_conflicts") or []
    value_score = min(len(value_conflicts) * 10, 30)

    emotion_items, emotion_score = _emotion_dimension(profile)

    reversibility = profile.get("reversibility", "medium")
    reversibility_score = {"high": 5, "medium": 15, "low": 25}.get(reversibility, 15)

    stakeholders = profile.get("stakeholders") or []
    stakeholder_score = min(len(stakeholders) * 3, 10)

    explicit = profile.get("user_requested_depth") or 0
    explicit_score = min(float(explicit) * 10, 10)

    conversation_depth = float(profile.get("conversation_depth") or 0.0)
    conversation_depth_score = min(round(conversation_depth * 15), 15)

    return {
        "value_conflicts": {"score": value_score, "max": 30, "items": value_conflicts},
        "emotions": {"score": emotion_score, "max": 25, "items": emotion_items},
        "reversibility": {"score": reversibility_score, "max": 25, "level": reversibility},
        "stakeholders": {"score": stakeholder_score, "max": 10, "items": stakeholders},
        "explicit_depth": {"score": explicit_score, "max": 10, "value": explicit},
        "conversation_depth": {"score": conversation_depth_score, "max": 15, "value": conversation_depth},
    }


def calculate_complexity_score(profile: dict[str, Any]) -> float:
    """Complexity score from the sum of dimension scores."""
    total = sum(dimension["score"] for dimension in calculate_complexity_dimensions(profile).values())
    return min(total, 100.0)


def assign_debate_level(score: float) -> tuple[str, int, int]:
    """Return (debate_level, agent_count, max_rounds).

    max_rounds must match debate.round_state.COMPLEXITY_ROUNDS — that table
    is what actually gates RoundStateMachine.advance().
    """
    if score < 40:
        return "L1", 2, 2
    if score < 70:
        return "L2", 4, 3
    return "L3", 5, 4
