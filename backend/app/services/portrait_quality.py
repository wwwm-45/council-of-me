"""Deterministic quality checks for elicitation-derived portraits."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.models.elicitation import ElicitationOutcome, normalize_comparable_text
from app.services.elicitation_control import is_process_intent


@dataclass
class PortraitQualityIssue:
    code: str
    severity: str
    message: str
    suggestion: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "suggestion": self.suggestion,
        }


@dataclass
class PortraitQualityResult:
    status: str
    score: float
    issues: list[PortraitQualityIssue] = field(default_factory=list)
    can_force_continue: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "score": self.score,
            "issues": [issue.to_dict() for issue in self.issues],
            "can_force_continue": self.can_force_continue,
        }


class PortraitQualityGate:
    """Evaluate whether portrait evidence is rich enough for downstream debate."""

    def evaluate(self, outcome: ElicitationOutcome, profile: dict[str, Any]) -> PortraitQualityResult:
        issues: list[PortraitQualityIssue] = []

        if not self._has_tension_or_value_pair(outcome, profile):
            issues.append(
                PortraitQualityIssue(
                    code="missing_tension",
                    severity="high",
                    message="画像还没有形成清晰的张力或价值冲突。",
                    suggestion="请补充这个困境的两端分别是什么，以及你为什么难以取舍。",
                )
            )

        if not outcome.dilemma_layers:
            issues.append(
                PortraitQualityIssue(
                    code="missing_layers",
                    severity="high",
                    message="画像还缺少能支撑判断的困境层次。",
                    suggestion="请至少补充一个具体场景、担心或取舍，再继续进入辩论。",
                )
            )
        elif self._layers_are_duplicate(outcome):
            issues.append(
                PortraitQualityIssue(
                    code="duplicate_layers",
                    severity="medium",
                    message="当前困境层次有重复，暂时没有继续加深。",
                    suggestion="请补充一个不同层面的证据，例如情绪代价、现实约束或更深的在意。",
                )
            )

        if outcome.emotion_map and any(not str(item.context).strip() for item in outcome.emotion_map):
            issues.append(
                PortraitQualityIssue(
                    code="thin_emotion_context",
                    severity="medium",
                    message="有些情绪还缺少具体依附的场景。",
                    suggestion="请补充这些情绪和哪个处境、记忆或取舍有关。",
                )
            )

        if self._voices_are_indistinct(outcome):
            issues.append(
                PortraitQualityIssue(
                    code="indistinct_voices",
                    severity="high",
                    message="内在声音还不够清楚，难以支持不同角色展开辩论。",
                    suggestion="请至少区分两个声音：它们各自担心什么、想保护什么。",
                )
            )

        if self._evidence_is_process_dominated(outcome):
            issues.append(
                PortraitQualityIssue(
                    code="process_only_evidence",
                    severity="high",
                    message="当前画像主要来自流程指令，而不是实质困境内容。",
                    suggestion="请补充真实的选择、担心或冲突，再继续进入辩论。",
                )
            )

        score = self._score(issues)
        return PortraitQualityResult(
            status="pass" if not issues else "warn",
            score=score,
            issues=issues,
            can_force_continue=True,
        )

    def _has_tension_or_value_pair(self, outcome: ElicitationOutcome, profile: dict[str, Any]) -> bool:
        for tension in outcome.core_tensions:
            if self._meaningful_pair(tension.pole_a, tension.pole_b):
                return True
        for conflict in outcome.value_conflicts:
            if self._meaningful_pair(conflict.value_a, conflict.value_b):
                return True

        for item in self._profile_list(profile, "core_tension_pairs"):
            if self._meaningful_profile_pair(item, ("pole_a", "value_a", "left", "a"), ("pole_b", "value_b", "right", "b")):
                return True
        for item in self._profile_list(profile, "core_tensions"):
            if self._meaningful_profile_pair(item, ("pole_a", "value_a", "left", "a"), ("pole_b", "value_b", "right", "b")):
                return True
        for item in self._profile_list(profile, "value_conflicts"):
            if self._meaningful_profile_pair(item, ("value_a", "pole_a", "left", "a"), ("value_b", "pole_b", "right", "b")):
                return True

        if self._has_distinct_inner_voice_pair(outcome):
            return True

        return False

    def _has_distinct_inner_voice_pair(self, outcome: ElicitationOutcome) -> bool:
        return not self._voices_are_indistinct(outcome)

    def _meaningful_profile_pair(
        self,
        item: Any,
        left_keys: tuple[str, ...],
        right_keys: tuple[str, ...],
    ) -> bool:
        if isinstance(item, dict):
            left = self._first_text(item, left_keys)
            right = self._first_text(item, right_keys)
            return self._meaningful_pair(left, right)
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            return self._meaningful_pair(item[0], item[1])
        if isinstance(item, str):
            return self._meaningful_profile_text(item)
        return False

    def _meaningful_profile_text(self, text: str) -> bool:
        patterns = [
            r"on one side\s*:\s*(.+?)\s*;\s*on the other\s*:\s*(.+)",
            r"one side\s*:\s*(.+?)\s*;\s*other side\s*:\s*(.+)",
            r"(.+?)\s+(?:vs\.?|versus)\s+(.+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match and self._meaningful_pair(match.group(1), match.group(2)):
                return True
        return False

    def _meaningful_pair(self, left: Any, right: Any) -> bool:
        left_text = normalize_comparable_text(left)
        right_text = normalize_comparable_text(right)
        return bool(left_text and right_text and left_text != right_text)

    def _layers_are_duplicate(self, outcome: ElicitationOutcome) -> bool:
        if any(self._layer_is_thin(layer.description, layer.user_language) for layer in outcome.dilemma_layers):
            return True

        all_descriptions_equal_evidence = all(
            normalize_comparable_text(layer.description)
            and normalize_comparable_text(layer.description) == normalize_comparable_text(layer.user_language)
            for layer in outcome.dilemma_layers
        )
        if all_descriptions_equal_evidence:
            return True

        if len(outcome.dilemma_layers) < 2:
            return False

        descriptions = [
            normalize_comparable_text(layer.description)
            for layer in outcome.dilemma_layers
            if normalize_comparable_text(layer.description)
        ]
        return bool(descriptions and len(set(descriptions)) < len(descriptions))

    def _layer_is_thin(self, description: Any, user_language: Any) -> bool:
        description_text = normalize_comparable_text(description)
        user_language_text = normalize_comparable_text(user_language)
        generic_descriptions = {"unclear", "unknown", "notsure", "unsure", "thin", "vague"}
        return (
            not user_language_text
            or not description_text
            or description_text in generic_descriptions
            or len(description_text) < 12
        )

    def _voices_are_indistinct(self, outcome: ElicitationOutcome) -> bool:
        voices = outcome.inner_voices
        if len(voices) < 2:
            return True

        names = [normalize_comparable_text(voice.name) for voice in voices]
        usable_names = [name for name in names if name]
        if len(set(usable_names)) < len(usable_names):
            return True

        signatures = []
        for voice in voices:
            signature = normalize_comparable_text(
                " ".join(
                    [
                        voice.core_concern,
                        voice.protective_intent,
                        voice.fear,
                        voice.language_style,
                        " ".join(voice.typical_phrases),
                    ]
                )
            )
            signatures.append(signature)

        usable_signatures = [signature for signature in signatures if self._voice_signature_is_usable(signature)]
        if len(usable_signatures) < 2:
            return True
        return len(set(usable_signatures)) < len(usable_signatures)

    def _voice_signature_is_usable(self, signature: str) -> bool:
        generic_terms = {"helpme", "protectme", "avoidpain", "besafe", "supportme"}
        generic_remainder = signature
        while generic_remainder:
            for term in generic_terms:
                if generic_remainder.startswith(term):
                    generic_remainder = generic_remainder[len(term) :]
                    break
            else:
                break
        generic_only = bool(signature and not generic_remainder)
        return bool(signature and not generic_only and len(signature) >= 12)

    def _evidence_is_process_dominated(self, outcome: ElicitationOutcome) -> bool:
        texts = [outcome.core_dilemma, *outcome.key_expressions]
        for layer in outcome.dilemma_layers:
            texts.extend([layer.description, layer.user_language])

        meaningful_texts = [text for text in texts if str(text).strip()]
        if not meaningful_texts:
            return False

        process_count = sum(1 for text in meaningful_texts if is_process_intent(str(text)))
        return process_count > 0 and process_count * 2 >= len(meaningful_texts)

    def _score(self, issues: list[PortraitQualityIssue]) -> float:
        weights = {"high": 0.18, "medium": 0.11, "low": 0.05}
        penalty = sum(weights.get(issue.severity, 0.08) for issue in issues)
        return round(max(0.0, 1.0 - penalty), 2)

    def _profile_list(self, profile: dict[str, Any], key: str) -> list[Any]:
        value = profile.get(key) if isinstance(profile, dict) else None
        return value if isinstance(value, list) else []

    def _first_text(self, payload: dict[str, Any], keys: tuple[str, ...]) -> str:
        for key in keys:
            value = payload.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""
