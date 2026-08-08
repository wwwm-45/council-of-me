"""LLM-backed complexity evaluation for portrait composition."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from app.models.elicitation import ElicitationOutcome
from app.services.complexity import assign_debate_level, calculate_complexity_score
from app.services.conflict_profile import ConflictProfileGenerator
from app.services.llm import generate, is_llm_error

logger = logging.getLogger(__name__)

# (agent_count, max_rounds); max_rounds mirrors debate.round_state.COMPLEXITY_ROUNDS
_LEVEL_CONFIG: dict[str, tuple[int, int]] = {
    "L1": (2, 2),
    "L2": (4, 3),
    "L3": (5, 4),
}


@dataclass
class ComplexityAssessment:
    level: str
    agent_count: int
    max_rounds: int
    narrative: str
    reasoning: str
    key_factors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "agent_count": self.agent_count,
            "max_rounds": self.max_rounds,
            "narrative": self.narrative,
            "reasoning": self.reasoning,
            "key_factors": self.key_factors,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "ComplexityAssessment":
        return cls(
            level=str(payload.get("level") or "L2"),
            agent_count=int(payload.get("agent_count") or 4),
            max_rounds=int(payload.get("max_rounds") or 4),
            narrative=str(payload.get("narrative") or ""),
            reasoning=str(payload.get("reasoning") or ""),
            key_factors=list(payload.get("key_factors") or []),
        )


def _strip_json_block(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
    return text


class ComplexityEvaluator:
    async def evaluate(self, outcome: ElicitationOutcome) -> ComplexityAssessment:
        try:
            return await self._evaluate_with_llm(outcome)
        except Exception:
            logger.warning("ComplexityEvaluator failed; using fallback scoring.", exc_info=True)
            return self._fallback(outcome)

    async def _evaluate_with_llm(self, outcome: ElicitationOutcome) -> ComplexityAssessment:
        prompt = self._build_prompt(outcome)
        raw = await generate(
            prompt,
            system="你是一个内在困境复杂度评估专家。只返回 JSON。",
            temperature=0.2,
            max_tokens=600,
        )
        if not raw or is_llm_error(raw):
            raise ValueError("LLM error sentinel returned")

        payload = json.loads(_strip_json_block(raw))
        level = str(payload.get("level") or "L2")
        if level not in _LEVEL_CONFIG:
            level = "L2"
        agent_count, max_rounds = _LEVEL_CONFIG[level]
        key_factors = [str(item) for item in (payload.get("key_factors") or []) if str(item).strip()]

        return ComplexityAssessment(
            level=level,
            agent_count=agent_count,
            max_rounds=max_rounds,
            narrative=str(payload.get("narrative") or ""),
            reasoning=str(payload.get("reasoning") or ""),
            key_factors=key_factors,
        )

    def _build_prompt(self, outcome: ElicitationOutcome) -> str:
        layers = "\n".join(f"- [{item.depth}] {item.description}: {item.user_language}" for item in outcome.dilemma_layers) or "- None"
        voices = "\n".join(
            f"- {item.name}: concern={item.core_concern}; intent={item.protective_intent}; intensity={item.intensity:.2f}"
            for item in outcome.inner_voices
        ) or "- None"
        tensions = "\n".join(
            f"- {item.pole_a} <-> {item.pole_b}: {item.user_evidence}"
            for item in outcome.core_tensions
        ) or "- None"

        return (
            "请评估下面这个内在困境的讨论复杂度，并输出 JSON。\n"
            f"core_dilemma: {outcome.core_dilemma}\n"
            f"dilemma_layers:\n{layers}\n"
            f"voices:\n{voices}\n"
            f"tensions:\n{tensions}\n"
            f"conversation_depth: {outcome.conversation_depth:.2f}\n"
            f"max_depth_reached: {outcome.max_depth_reached:.2f}\n"
            f"depth_trajectory: {outcome.depth_trajectory}\n"
            "返回格式: "
            '{"level":"L1|L2|L3","narrative":"给用户看的中文说明","reasoning":"debug reasoning","key_factors":["factor"]}'
        )

    def _fallback(self, outcome: ElicitationOutcome) -> ComplexityAssessment:
        profile = ConflictProfileGenerator().generate_from_outcome(outcome)
        score = calculate_complexity_score(profile)
        level, agent_count, max_rounds = assign_debate_level(score)
        narrative = {
            "L1": "你的困境已经相对清晰，我们会先用两种核心视角来帮助你梳理。",
            "L2": "你的困境同时承载情绪、现实与价值层面的拉扯，适合由四个代表共同展开审视。",
            "L3": "你的困境已经触及更深层的存在议题，我们会用完整议会来承接这些复杂张力。",
        }[level]
        return ComplexityAssessment(
            level=level,
            agent_count=agent_count,
            max_rounds=max_rounds,
            narrative=narrative,
            reasoning=f"fallback_score={score:.1f}",
            key_factors=[],
        )
