"""Session and debate_sessions row model."""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from uuid import UUID


@dataclass
class DebateSessionRow:
    session_id: UUID
    user_id: UUID
    created_at: datetime
    completed_at: Optional[datetime]
    core_dilemma: str
    dilemma_type: Optional[str]
    complexity_score: Optional[float]
    debate_level: Optional[str]
    max_rounds: Optional[int]
    agent_count: Optional[int]
    total_rounds: Optional[int]
    total_duration_seconds: Optional[int]
    user_interventions_count: int
    status: str
    framing_preference: Optional[str]
    elicitation_history: Optional[Any]
    conflict_profile_snapshot: Optional[Any]
    identity_cards_snapshot: Optional[Any]
    crisis_returned_at: Optional[datetime]
    rounds_since_crisis_return: int
