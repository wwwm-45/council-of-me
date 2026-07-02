from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
import re
from typing import Any
from uuid import uuid4

from app.services.debate.spine import DebateSpine


_CJK_OR_WORD_RE = re.compile(r"[\u4e00-\u9fff]+|[a-z0-9]+", re.IGNORECASE)


@dataclass(frozen=True)
class AuditFinding:
    code: str
    message: str
    severity: str = "medium"
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class AuditResult:
    audit_id: str
    stage: str
    status: str
    summary: str
    findings: list[AuditFinding]
    metrics: dict[str, float]
    recommended_action: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "stage": self.stage,
            "status": self.status,
            "summary": self.summary,
            "findings": [item.to_dict() for item in self.findings],
            "metrics": dict(self.metrics),
            "recommended_action": self.recommended_action,
            "created_at": self.created_at,
        }


class DebateAuditService:
    def audit_preflight(
        self,
        *,
        profile: dict[str, Any],
        spine: DebateSpine,
        identity_cards: list[dict[str, Any]],
    ) -> AuditResult:
        findings: list[AuditFinding] = []

        tension_pairs = profile.get("core_tension_pairs") or []
        tension_texts = profile.get("core_tensions") or []
        value_conflicts = profile.get("value_conflicts") or []
        dilemma_layers = profile.get("dilemma_layers") or []
        emotion_map = profile.get("emotion_map") or []
        has_debatable_tension = bool(
            tension_pairs
            or tension_texts
            or value_conflicts
            or self._has_distinct_inner_voice_pair(profile)
        )

        if not has_debatable_tension:
            findings.append(
                AuditFinding(
                    code="false_progress",
                    message=(
                        "\u7f3a\u5c11\u53ef\u4ee5\u652f\u6491\u8fa9\u8bba\u7684\u5f20"
                        "\u529b\u5bf9\uff0c\u5f00\u573a\u5f88\u5bb9\u6613\u53d8\u6210"
                        "\u4e00\u7ec4\u5404\u8bf4\u5404\u7684\u89c2\u70b9\u7f57\u5217\u3002"
                    ),
                    severity="high",
                    evidence=[spine.core_contradiction] if spine.core_contradiction else [],
                )
            )

        normalized_layers = [
            self._normalize_text(
                layer.get("user_language") or layer.get("description") or ""
            )
            for layer in dilemma_layers
        ]
        normalized_layers = [item for item in normalized_layers if item]
        if len(normalized_layers) >= 2 and len(set(normalized_layers)) <= 1:
            findings.append(
                AuditFinding(
                    code="parallel_monologues",
                    message=(
                        "\u56f0\u5883\u5c42\u6b21\u51e0\u4e4e\u662f\u540c\u4e00\u53e5\u8bdd"
                        "\u91cd\u590d\uff0c\u4e0d\u8db3\u4ee5\u652f\u6491\u591a\u4e2a"
                        "\u89d2\u8272\u56f4\u7ed5\u540c\u4e00\u6838\u5fc3\u77db\u76fe"
                        "\u5c55\u5f00\u771f\u6b63\u4ea4\u950b\u3002"
                    ),
                    severity="high",
                )
            )

        if emotion_map and not any(self._emotion_has_context(item) for item in emotion_map):
            findings.append(
                AuditFinding(
                    code="thin_emotion_context",
                    message=(
                        "\u60c5\u7eea\u4fe1\u53f7\u5b58\u5728\uff0c\u4f46\u7f3a\u5c11"
                        "\u5177\u4f53\u60c5\u5883\uff0c\u540e\u7eed\u53d1\u8a00\u5bb9\u6613"
                        "\u6ed1\u5411\u7a7a\u6cdb\u8868\u6001\u3002"
                    ),
                    severity="medium",
                )
            )

        signal_count = 0
        signal_count += (
            1
            if tension_pairs
            or tension_texts
            or self._has_distinct_inner_voice_pair(profile)
            else 0
        )
        signal_count += 1 if value_conflicts else 0
        signal_count += 1 if normalized_layers else 0
        signal_count += 1 if any(self._emotion_has_context(item) for item in emotion_map) else 0
        signal_count += 1 if spine.active_questions else 0
        signal_count += 1 if identity_cards else 0
        metrics = {"signal_density": round(signal_count / 6.0, 3)}

        if findings:
            summary = "\u3001".join(item.message for item in findings[:2])
            has_blocking_issue = any(item.severity == "high" for item in findings)
            return self._result(
                stage="preflight",
                status="fail" if has_blocking_issue else "needs_review",
                summary=summary,
                findings=findings,
                metrics=metrics,
                recommended_action=(
                    "block_start" if has_blocking_issue else "tighten_opening_prompt"
                ),
            )

        return self._result(
            stage="preflight",
            status="pass",
            summary=(
                "\u8fa9\u8bba\u5f00\u573a\u6240\u9700\u7684\u5f20\u529b\u3001\u95ee\u9898"
                "\u548c\u89d2\u8272\u6293\u624b\u5df2\u5177\u5907\u3002"
            ),
            findings=[],
            metrics=metrics,
            recommended_action="none",
        )

    def audit_round(
        self,
        *,
        phase: str,
        spine: DebateSpine,
        statements: list[dict[str, Any]],
        artifact: dict[str, Any],
    ) -> AuditResult:
        contents = [
            (statement.get("content") or "").strip()
            for statement in statements
            if (statement.get("content") or "").strip()
        ]
        metrics = {
            "novelty": self._score_novelty(contents),
            "focus": self._score_focus(contents, spine, artifact),
        }
        findings: list[AuditFinding] = []

        repeated_pair = self._find_same_point_rephrasing(contents)
        if repeated_pair is not None:
            left, right, overlap = repeated_pair
            severity = "high" if overlap >= 0.65 else "medium"
            findings.append(
                AuditFinding(
                    code="same_point_rephrasing",
                    message=(
                        "\u591a\u4e2a agent \u8fd8\u5728\u56f4\u7740\u540c\u4e00\u4e2a"
                        "\u538b\u529b\u70b9\u6362\u53e5\u5f0f\u91cd\u590d\uff0c\u4f46"
                        "\u6ca1\u6709\u63a8\u8fdb\u65b0\u4fe1\u606f\u6216\u65b0\u5206\u6b67\u3002"
                    ),
                    severity=severity,
                    evidence=[left, right],
                )
            )

        recommended_action = self._pick_round_action(findings=findings, metrics=metrics)
        status = "pass" if not findings else "needs_review"
        summary = self._summarize_round(findings=findings, metrics=metrics, artifact=artifact)

        return self._result(
            stage=phase,
            status=status,
            summary=summary,
            findings=findings,
            metrics=metrics,
            recommended_action=recommended_action,
        )

    @staticmethod
    def _normalize_text(text: str) -> str:
        lowered = (text or "").strip().lower()
        if not lowered:
            return ""
        return "".join(_CJK_OR_WORD_RE.findall(lowered))

    def _has_distinct_inner_voice_pair(self, profile: dict[str, Any]) -> bool:
        voices = profile.get("inner_voices") or []
        if not isinstance(voices, list) or len(voices) < 2:
            return False

        signatures = []
        for voice in voices:
            if not isinstance(voice, dict):
                continue
            signature = self._voice_signature(voice)
            if self._voice_signature_is_usable(signature):
                signatures.append(signature)

        if len(signatures) < 2:
            return False

        for left_index, left in enumerate(signatures):
            for right in signatures[left_index + 1 :]:
                if left != right and SequenceMatcher(None, left, right).ratio() < 0.86:
                    return True
        return False

    def _voice_signature(self, voice: dict[str, Any]) -> str:
        values: list[str] = []
        for key in (
            "core_concern",
            "protective_intent",
            "fear",
            "language_style",
        ):
            values.append(str(voice.get(key) or ""))
        for key in ("typical_phrases", "relationship_to_others"):
            value = voice.get(key)
            if isinstance(value, list):
                values.extend(str(item) for item in value)
            else:
                values.append(str(value or ""))
        return self._normalize_text(" ".join(values))

    def _voice_signature_is_usable(self, signature: str) -> bool:
        generic_signatures = {
            "stabilityavoidingloss",
            "possibilityprotectingvitality",
            "securitystabilityavoidingloss",
            "growthpossibilityprotectingvitality",
            "helpmeprotectme",
            "avoidpainbesafe",
            "supportme",
        }
        if not signature or len(signature) < 12:
            return False
        return signature not in generic_signatures

    def _emotion_has_context(self, item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        return any(
            str(item.get(key) or "").strip()
            for key in ("context", "source", "trigger")
        )

    def _find_same_point_rephrasing(
        self,
        contents: list[str],
    ) -> tuple[str, str, float] | None:
        normalized = [self._normalize_text(text) for text in contents]
        normalized = [item for item in normalized if item]
        if len(normalized) < 2:
            return None

        best_pair: tuple[str, str, float] | None = None
        for left_index in range(len(normalized)):
            for right_index in range(left_index + 1, len(normalized)):
                left = normalized[left_index]
                right = normalized[right_index]
                overlap = self._pair_overlap(left, right)
                longest_shared = self._longest_shared_span(left, right)
                if overlap >= 0.45 or longest_shared >= 4:
                    candidate = (contents[left_index], contents[right_index], overlap)
                    if best_pair is None or overlap > best_pair[2]:
                        best_pair = candidate
        return best_pair

    def _score_novelty(self, contents: list[str]) -> float:
        normalized = [self._normalize_text(text) for text in contents if text]
        if len(normalized) < 2:
            return 1.0

        max_overlap = 0.0
        for left_index in range(len(normalized)):
            for right_index in range(left_index + 1, len(normalized)):
                max_overlap = max(
                    max_overlap,
                    self._pair_overlap(normalized[left_index], normalized[right_index]),
                )
        return round(max(0.0, 1.0 - max_overlap), 3)

    def _score_focus(
        self,
        contents: list[str],
        spine: DebateSpine,
        artifact: dict[str, Any],
    ) -> float:
        """Anchor fidelity in [0, 1].

        This metric measures how much the current round stays attached to the
        debate spine's active anchors: the core contradiction, the current
        dispute, the unresolved issue, and the leading cost-ledger items.

        It intentionally does not mean "progress on unresolved tensions".
        That would be a different metric. A round can therefore have high
        `novelty` and low `focus`: the agents introduced new language, but the
        new material drifted away from the spine anchors.

        The persisted metric key remains `focus` for backward compatibility.
        """
        summary = artifact.get("summary") or {}
        anchors = [
            spine.core_contradiction,
            summary.get("current_dispute", ""),
            summary.get("unresolved_issue", ""),
            *spine.cost_ledger[:3],
        ]
        keywords = {
            token
            for source in anchors
            for token in self._extract_keywords(source)
            if len(token) >= 2
        }
        if not keywords:
            return 0.5

        joined = " ".join(contents)
        matched = sum(1 for token in keywords if token in joined)
        return round(min(1.0, matched / max(len(keywords), 1)), 3)

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        return _CJK_OR_WORD_RE.findall((text or "").lower())

    @staticmethod
    def _pair_overlap(left: str, right: str) -> float:
        if not left or not right:
            return 0.0
        left_bigrams = {
            left[index:index + 2]
            for index in range(max(len(left) - 1, 1))
        }
        right_bigrams = {
            right[index:index + 2]
            for index in range(max(len(right) - 1, 1))
        }
        union = left_bigrams | right_bigrams
        if not union:
            return 0.0
        return len(left_bigrams & right_bigrams) / len(union)

    @staticmethod
    def _longest_shared_span(left: str, right: str) -> int:
        if not left or not right:
            return 0
        return SequenceMatcher(a=left, b=right).find_longest_match().size

    @staticmethod
    def _pick_round_action(
        *,
        findings: list[AuditFinding],
        metrics: dict[str, float],
    ) -> str:
        if any(item.severity == "high" for item in findings):
            return "hold_termination"
        if findings:
            return "tighten_next_prompt"
        if metrics.get("novelty", 0.0) >= 0.82 and metrics.get("focus", 0.0) >= 0.5:
            return "end_early"
        return "none"

    @staticmethod
    def _summarize_round(
        *,
        findings: list[AuditFinding],
        metrics: dict[str, float],
        artifact: dict[str, Any],
    ) -> str:
        summary = artifact.get("summary") or {}
        current_dispute = summary.get("current_dispute") or (
            "\u8fd9\u4e00\u8f6e\u7684\u6838\u5fc3\u4e89\u70b9\u8fd8\u4e0d\u591f\u6e05\u6670"
        )
        if findings:
            return (
                f"{current_dispute}\uff1b"
                "\u4f46\u53d1\u8a00\u51fa\u73b0\u4e86\u540c\u70b9\u91cd\u590d\uff0c"
                f"\u65b0\u4fe1\u606f\u589e\u91cf\u53ea\u6709 {metrics.get('novelty', 0.0):.2f}\u3002"
            )
        return (
            f"{current_dispute}\uff1b"
            f"\u672c\u8f6e\u4ecd\u6709\u63a8\u8fdb\uff0c\u65b0\u9c9c\u5ea6 {metrics.get('novelty', 0.0):.2f}\u3002"
        )

    @staticmethod
    def _result(
        *,
        stage: str,
        status: str,
        summary: str,
        findings: list[AuditFinding],
        metrics: dict[str, float],
        recommended_action: str,
    ) -> AuditResult:
        return AuditResult(
            audit_id=str(uuid4()),
            stage=stage,
            status=status,
            summary=summary,
            findings=findings,
            metrics=metrics,
            recommended_action=recommended_action,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
