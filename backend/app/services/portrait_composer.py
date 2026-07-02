"""Portrait composition and council recomposition."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.elicitation import ElicitationOutcome
from app.services.agent_mapper import AgentAssignment, AgentMapper
from app.services.complexity_evaluator import ComplexityAssessment, ComplexityEvaluator

_LEVEL_CONFIG: dict[str, tuple[int, int]] = {
    "L1": (2, 3),
    "L2": (4, 4),
    "L3": (5, 5),
}


@dataclass
class QuotePlacement:
    after_section: str
    quote: str
    source_emotion: str = ""

    def to_dict(self) -> dict:
        return {
            "after_section": self.after_section,
            "quote": self.quote,
            "source_emotion": self.source_emotion,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "QuotePlacement":
        return cls(
            after_section=str(payload.get("after_section") or ""),
            quote=str(payload.get("quote") or ""),
            source_emotion=str(payload.get("source_emotion") or ""),
        )


@dataclass
class Portrait:
    core_dilemma: str
    dilemma_layers: list[dict]
    inner_voices: list[dict]
    core_tensions: list[dict]
    emotion_map: list[dict]
    complexity: ComplexityAssessment
    agent_assignments: list[AgentAssignment]
    quote_placements: list[QuotePlacement] = field(default_factory=list)
    conversation_depth: float = 0.0
    depth_trajectory: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "core_dilemma": self.core_dilemma,
            "dilemma_layers": self.dilemma_layers,
            "inner_voices": self.inner_voices,
            "core_tensions": self.core_tensions,
            "emotion_map": self.emotion_map,
            "complexity": self.complexity.to_dict(),
            "agent_assignments": [item.to_dict() for item in self.agent_assignments],
            "quote_placements": [item.to_dict() for item in self.quote_placements],
            "conversation_depth": self.conversation_depth,
            "depth_trajectory": self.depth_trajectory,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Portrait":
        return cls(
            core_dilemma=str(payload.get("core_dilemma") or ""),
            dilemma_layers=list(payload.get("dilemma_layers") or []),
            inner_voices=list(payload.get("inner_voices") or []),
            core_tensions=list(payload.get("core_tensions") or []),
            emotion_map=list(payload.get("emotion_map") or []),
            complexity=ComplexityAssessment.from_dict(payload.get("complexity") or {}),
            agent_assignments=[
                AgentAssignment.from_dict(item)
                for item in (payload.get("agent_assignments") or [])
                if isinstance(item, dict)
            ],
            quote_placements=[
                QuotePlacement.from_dict(item)
                for item in (payload.get("quote_placements") or [])
                if isinstance(item, dict)
            ],
            conversation_depth=float(payload.get("conversation_depth") or 0.0),
            depth_trajectory=[float(item) for item in (payload.get("depth_trajectory") or [])],
        )


class PortraitComposer:
    def __init__(
        self,
        *,
        complexity_evaluator: ComplexityEvaluator | None = None,
        agent_mapper: AgentMapper | None = None,
    ) -> None:
        self._complexity_evaluator = complexity_evaluator or ComplexityEvaluator()
        self._agent_mapper = agent_mapper or AgentMapper()

    async def compose(
        self,
        outcome: ElicitationOutcome,
        *,
        framing_preference: str | None = None,
        level_override: str | None = None,
    ) -> Portrait:
        complexity = await self._complexity_evaluator.evaluate(outcome)
        if level_override:
            complexity = self._override_level(complexity, level_override)
        assignments = await self._agent_mapper.map_voices(
            outcome.inner_voices,
            outcome.core_tensions,
            complexity.level,
            framing_preference=framing_preference,
        )
        return self._build_portrait(outcome, complexity, assignments)

    async def recompose_council(
        self,
        outcome: ElicitationOutcome,
        portrait: Portrait,
        *,
        level_override: str | None = None,
        framing_preference: str | None = None,
    ) -> Portrait:
        complexity = portrait.complexity
        if level_override:
            complexity = self._override_level(complexity, level_override)
        assignments = await self._agent_mapper.map_voices(
            outcome.inner_voices,
            outcome.core_tensions,
            complexity.level,
            framing_preference=framing_preference,
        )
        return self._build_portrait(outcome, complexity, assignments)

    def _build_portrait(
        self,
        outcome: ElicitationOutcome,
        complexity: ComplexityAssessment,
        assignments: list[AgentAssignment],
    ) -> Portrait:
        return Portrait(
            core_dilemma=outcome.core_dilemma,
            dilemma_layers=[item.to_dict() for item in outcome.dilemma_layers],
            inner_voices=[item.to_dict() for item in outcome.inner_voices],
            core_tensions=[item.to_dict() for item in outcome.core_tensions],
            emotion_map=[item.to_dict() for item in outcome.emotion_map],
            complexity=complexity,
            agent_assignments=assignments,
            quote_placements=self._select_quotes(outcome),
            conversation_depth=outcome.conversation_depth,
            depth_trajectory=outcome.depth_trajectory,
        )

    def _override_level(self, complexity: ComplexityAssessment, level_override: str) -> ComplexityAssessment:
        level = level_override if level_override in _LEVEL_CONFIG else complexity.level
        agent_count, max_rounds = _LEVEL_CONFIG[level]
        return ComplexityAssessment(
            level=level,
            agent_count=agent_count,
            max_rounds=max_rounds,
            narrative=complexity.narrative,
            reasoning=complexity.reasoning,
            key_factors=list(complexity.key_factors),
        )

    def _select_quotes(self, outcome: ElicitationOutcome) -> list[QuotePlacement]:
        expressions = []
        seen: set[str] = set()
        for item in outcome.key_expressions:
            text = str(item).strip()
            if text and text not in seen:
                expressions.append(text)
                seen.add(text)

        if not expressions:
            return []

        def score(text: str) -> tuple[int, int]:
            contrast_bonus = sum(token in text for token in ("但", "但是", "可是", "却", "一方面", "另一方面"))
            emotion_bonus = sum(token in text for token in ("怕", "想", "渴望", "困住", "挣扎", "不想"))
            return contrast_bonus + emotion_bonus, len(text)

        ranked = sorted(expressions, key=score, reverse=True)
        slots = ["dilemma", "voices"]
        source_emotion = outcome.emotion_map[0].emotion if outcome.emotion_map else ""
        return [
            QuotePlacement(after_section=slot, quote=quote, source_emotion=source_emotion)
            for slot, quote in zip(slots, ranked[:2], strict=False)
        ]
