"""Semantic refinement for portrait-facing elicitation outcomes."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from app.models.elicitation import (
    DepthEvaluation,
    DilemmaLayer,
    ElicitationOutcome,
    EmotionEntry,
    InnerVoice,
    Tension,
    intensities_are_flat,
    normalize_comparable_text,
    texts_meaningfully_different,
    voice_name_is_label,
)
from app.services.language_guard import (
    chinese_system_prompt,
    find_low_chinese_fields,
    has_sufficient_chinese,
    record_failure,
    record_retry,
)
from app.services.llm import generate as llm_generate

logger = logging.getLogger(__name__)

LlmFn = Callable[..., Awaitable[str]]
_RETRY_SUFFIX = (
    "\n\n上一次改写结果里仍有面向用户的说明字段出现了整段英文。"
    "请重新返回 JSON，并确保所有说明性字段使用中文，只保留必要的英文专有名词。"
)
_DEFAULT_CORE_CONCERN = "担心一旦走错，会让自己失去稳定感和对自己的信任。"
_DEFAULT_PROTECTIVE_INTENT = "想保护内在的安全感和对自己的基本信任。"
_VOICE_THOUGHT_MARKERS = ("担心", "害怕", "别", "不要", "不能", "万一", "我", "需要", "想保护", "得")
_ACTION_STYLE_MARKERS = (
    "去实习",
    "做项目",
    "投暑期实习",
    "投简历",
    "完成一个项目",
    "投入时间做项目",
    "不投入时间做项目",
)


def _parse_json(text: str) -> Optional[dict]:
    if not text or not text.strip():
        return None

    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(1).strip())
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    return None


@dataclass
class RefinementResult:
    outcome: ElicitationOutcome
    improved: bool
    score_before: int
    score_after: int


class PortraitLanguageRefiner:
    """Upgrade low-quality elicitation outcomes into portrait-ready language."""

    SEMANTIC_VERSION = "v2"

    def __init__(self, llm_fn: Optional[LlmFn] = None) -> None:
        self._llm = llm_fn or llm_generate

    def _voice_semantic_key(self, voice: InnerVoice) -> str:
        return normalize_comparable_text(f"{voice.core_concern}{voice.protective_intent}")

    def _count_repeated_texts(self, values: list[str]) -> int:
        counts: dict[str, int] = {}
        repeated = 0
        for value in values:
            normalized = normalize_comparable_text(value)
            if not normalized:
                continue
            counts[normalized] = counts.get(normalized, 0) + 1
        for count in counts.values():
            if count > 1:
                repeated += count
        return repeated

    def _has_repeated_voice_semantics(self, voices: list[InnerVoice]) -> bool:
        if len(voices) < 2:
            return False
        combined = self._count_repeated_texts([self._voice_semantic_key(voice) for voice in voices])
        concerns = self._count_repeated_texts([voice.core_concern for voice in voices])
        intents = self._count_repeated_texts([voice.protective_intent for voice in voices])
        default_concerns = sum(1 for voice in voices if voice.core_concern.strip() == _DEFAULT_CORE_CONCERN)
        default_intents = sum(1 for voice in voices if voice.protective_intent.strip() == _DEFAULT_PROTECTIVE_INTENT)
        return combined >= 2 or concerns >= 3 or intents >= 3 or default_concerns > 1 or default_intents > 1

    def _tension_pole_texts(self, tensions: list[Tension]) -> set[str]:
        values: set[str] = set()
        for tension in tensions:
            for value in (tension.pole_a, tension.pole_b):
                normalized = normalize_comparable_text(value)
                if normalized:
                    values.add(normalized)
        return values

    def _is_action_style_voice_name(self, name: str, tensions: list[Tension]) -> bool:
        text = name.strip()
        normalized = normalize_comparable_text(text)
        if not normalized:
            return False
        if normalized in self._tension_pole_texts(tensions):
            return True
        if any(marker in text for marker in _ACTION_STYLE_MARKERS):
            return not any(marker in text for marker in _VOICE_THOUGHT_MARKERS)
        return False

    def needs_refinement(self, outcome: ElicitationOutcome) -> bool:
        duplicate_layers = any(
            item.description.strip()
            and item.user_language.strip()
            and not texts_meaningfully_different(item.description, item.user_language)
            for item in outcome.dilemma_layers
        )
        all_surface = len(outcome.dilemma_layers) > 1 and all(
            (item.depth or "").strip().lower() == "surface"
            for item in outcome.dilemma_layers
        )
        label_names = any(voice_name_is_label(item.name) for item in outcome.inner_voices)
        collapsed_voice_fields = any(
            item.core_concern.strip()
            and item.protective_intent.strip()
            and not texts_meaningfully_different(item.core_concern, item.protective_intent)
            for item in outcome.inner_voices
        )
        flat_voice_intensities = intensities_are_flat([item.intensity for item in outcome.inner_voices])
        flat_emotion_intensities = intensities_are_flat([item.intensity for item in outcome.emotion_map])
        repeated_voice_semantics = self._has_repeated_voice_semantics(outcome.inner_voices)
        action_style_names = any(
            self._is_action_style_voice_name(item.name, outcome.core_tensions)
            for item in outcome.inner_voices
        )
        overgrown_voice_list = len(outcome.inner_voices) > 4 and repeated_voice_semantics
        surface_dump = len(outcome.dilemma_layers) > 3 and all(
            (item.depth or "").strip().lower() == "surface"
            for item in outcome.dilemma_layers
        )

        return any(
            (
                duplicate_layers,
                all_surface,
                label_names,
                collapsed_voice_fields,
                flat_voice_intensities,
                flat_emotion_intensities,
                repeated_voice_semantics,
                action_style_names,
                overgrown_voice_list,
                surface_dump,
            )
        )

    def detail_score(self, outcome: ElicitationOutcome) -> int:
        score = 0
        if outcome.core_dilemma.strip():
            score += 1
        if len(outcome.dilemma_layers) > 3 and all(
            (item.depth or "").strip().lower() == "surface"
            for item in outcome.dilemma_layers
        ):
            score -= len(outcome.dilemma_layers)

        non_surface_layers = 0
        for item in outcome.dilemma_layers:
            if item.description.strip():
                score += 1
            if item.user_language.strip():
                score += 1
            if texts_meaningfully_different(item.description, item.user_language):
                score += 3
            if (item.depth or "").strip().lower() in {"emotional", "existential"}:
                non_surface_layers += 1
        score += non_surface_layers

        for item in outcome.inner_voices:
            if item.name.strip():
                score += 1
            if not voice_name_is_label(item.name):
                score += 2
            if self._is_action_style_voice_name(item.name, outcome.core_tensions):
                score -= 3
            if item.core_concern.strip():
                score += 1
            if item.protective_intent.strip():
                score += 1
            if texts_meaningfully_different(item.core_concern, item.protective_intent):
                score += 3
            if abs(item.intensity - 0.5) > 1e-9:
                score += 1
        if len(outcome.inner_voices) > 1 and not intensities_are_flat([item.intensity for item in outcome.inner_voices]):
            score += 2
        repeated_voice_penalty = self._count_repeated_texts(
            [self._voice_semantic_key(item) for item in outcome.inner_voices]
        )
        score -= repeated_voice_penalty * 4
        if 2 <= len(outcome.inner_voices) <= 4 and not self._has_repeated_voice_semantics(outcome.inner_voices):
            score += 4
        if len(outcome.inner_voices) > 4:
            score -= len(outcome.inner_voices) - 4
        for item in outcome.inner_voices:
            if item.typical_phrases:
                score += 1

        for item in outcome.emotion_map:
            if item.context.strip():
                score += 1
            if abs(item.intensity - 0.5) > 1e-9:
                score += 1
        if len(outcome.emotion_map) > 1 and not intensities_are_flat([item.intensity for item in outcome.emotion_map]):
            score += 1

        return score

    def _heuristic_refinement(self, outcome: ElicitationOutcome, score_before: int) -> RefinementResult:
        candidate = self._post_process(ElicitationOutcome.from_dict(outcome.to_dict()))
        score_after = self.detail_score(candidate)
        improved = score_after > score_before
        return RefinementResult(
            outcome=candidate if improved else outcome,
            improved=improved,
            score_before=score_before,
            score_after=score_after,
        )

    async def refine(
        self,
        outcome: ElicitationOutcome,
        *,
        conversation_history: list[dict],
        depth_evaluations: list[DepthEvaluation],
    ) -> RefinementResult:
        score_before = self.detail_score(outcome)
        if not self.needs_refinement(outcome):
            return RefinementResult(outcome=outcome, improved=False, score_before=score_before, score_after=score_before)

        heuristic_result = self._heuristic_refinement(outcome, score_before)
        if not conversation_history:
            return heuristic_result

        prompt = self._build_prompt(outcome, conversation_history, depth_evaluations)
        system_prompt = chinese_system_prompt("只返回 JSON。")

        try:
            raw = await self._llm(
                prompt,
                system=system_prompt,
                temperature=0.2,
                max_tokens=1200,
            )
        except Exception:
            logger.exception("Portrait language refinement LLM call failed.")
            return heuristic_result

        payload = _parse_json(raw)
        if not payload:
            return heuristic_result

        candidate = self._merge_refinement(outcome, payload)
        candidate = self._post_process(candidate)
        candidate = await self._retry_for_language_if_needed(
            original_outcome=outcome,
            candidate=candidate,
            prompt=prompt,
            system_prompt=system_prompt,
        )

        score_after = self.detail_score(candidate)
        best_result = heuristic_result
        candidate_failures = len(self._language_failures(candidate))
        heuristic_failures = len(self._language_failures(heuristic_result.outcome))
        if candidate_failures < heuristic_failures or (
            candidate_failures == heuristic_failures and score_after >= best_result.score_after
        ):
            best_result = RefinementResult(
                outcome=candidate,
                improved=True,
                score_before=score_before,
                score_after=score_after,
            )
        return RefinementResult(
            outcome=best_result.outcome,
            improved=best_result.improved,
            score_before=score_before,
            score_after=best_result.score_after,
        )

    async def _retry_for_language_if_needed(
        self,
        *,
        original_outcome: ElicitationOutcome,
        candidate: ElicitationOutcome,
        prompt: str,
        system_prompt: str,
    ) -> ElicitationOutcome:
        initial_failures = self._language_failures(candidate)
        if not initial_failures:
            return candidate

        record_retry()
        try:
            retry_raw = await self._llm(
                prompt + _RETRY_SUFFIX,
                system=system_prompt,
                temperature=0.2,
                max_tokens=1200,
            )
        except Exception:
            logger.warning(
                "language_guard_warning: portrait refinement retry failed for %s",
                ", ".join(initial_failures),
            )
            record_failure()
            return candidate

        retry_payload = _parse_json(retry_raw)
        if not retry_payload:
            logger.warning(
                "language_guard_warning: portrait refinement retry returned invalid JSON for %s",
                ", ".join(initial_failures),
            )
            record_failure()
            return candidate

        retry_candidate = self._post_process(self._merge_refinement(original_outcome, retry_payload))
        retry_failures = self._language_failures(retry_candidate)
        if not retry_failures:
            return retry_candidate

        best_candidate = retry_candidate if len(retry_failures) < len(initial_failures) else candidate
        logger.warning(
            "language_guard_warning: portrait refinement fields still English-heavy after retry; keeping best candidate (%s)",
            ", ".join(retry_failures),
        )
        record_failure()
        return best_candidate

    def _build_prompt(
        self,
        outcome: ElicitationOutcome,
        conversation_history: list[dict],
        depth_evaluations: list[DepthEvaluation],
    ) -> str:
        history_lines = []
        for message in conversation_history:
            role = "user" if message.get("role") == "user" else "assistant"
            history_lines.append(f"{role}: {message.get('content', '')}")

        evaluation_lines = []
        for index, evaluation in enumerate(depth_evaluations, start=1):
            evaluation_lines.append(
                f"{index}. depth={evaluation.depth_score:.2f}, "
                f"layer={evaluation.depth_layer}, "
                f"readiness={evaluation.readiness_score:.2f}, "
                f"action={evaluation.recommended_action}, "
                f"strategy={evaluation.strategy_hint}"
            )

        return f"""Rewrite this outcome into portrait-friendly language.

Original outcome:
{json.dumps(outcome.to_dict(), ensure_ascii=False, indent=2)}

Conversation history:
{chr(10).join(history_lines) or "(empty)"}

Depth evaluations:
{chr(10).join(evaluation_lines) or "(none)"}

Rules:
- Keep the same overall dilemma.
- `dilemma_layers[].description` must be an analytic summary sentence, not a copy of user wording.
- `dilemma_layers[].user_language` should sound like the user's own phrasing.
- Inner voice names must sound like actual inner thoughts, not abstract labels like "...者" or "...analyst".
- `core_concern` must describe the concrete feared outcome.
- `protective_intent` must describe the deeper thing the voice wants to protect.
- Voice intensities must not all be 0.5.
- Emotion intensities must not all be 0.5.
- If inner_voices contain repeated core_concern/protective_intent, compress them into 2-4 distinct voices.
- Do not use action names or external options such as "去实习", "做项目", "先用一个月快速完成一个项目" as inner voice names.
- Each voice must have a different core_concern and protective_intent from the other voices.
- If there are more than 3 surface dilemma layers, summarize them into at most 3 layers: reality constraint, emotional cost, and deeper standard/value when evidence supports it.
- Return only the keys you are changing: dilemma_layers, inner_voices, emotion_map.
"""

    def _merge_refinement(self, outcome: ElicitationOutcome, payload: dict) -> ElicitationOutcome:
        base = outcome.to_dict()

        for key in ("dilemma_layers", "inner_voices", "emotion_map"):
            value = payload.get(key)
            if isinstance(value, list) and value:
                base[key] = value

        return ElicitationOutcome.from_dict(base)

    def _language_failures(self, outcome: ElicitationOutcome) -> list[str]:
        named_values: list[tuple[str, str]] = []

        for index, layer in enumerate(outcome.dilemma_layers):
            named_values.append((f"dilemma_layers[{index}].description", layer.description))

        for index, voice in enumerate(outcome.inner_voices):
            named_values.extend(
                [
                    (f"inner_voices[{index}].name", voice.name),
                    (f"inner_voices[{index}].core_concern", voice.core_concern),
                    (f"inner_voices[{index}].protective_intent", voice.protective_intent),
                ]
            )

        for index, emotion in enumerate(outcome.emotion_map):
            named_values.append((f"emotion_map[{index}].context", emotion.context))

        return find_low_chinese_fields(named_values)

    def _post_process(self, outcome: ElicitationOutcome) -> ElicitationOutcome:
        outcome.dilemma_layers = [
            self._normalize_dilemma_layer(item, index=index)
            for index, item in enumerate(outcome.dilemma_layers)
        ]
        outcome.inner_voices = self._normalize_inner_voices(outcome.inner_voices)
        outcome.emotion_map = self._normalize_emotions(outcome.emotion_map)
        return outcome

    def _normalize_dilemma_layer(self, layer: DilemmaLayer, *, index: int = 0) -> DilemmaLayer:
        if layer.description.strip() and layer.user_language.strip() and not texts_meaningfully_different(
            layer.description,
            layer.user_language,
        ):
            layer.description = self._fallback_layer_description(layer, index=index)
        return layer

    def _normalize_inner_voices(self, voices: list[InnerVoice]) -> list[InnerVoice]:
        normalized: list[InnerVoice] = []
        for voice in voices:
            name = voice.name.strip()
            if voice_name_is_label(name) or not name:
                name = self._fallback_voice_name(voice)

            core_concern = voice.core_concern.strip()
            protective_intent = voice.protective_intent.strip()
            if not protective_intent or (core_concern and not texts_meaningfully_different(core_concern, protective_intent)):
                protective_intent = self._fallback_protective_intent(voice)
            if self._core_concern_needs_rewrite(core_concern, protective_intent):
                core_concern = self._fallback_core_concern(voice)

            normalized.append(
                InnerVoice(
                    name=name,
                    core_concern=core_concern,
                    protective_intent=protective_intent,
                    intensity=voice.intensity,
                    fear=voice.fear,
                    language_style=voice.language_style,
                    typical_phrases=list(voice.typical_phrases),
                    relationship_to_others=list(voice.relationship_to_others),
                )
            )

        if intensities_are_flat([item.intensity for item in normalized]):
            normalized = self._spread_voice_intensities(normalized)

        return normalized

    def _normalize_emotions(self, emotions: list[EmotionEntry]) -> list[EmotionEntry]:
        if intensities_are_flat([item.intensity for item in emotions]):
            template = self._spread_values(len(emotions), peak=0.74, floor=0.42)
            return [
                EmotionEntry(
                    emotion=item.emotion,
                    context=item.context,
                    intensity=template[index],
                )
                for index, item in enumerate(emotions)
            ]
        return emotions

    def _spread_voice_intensities(self, voices: list[InnerVoice]) -> list[InnerVoice]:
        template = self._spread_values(len(voices), peak=0.78, floor=0.44)
        return [
            InnerVoice(
                name=item.name,
                core_concern=item.core_concern,
                protective_intent=item.protective_intent,
                intensity=template[index],
                fear=item.fear,
                language_style=item.language_style,
                typical_phrases=list(item.typical_phrases),
                relationship_to_others=list(item.relationship_to_others),
            )
            for index, item in enumerate(voices)
        ]

    def _spread_values(self, count: int, *, peak: float, floor: float) -> list[float]:
        if count <= 0:
            return []
        if count == 1:
            return [peak]
        step = (peak - floor) / max(count - 1, 1)
        return [round(peak - step * index, 2) for index in range(count)]

    def _fallback_layer_description(self, layer: DilemmaLayer, index: int = 0) -> str:
        depth = (layer.depth or "").strip().lower()
        fallback_pools = {
            "surface": [
                "表层上，这是现实与渴望之间的拉扯。",
                "表面看，这是稳妥路径和真实向往之间的摇摆。",
                "表层上，这是现实与渴望之间的拉扯。",
            ],
            "emotional": [
                "再往里一层，真正拉扯的是做错选择后要承受的失落、自责与后悔。",
                "再往里一层，真正拉扯的是做错选择后要承受的失落、自责与后悔。",
                "情绪上，这是对失落与后悔的担心。",
            ],
            "existential": [
                "最深处，这道题关乎你要成为什么样的人，以及想把人生带向哪里。",
                "最深处，这道题关乎你要成为什么样的人，以及想把人生带向哪里。",
                "最深处，这道题关乎你要成为什么样的人，以及想把人生带向哪里。",
            ],
        }
        pool = fallback_pools.get(depth, fallback_pools["surface"])
        return pool[index % len(pool)]

    def _voice_anchor(self, voice: InnerVoice) -> str:
        for phrase in voice.typical_phrases:
            text = str(phrase or "").strip()
            if text:
                return text
        name = voice.name.strip()
        return name if name and not voice_name_is_label(name) else ""

    def _fallback_voice_name(self, voice: InnerVoice) -> str:
        anchor = self._voice_anchor(voice)
        if anchor:
            return anchor if len(anchor) <= 16 else "我得先把这件事想清楚"
        return "我得先保护好自己"

    def _fallback_core_concern(self, voice: InnerVoice) -> str:
        anchor = self._voice_anchor(voice)
        if anchor:
            return f"担心如果忽视「{anchor}」，会让自己更被动、更难受。"
        return "担心一旦走错，会让自己失去稳定感和对自己的信任。"

    def _fallback_protective_intent(self, voice: InnerVoice) -> str:
        anchor = self._voice_anchor(voice)
        if anchor:
            return f"想守住「{anchor}」背后那份对自己的在意。"
        return "想保护内在的安全感和对自己的基本信任。"

    def _core_concern_needs_rewrite(self, concern: str, protective_intent: str) -> bool:
        stripped = concern.strip()
        if not stripped:
            return True
        if not texts_meaningfully_different(stripped, protective_intent):
            return True
        if not has_sufficient_chinese(stripped):
            return True
        return False

