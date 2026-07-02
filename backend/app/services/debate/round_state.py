"""
RoundStateMachine: tracks the diamond debate model phases.
Controls phase transitions, per-round agent speaking order,
and generates phase-specific prompt instructions.
"""
from enum import Enum
from typing import Optional


class DebatePhase(str, Enum):
    ROUND1_OPENING = "round1_opening"
    ROUND2_CROSS = "round2_cross"
    ROUND3_DEEPEN = "round3_deepen"
    R3_ACKNOWLEDGE = "r3_acknowledge"
    ROUND4_CONVERGE = "round4_converge"  # backward compat; no longer in natural progression
    R4_REFLECTION = "r4_reflection"
    R4_MAPPING = "r4_mapping"
    R4_FINAL = "r4_final"
    SYNTHESIS = "synthesis"
    TERMINATED = "terminated"


# Complexity level → max debate rounds
COMPLEXITY_ROUNDS: dict[str, int] = {
    "L1": 2,  # Opening + Cross-examination
    "L2": 3,  # + Deepening
    "L3": 4,  # + Convergence
}

# Soft preference: each agent's natural debate opponent.
# Used as a scoring signal (not a hard constraint) by DiscussionEngine.
OPPOSING_PAIRS: dict[str, Optional[str]] = {
    "empathic_listener": "rational_analyst",
    "rational_analyst": "empathic_listener",
    "critical_examiner": "creative_explorer",
    "creative_explorer": "critical_examiner",
    "synthesizer": None,  # responds to all
}


class RoundStateMachine:
    """
    Tracks debate progress through the diamond model.
    Orchestrator calls record_spoken() after each agent speaks,
    then should_advance_round() + advance() when all have spoken.
    """

    def __init__(self, complexity_level: str = "L2"):
        self._complexity = complexity_level
        self._max_rounds = COMPLEXITY_ROUNDS.get(complexity_level, 3)
        self._current_phase = DebatePhase.ROUND1_OPENING
        self._current_round = 1
        self._agents_spoken: set[str] = set()
        self._speak_counts: dict[str, int] = {}   # agent → speak count this round
        self._total_agents = 0

    # -- Properties --

    @property
    def current_phase(self) -> DebatePhase:
        return self._current_phase

    @property
    def current_round(self) -> int:
        if self._current_phase in (
            DebatePhase.R3_ACKNOWLEDGE,
            DebatePhase.R4_REFLECTION,
            DebatePhase.R4_MAPPING,
            DebatePhase.R4_FINAL,
        ):
            return 3 if self._current_phase == DebatePhase.R3_ACKNOWLEDGE else 4
        return self._current_round

    @property
    def max_rounds(self) -> int:
        return self._max_rounds

    # -- Agent tracking --

    def set_agent_count(self, count: int) -> None:
        self._total_agents = count

    def record_spoken(self, agent_id: str) -> None:
        self._agents_spoken.add(agent_id)
        self._speak_counts[agent_id] = self._speak_counts.get(agent_id, 0) + 1

    def should_advance_round(self) -> bool:
        return self._total_agents > 0 and len(self._agents_spoken) >= self._total_agents

    # -- Phase transitions --

    def advance(self) -> DebatePhase:
        """Advance to the next phase. Returns the new phase."""
        self._agents_spoken.clear()
        self._speak_counts.clear()

        if self._current_phase == DebatePhase.ROUND1_OPENING:
            self._current_phase = DebatePhase.ROUND2_CROSS
            self._current_round = 2
        elif self._current_phase == DebatePhase.ROUND2_CROSS:
            if self._max_rounds >= 3:
                self._current_phase = DebatePhase.ROUND3_DEEPEN
                self._current_round = 3
            else:
                self._current_phase = DebatePhase.SYNTHESIS
        elif self._current_phase == DebatePhase.ROUND3_DEEPEN:
            if self._max_rounds >= 4:
                self._current_phase = DebatePhase.R3_ACKNOWLEDGE
                self._current_round = 3
            else:
                self._current_phase = DebatePhase.SYNTHESIS
        elif self._current_phase == DebatePhase.ROUND4_CONVERGE:
            # backward-compat path: ROUND4_CONVERGE still goes to SYNTHESIS
            self._current_phase = DebatePhase.SYNTHESIS
        elif self._current_phase == DebatePhase.R3_ACKNOWLEDGE:
            self._current_phase = DebatePhase.R4_REFLECTION
            self._current_round = 4
        elif self._current_phase == DebatePhase.R4_REFLECTION:
            self._current_phase = DebatePhase.R4_MAPPING
        elif self._current_phase == DebatePhase.R4_MAPPING:
            self._current_phase = DebatePhase.R4_FINAL
        elif self._current_phase == DebatePhase.R4_FINAL:
            self._current_phase = DebatePhase.SYNTHESIS
        elif self._current_phase == DebatePhase.SYNTHESIS:
            self._current_phase = DebatePhase.TERMINATED

        return self._current_phase

    def skip_to_synthesis(self) -> DebatePhase:
        """
        Skip remaining rounds and jump directly to SYNTHESIS.
        Used when convergence is detected early (e.g., skip R4).
        """
        self._agents_spoken.clear()
        self._speak_counts.clear()
        self._current_phase = DebatePhase.SYNTHESIS
        return self._current_phase

    def is_done(self) -> bool:
        return self._current_phase in (DebatePhase.SYNTHESIS, DebatePhase.TERMINATED)
