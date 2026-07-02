"""
Phase 4.5: User interventions and annotations (in-memory store for archive).
Types: pause, resume, inject, steer, challenge, adjust, annotate.
"""

# session_id -> list of {intervention_type, round_number?, content?, target_agent_id?, ...}
_interventions: dict[str, list[dict]] = {}
# session_id -> list of {content, round_number?, agent_speaking?, emotion_tag?}
_annotations: dict[str, list[dict]] = {}


def record_intervention(
    session_id: str,
    intervention_type: str,
    *,
    round_number: int | None = None,
    content: str | None = None,
    target_agent_id: str | None = None,
    agent_response: str | None = None,
    intensity: float | None = None,
) -> None:
    """Record one user intervention for later archive."""
    if session_id not in _interventions:
        _interventions[session_id] = []
    _interventions[session_id].append({
        "intervention_type": intervention_type,
        "round_number": round_number,
        "content": content,
        "target_agent_id": target_agent_id,
        "agent_response": agent_response,
        "intensity": intensity,
    })


def get_interventions(session_id: str) -> list[dict]:
    """Return list of interventions for session (for Phase 7 archive)."""
    return list(_interventions.get(session_id) or [])


def get_annotations(session_id: str) -> list[dict]:
    """Return list of annotations for session (for Phase 7 archive)."""
    return list(_annotations.get(session_id) or [])
