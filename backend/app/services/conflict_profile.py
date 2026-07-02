"""Conflict profile generation from elicitation outputs."""

from typing import Any, Iterable

from app.models.elicitation import ElicitationOutcome, TensionCard
from app.services.complexity import assign_debate_level, calculate_complexity_score
from app.services.outcome_extractor import core_tensions_from_cards
from app.services.psyche.builder import PsycheBundleBuilder


class ConflictProfileGenerator:
    """Build the downstream profile shape expected by later phases."""

    def generate(
        self,
        extracted_info: dict[str, Any],
        conversation_history: list[dict],
        pain_analysis: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        pain_analysis = pain_analysis or {}
        profile = {
            "core_dilemma": extracted_info.get("core_dilemma") or "Inner conflict",
            "dilemma_type": "decision",
            "dilemmas": pain_analysis.get("dilemmas") or [],
            "pain_points": pain_analysis.get("pain_points") or [],
            "core_tensions": pain_analysis.get("core_tensions") or [],
            "inner_voices": extracted_info.get("inner_voices") or [],
            "value_conflicts": self._value_conflicts_from_values(extracted_info.get("values") or []),
            "emotions": extracted_info.get("emotions") or [],
            "emotional_tone": {"emotions": {emotion: 0.5 for emotion in (extracted_info.get("emotions") or [])}},
            "stakeholders": extracted_info.get("stakeholders") or [],
            "constraints": extracted_info.get("constraints") or [],
            "reversibility": "medium",
            "time_pressure": "medium",
        }
        return self._finalize_profile(profile)

    def generate_from_outcome(
        self,
        outcome: ElicitationOutcome,
        *,
        tension_cards: list[Any] | None = None,
    ) -> dict[str, Any]:
        inner_voices = [
            {
                "name": voice.name,
                "core_concern": voice.core_concern,
                "protective_intent": voice.protective_intent,
                "intensity": voice.intensity,
                "fear": voice.fear,
                "language_style": voice.language_style,
                "typical_phrases": list(voice.typical_phrases),
                "relationship_to_others": list(voice.relationship_to_others),
            }
            for voice in outcome.inner_voices
        ]
        if len(inner_voices) < 2:
            inner_voices.extend(
                [
                    {
                        "name": "Security",
                        "core_concern": "Stability",
                        "protective_intent": "Avoiding loss",
                        "intensity": 0.5,
                        "fear": "",
                        "language_style": "",
                        "typical_phrases": [],
                        "relationship_to_others": [],
                    },
                    {
                        "name": "Growth",
                        "core_concern": "Possibility",
                        "protective_intent": "Protecting vitality",
                        "intensity": 0.5,
                        "fear": "",
                        "language_style": "",
                        "typical_phrases": [],
                        "relationship_to_others": [],
                    },
                ][len(inner_voices) :]
            )

        value_conflicts = [
            {
                "value_a": item.value_a,
                "value_b": item.value_b,
                "tension_description": item.context or f"{item.value_a} vs {item.value_b}",
            }
            for item in outcome.value_conflicts
        ]

        if not outcome.core_tensions and tension_cards:
            normalized_cards = [
                card if isinstance(card, TensionCard) else TensionCard.from_dict(card)
                for card in tension_cards
                if isinstance(card, (TensionCard, dict))
            ]
            derived = core_tensions_from_cards(normalized_cards)
            if derived:
                outcome.core_tensions = derived

        core_tension_pairs = [item.to_dict() for item in outcome.core_tensions]
        core_tensions = [
            f"On one side: {item.pole_a}; on the other: {item.pole_b}"
            for item in outcome.core_tensions
        ]
        dilemma_layers = [layer.to_dict() for layer in outcome.dilemma_layers]
        dilemmas = self._dedupe_texts(layer.description for layer in outcome.dilemma_layers)
        emotion_map = [entry.to_dict() for entry in outcome.emotion_map]
        pain_points = [entry.context for entry in outcome.emotion_map if entry.context]
        emotions = [entry.emotion for entry in outcome.emotion_map]
        stakeholders = [item.name for item in outcome.stakeholders]

        profile = {
            "core_dilemma": outcome.core_dilemma,
            "dilemma_type": "decision",
            "dilemmas": dilemmas,
            "dilemma_layers": dilemma_layers,
            "pain_points": pain_points,
            "core_tensions": core_tensions,
            "core_tension_pairs": core_tension_pairs,
            "inner_voices": inner_voices[:5],
            "value_conflicts": value_conflicts[:5],
            "emotions": emotions,
            "emotion_map": emotion_map,
            "emotional_tone": {"emotions": {entry.emotion: entry.intensity for entry in outcome.emotion_map}},
            "stakeholders": stakeholders,
            "constraints": [],
            "reversibility": self._infer_reversibility(outcome),
            "time_pressure": "medium",
            "conversation_depth": outcome.conversation_depth,
            "max_depth_reached": outcome.max_depth_reached,
            "depth_trajectory": outcome.depth_trajectory,
            "closing_readiness": outcome.closing_readiness,
        }
        profile["psyche_bundle"] = PsycheBundleBuilder().from_outcome(
            outcome,
            tension_cards or [],
        ).to_dict()
        return self._finalize_profile(profile)

    def _value_conflicts_from_values(self, values: list[str]) -> list[dict[str, str]]:
        conflicts = []
        for index, left in enumerate(values[:5]):
            for right in values[index + 1 : index + 2]:
                if left != right:
                    conflicts.append(
                        {
                            "value_a": left,
                            "value_b": right,
                            "tension_description": f"{left} vs {right}",
                        }
                    )
        return conflicts

    def _infer_reversibility(self, outcome: ElicitationOutcome) -> str:
        depth_lookup = {"surface": 0, "emotional": 1, "existential": 2}
        max_layer = max((depth_lookup.get(layer.depth, 0) for layer in outcome.dilemma_layers), default=0)
        if max_layer >= 2 or outcome.conversation_depth >= 0.7:
            return "low"
        if max_layer >= 1 or outcome.conversation_depth >= 0.4:
            return "medium"
        return "high"

    def _finalize_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        score = calculate_complexity_score(profile)
        level, agent_count, max_rounds = assign_debate_level(score)
        profile["complexity_score"] = score
        profile["debate_level"] = level
        profile["agent_count"] = agent_count
        profile["max_rounds"] = max_rounds
        return profile

    def _dedupe_texts(self, values: Iterable[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            deduped.append(text)
        return deduped
