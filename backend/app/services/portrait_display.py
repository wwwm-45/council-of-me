"""User-facing display model for Phase 2 portrait review."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from app.models.elicitation import DilemmaLayer, ElicitationOutcome, InnerVoice, normalize_comparable_text
from app.services.portrait_composer import Portrait


SHORT_OR_PROCESS_REPLIES = {
    "可以",
    "当然",
    "嗯",
    "好的",
    "好",
    "我会的",
    "继续",
    "这样可以",
    "这是一个可行的方案",
}

PROCESS_REPLY_MARKERS = (
    "下一阶段",
    "进入下一步",
    "继续下一步",
    "继续聊",
    "开始辩论",
    "生成画像",
    "确认画像",
)

GENERIC_ENGLISH_LABEL_PREFIXES = ("你的", "我的", "这个", "一个", "这位")

LAYER_TITLES = {
    "situation": "表层处境",
    "emotion": "情绪担心",
    "value": "更深在意",
}

EMOTION_MARKERS = ("担心", "害怕", "怕", "焦虑", "后悔", "失望", "否定", "不安", "压力")
VALUE_MARKERS = ("价值", "成为", "人生", "自我", "关系", "自由", "稳定", "成长", "归属", "意义", "值得")

_LATIN_WORD_RE = re.compile(r"[A-Za-z]{3,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_THIN_FOCUS_WARNING = "现在的信息还不足以形成清晰的抉择焦点。"
_TENSION_WHY_HARD = "这两端都在保护你在意的东西，所以它不是简单的利弊题。"
_VOICE_WHY_HARD = "这两个声音都不是在捣乱，它们分别在替你守住不同的需要。"
_INNER_ROLE_FALLBACK_REASON = "这个角色会承接对应的内在声音。"
_SUPPLEMENTAL_ROLE_FALLBACK_REASON = "这个角色会补充一个有用的观察视角。"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _is_substantive(value: str) -> bool:
    text = _clean(value)
    if not text or _is_process_reply(text):
        return False
    return len(normalize_comparable_text(text)) >= 8


def _is_process_reply(value: str) -> bool:
    text = _clean(value)
    if text in SHORT_OR_PROCESS_REPLIES:
        return True
    if not any(marker in text for marker in PROCESS_REPLY_MARKERS):
        return False
    return len(normalize_comparable_text(text)) <= 10


def _has_cjk(value: str) -> bool:
    return bool(_CJK_RE.search(value))


def _is_generic_english_label(value: str) -> bool:
    text = _clean(value)
    latin_words = _LATIN_WORD_RE.findall(text)
    if not latin_words:
        return False

    for prefix in GENERIC_ENGLISH_LABEL_PREFIXES:
        if not text.startswith(prefix):
            continue
        remainder = text[len(prefix) :].strip()
        without_words = _LATIN_WORD_RE.sub("", remainder).strip()
        return bool(remainder) and len(_CJK_RE.findall(without_words)) == 0

    return False


def _safe_chinese(value: str, fallback: str = "") -> str:
    text = _clean(value)
    if (
        not text
        or not _has_cjk(text)
        or _is_generic_english_label(text)
    ):
        return fallback
    return text


def _safe_substantive(value: str) -> str:
    text = _safe_chinese(value)
    return text if _is_substantive(text) else ""


def _layer_type(layer: DilemmaLayer) -> str:
    depth = _clean(layer.depth).lower()
    text = f"{layer.description} {layer.user_language}"
    if depth == "existential" or any(marker in text for marker in VALUE_MARKERS):
        return "value"
    if depth == "emotional" or any(marker in text for marker in EMOTION_MARKERS):
        return "emotion"
    return "situation"


def _short_label(value: str) -> str:
    text = _clean(value)
    if not text:
        return "这一端"

    for separator in ("，", "。", "；", ",", ".", ";"):
        index = text.find(separator)
        if index >= 0:
            text = text[:index].strip()
            break

    return text[:16] or "这一端"


@dataclass
class PortraitDisplayComposer:
    """Compose a concise, Chinese-only payload for the portrait review UI."""

    def compose(
        self,
        outcome: ElicitationOutcome,
        portrait: Portrait,
        quality: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        decision_focus = self._decision_focus(outcome)
        layers = self._layers(outcome)
        voices = self._voices(outcome)
        warnings = self._quality_warnings(quality)

        if decision_focus is None:
            warnings.insert(0, _THIN_FOCUS_WARNING)

        status = "ready" if decision_focus is not None and len(voices) >= 2 else "thin"
        return {
            "headline": _safe_chinese(outcome.core_dilemma, "你的内心画像"),
            "decision_focus": decision_focus,
            "layers": layers,
            "voices": voices,
            "council_preview": self._council_preview(portrait, voices),
            "quality": {"status": status, "warnings": self._dedupe_texts(warnings)},
        }

    def _decision_focus(self, outcome: ElicitationOutcome) -> dict[str, Any] | None:
        for tension in outcome.core_tensions:
            left = _safe_chinese(tension.pole_a)
            right = _safe_chinese(tension.pole_b)
            if not left or not right:
                continue
            if normalize_comparable_text(left) == normalize_comparable_text(right):
                continue

            evidence = _safe_chinese(tension.user_evidence)
            if evidence and not _is_substantive(evidence):
                evidence = ""
            left_label = _short_label(left)
            right_label = _short_label(right)
            return {
                "summary": f"你卡在：{left_label}，还是{right_label}。",
                "option_a": {
                    "label": left_label,
                    "description": left,
                    "evidence": evidence,
                },
                "option_b": {
                    "label": right_label,
                    "description": right,
                    "evidence": evidence,
                },
                "why_hard": _TENSION_WHY_HARD,
            }

        candidates: list[InnerVoice] = []
        for voice in outcome.inner_voices:
            name = _safe_chinese(voice.name)
            concern = _safe_substantive(voice.protective_intent) or _safe_substantive(voice.core_concern)
            if name and concern:
                candidates.append(voice)
            if len(candidates) >= 2:
                break

        if len(candidates) < 2:
            return None

        first, second = candidates[:2]
        first_name = _short_label(_safe_chinese(first.name))
        second_name = _short_label(_safe_chinese(second.name))
        first_description = _safe_substantive(first.protective_intent) or _safe_substantive(first.core_concern)
        second_description = _safe_substantive(second.protective_intent) or _safe_substantive(second.core_concern)
        return {
            "summary": f"你卡在：{first_name}，还是{second_name}。",
            "option_a": {
                "label": first_name,
                "description": first_description,
                "evidence": "",
            },
            "option_b": {
                "label": second_name,
                "description": second_description,
                "evidence": "",
            },
            "why_hard": _VOICE_WHY_HARD,
        }

    def _layers(self, outcome: ElicitationOutcome) -> list[dict[str, Any]]:
        by_type: dict[str, dict[str, Any]] = {}
        seen_descriptions: set[str] = set()

        for layer in outcome.dilemma_layers:
            description = _safe_chinese(layer.description)
            evidence = _safe_chinese(layer.user_language)
            if not _is_substantive(description) or not _is_substantive(evidence):
                continue

            normalized = normalize_comparable_text(description)
            layer_type = _layer_type(layer)
            if not normalized or normalized in seen_descriptions or layer_type in by_type:
                continue

            by_type[layer_type] = {
                "type": layer_type,
                "title": LAYER_TITLES[layer_type],
                "description": description,
                "evidence": evidence,
            }
            seen_descriptions.add(normalized)

        return [by_type[layer_type] for layer_type in ("situation", "emotion", "value") if layer_type in by_type]

    def _voices(self, outcome: ElicitationOutcome) -> list[dict[str, Any]]:
        voices: list[dict[str, Any]] = []
        seen: set[str] = set()

        for voice in outcome.inner_voices:
            name = _safe_chinese(voice.name)
            concern = _safe_substantive(voice.core_concern)
            protective_intent = _safe_substantive(voice.protective_intent)
            if not name or not (concern or protective_intent):
                continue

            normalized = normalize_comparable_text(f"{name}{concern}{protective_intent}")
            if not normalized or normalized in seen:
                continue

            evidence = ""
            for phrase in voice.typical_phrases:
                candidate = _safe_chinese(phrase)
                if _is_substantive(candidate):
                    evidence = candidate
                    break

            voices.append(
                {
                    "name": name,
                    "concern": concern,
                    "protective_intent": protective_intent,
                    "evidence": evidence,
                    "intensity": voice.intensity,
                }
            )
            seen.add(normalized)
            if len(voices) >= 4:
                break

        return voices

    def _council_preview(self, portrait: Portrait, display_voices: list[dict[str, Any]]) -> dict[str, Any]:
        voice_names = {voice["name"] for voice in display_voices}
        roles: list[dict[str, str]] = []

        for assignment in portrait.agent_assignments:
            raw_voice_name = _clean(assignment.voice_name)
            voice_name = _safe_chinese(assignment.voice_name)
            if raw_voice_name and voice_name not in voice_names:
                continue
            is_inner_voice = bool(voice_name and voice_name in voice_names)
            source = "inner_voice" if is_inner_voice else "supplemental"
            reason = _safe_chinese(assignment.mapping_reason)
            if not reason:
                reason = _INNER_ROLE_FALLBACK_REASON if is_inner_voice else _SUPPLEMENTAL_ROLE_FALLBACK_REASON

            roles.append(
                {
                    "display_name": _safe_chinese(assignment.display_name, "议会成员"),
                    "represents": voice_name if is_inner_voice else "补充视角",
                    "source": source,
                    "reason": reason,
                }
            )

        return {
            "summary": _safe_chinese(portrait.complexity.narrative, "议会会从多个角度承接这组矛盾。"),
            "level": portrait.complexity.level,
            "agent_count": portrait.complexity.agent_count,
            "roles": roles,
        }

    def _quality_warnings(self, quality: dict[str, Any] | None) -> list[str]:
        if not quality:
            return []

        warnings: list[str] = []
        issues = quality.get("issues") if isinstance(quality, dict) else None
        if not isinstance(issues, list):
            return warnings

        for issue in issues:
            if not isinstance(issue, dict):
                continue
            for key in ("message", "suggestion"):
                text = _safe_chinese(issue.get(key, ""))
                if text:
                    warnings.append(text)
        return warnings

    def _dedupe_texts(self, values: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = _clean(value)
            normalized = normalize_comparable_text(text)
            if not text or not normalized or normalized in seen:
                continue
            deduped.append(text)
            seen.add(normalized)
        return deduped
