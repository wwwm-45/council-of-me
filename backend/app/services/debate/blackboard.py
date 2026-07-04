from __future__ import annotations

from pydantic import BaseModel, Field

from app.services.debate.artifacts import (
    AgentEvolution,
    ConvergenceMap,
    EngagementRecord,
    PositionMap,
    TensionMap,
)
from app.services.debate.round_state import DebatePhase


class StatementRecord(BaseModel):
    statement_id: str
    agent_id: str
    agent_name: str
    round_number: int
    content: str
    horizon: str | None = None
    reply_to: str | None = None
    exchange_seq: int | None = None
    type: str | None = None
    intervention_type: str | None = None


class AgentState(BaseModel):
    agent_id: str
    agent_name: str
    identity_card: dict[str, object]
    statements: dict[int, list[str]] = Field(default_factory=dict)
    challenged_points: list[str] = Field(default_factory=list)
    concessions: list[str] = Field(default_factory=list)
    evolution: AgentEvolution | None = None


class DebateBlackboard(BaseModel):
    session_id: str
    complexity: str
    dilemma: str
    phase: DebatePhase
    agents: dict[str, AgentState]
    position_map: PositionMap | None = None
    tension_map: TensionMap | None = None
    engagement_record: EngagementRecord | None = None
    convergence_map: ConvergenceMap | None = None
    transcript: list[StatementRecord] = Field(default_factory=list)
    interventions: list[dict[str, object]] = Field(default_factory=list)
    exchange_count_this_phase: int = 0
    consecutive_stable: int = 0
    evaluator_goal_met: bool = False
    phase_speaker_counts: dict[str, int] = Field(default_factory=dict)

    def record_statement(self, record: StatementRecord) -> None:
        self.transcript.append(record)
        agent = self.agents[record.agent_id]
        agent.statements.setdefault(record.round_number, []).append(record.content)
        if record.exchange_seq is not None:
            self.exchange_count_this_phase = max(self.exchange_count_this_phase, record.exchange_seq)
        self.phase_speaker_counts[record.agent_id] = self.phase_speaker_counts.get(record.agent_id, 0) + 1

    @property
    def all_statements(self) -> list[dict[str, object]]:
        rows = []
        for record in self.transcript:
            payload: dict[str, object] = {
                "statement_id": record.statement_id,
                "agent_id": record.agent_id,
                "agent_name": record.agent_name,
                "round_number": record.round_number,
                "content": record.content,
            }
            if record.reply_to is not None:
                payload["reply_to"] = record.reply_to
            if record.exchange_seq is not None:
                payload["exchange_seq"] = record.exchange_seq
            if record.horizon is not None:
                payload["horizon"] = record.horizon
            if record.type is not None:
                payload["type"] = record.type
            if record.intervention_type is not None:
                payload["intervention_type"] = record.intervention_type
            rows.append(payload)
        return rows
