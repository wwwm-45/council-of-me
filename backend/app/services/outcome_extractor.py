"""LLM-based final outcome extraction for Phase 1 elicitation."""

import json
import logging
import re
from typing import Any, Awaitable, Callable, Optional

from app.models.elicitation import (
    DepthEvaluation,
    DilemmaLayer,
    ElicitationOutcome,
    EmotionEntry,
    Tension,
    TensionCard,
    best_outcome_context_text,
    dilemma_layer_semantic_key,
    normalize_comparable_text,
)
from app.services.language_guard import (
    chinese_system_prompt,
    find_low_chinese_fields,
    record_failure,
    record_retry,
)
from app.services.elicitation_control import filter_process_turns
from app.services.llm import generate as llm_generate
from app.services.portrait_language_refiner import PortraitLanguageRefiner

logger = logging.getLogger(__name__)

LlmFn = Callable[..., Awaitable[str]]


def is_usable_tension_card(card: TensionCard) -> bool:
    if card.status not in {"probed", "layered", "saturated"}:
        return False
    if card.kind == "bipolar":
        return bool(card.pole_a and card.pole_b)
    if card.kind == "undecided":
        return True
    if card.kind == "tangled":
        return len(card.threads) >= 2
    return False


def _card_to_tension_dicts(card: TensionCard) -> list[dict]:
    if card.kind == "bipolar" and card.pole_a and card.pole_b:
        return [{"pole_a": card.pole_a, "pole_b": card.pole_b, "user_evidence": card.raw_quote}]
    if card.kind == "undecided":
        if len(card.candidates) >= 2:
            return [{"pole_a": card.candidates[0], "pole_b": card.candidates[1], "user_evidence": card.raw_quote}]
        if len(card.candidates) == 1:
            return [{"pole_a": card.candidates[0], "pole_b": "不" + card.candidates[0], "user_evidence": card.raw_quote}]
        return [{"pole_a": "做这件事", "pole_b": "不做这件事", "user_evidence": card.raw_quote}]
    if card.kind == "tangled" and len(card.threads) >= 2:
        return [
            {"pole_a": card.threads[0], "pole_b": thread, "user_evidence": card.raw_quote}
            for thread in card.threads[1:3]
        ]
    return []


def core_tensions_from_cards(cards: list[TensionCard]) -> list[Tension]:
    tensions: list[Tension] = []
    seen: set[tuple[str, str]] = set()
    for card in cards:
        if not is_usable_tension_card(card):
            continue
        for raw in _card_to_tension_dicts(card):
            pole_a = str(raw.get("pole_a") or "").strip()
            pole_b = str(raw.get("pole_b") or "").strip()
            if not pole_a or not pole_b:
                continue
            key = (pole_a, pole_b)
            if key in seen:
                continue
            seen.add(key)
            tensions.append(Tension(pole_a=pole_a, pole_b=pole_b, user_evidence=str(raw.get("user_evidence") or "").strip()))
    return tensions


_RETRY_SUFFIX = (
    "\n\n上一次输出里有面向用户的说明字段出现了整段英文。"
    "请重新生成完整 JSON，确保所有说明性字段都使用中文，只保留必要的英文专有名词。"
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


def copy_jsonable(payload: dict) -> dict:
    return json.loads(json.dumps(payload, ensure_ascii=False))


_VOICE_DUP_OVERLAP = 0.7


def _voice_body_signature(voice: dict) -> str:
    return normalize_comparable_text(
        " ".join(str(voice.get(k) or "") for k in ("core_concern", "protective_intent", "fear", "language_style"))
    )


def _bigrams(text: str) -> set[str]:
    if len(text) >= 2:
        return {text[i : i + 2] for i in range(len(text) - 1)}
    return {text} if text else set()


def _token_overlap(left: str, right: str) -> float:
    """Jaccard over char-bigrams (CJK-friendly, self-contained)."""
    a, b = _bigrams(left), _bigrams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class OutcomeExtractor:
    """Generate a structured elicitation outcome from the full conversation."""

    def __init__(
        self,
        llm_fn: Optional[LlmFn] = None,
        refiner: Optional[PortraitLanguageRefiner] = None,
    ) -> None:
        self._llm = llm_fn or llm_generate
        self._refiner = refiner or PortraitLanguageRefiner(llm_fn=self._llm)
        self._reextract_attempts = 0
        self._reextract_successes = 0
        self._tier2_falls = 0

    def metrics(self) -> dict[str, int]:
        return {
            "reextract_attempts": self._reextract_attempts,
            "reextract_successes": self._reextract_successes,
            "tier2_falls": self._tier2_falls,
        }

    async def extract(
        self,
        conversation_history: list[dict],
        depth_evaluations: list[DepthEvaluation],
        tension_cards: list[Any] | None = None,
    ) -> ElicitationOutcome:
        evidence_history = filter_process_turns(conversation_history)
        available_cards = self._normalize_tension_cards(tension_cards or [])
        usable_cards = self._usable_cards(available_cards)
        if usable_cards:
            return await self._extract_from_cards(
                evidence_history,
                depth_evaluations,
                self._outcome_cards(available_cards),
            )

        prompt = self._build_prompt(evidence_history, depth_evaluations)
        system_prompt = chinese_system_prompt("只返回 JSON。")

        try:
            raw = await self._llm(
                prompt,
                system=system_prompt,
                temperature=0.3,
                max_tokens=1024,
            )
        except Exception:
            logger.exception("Outcome extractor LLM call failed.")
            return self._fallback_outcome(evidence_history, depth_evaluations)

        parsed = _parse_json(raw)
        if not parsed:
            return self._fallback_outcome(evidence_history, depth_evaluations)

        issues = self._validate_voice_distinctness(parsed)
        if issues:
            self._reextract_attempts += 1
            repaired = await self._reextract_with_distinctness_hint(parsed, issues)
            remaining = self._validate_voice_distinctness(repaired)
            if not remaining:
                parsed = repaired
                self._reextract_successes += 1
            else:
                self._tier2_falls += 1

        parsed = await self._apply_voice_dedup(parsed)

        outcome = self._outcome_from_payload(parsed, depth_evaluations)
        if not outcome.core_dilemma:
            return self._fallback_outcome(evidence_history, depth_evaluations)
        outcome = await self._retry_for_language_if_needed(
            outcome=outcome,
            prompt=prompt,
            system_prompt=system_prompt,
            depth_evaluations=depth_evaluations,
        )

        refinement = await self._refiner.refine(
            outcome,
            conversation_history=evidence_history,
            depth_evaluations=depth_evaluations,
        )
        return refinement.outcome

    def _normalize_tension_cards(self, tension_cards: list[Any]) -> list[TensionCard]:
        normalized: list[TensionCard] = []
        for item in tension_cards:
            if isinstance(item, TensionCard):
                normalized.append(item)
            elif isinstance(item, dict):
                normalized.append(TensionCard.from_dict(item))
        return normalized

    async def _extract_from_cards(
        self,
        conversation_history: list[dict],
        depth_evaluations: list[DepthEvaluation],
        tension_cards: list[TensionCard],
    ) -> ElicitationOutcome:
        base_payload = self._card_payload(tension_cards, depth_evaluations)
        prompt = self._build_card_enrichment_prompt(conversation_history, depth_evaluations, tension_cards)
        system_prompt = chinese_system_prompt("只返回 JSON。")
        parsed: dict | None = None

        try:
            raw = await self._llm(
                prompt,
                system=system_prompt,
                temperature=0.3,
                max_tokens=1024,
            )
            parsed = _parse_json(raw)
        except Exception:
            logger.exception("Outcome extractor card enrichment LLM call failed.")

        outcome = self._outcome_from_payload(
            self._merge_card_enrichment(base_payload, parsed or {}),
            depth_evaluations,
        )
        outcome = await self._retry_card_language_if_needed(
            outcome=outcome,
            base_payload=base_payload,
            prompt=prompt,
            system_prompt=system_prompt,
            depth_evaluations=depth_evaluations,
        )
        refinement = await self._refiner.refine(
            outcome,
            conversation_history=conversation_history,
            depth_evaluations=depth_evaluations,
        )
        return refinement.outcome

    def _build_prompt(
        self,
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

        return f"""Summarize this counseling conversation into a structured JSON object.

Conversation history:
{chr(10).join(history_lines) or "(empty)"}

Depth evaluations:
{chr(10).join(evaluation_lines) or "(none)"}

Return JSON with keys:
- core_dilemma
- dilemma_layers: each item must include description (analytic summary, not the same wording as the quote), depth, user_language (short original-language wording from the user)
- inner_voices: each item must include name, core_concern, protective_intent, fear, language_style, typical_phrases, relationship_to_others, intensity (0-1). For layer 1 or 2 material, rich fields may be empty when evidence is weak; for layer 3 material, preserve rich fields whenever the conversation provides evidence.
- core_tensions: each item must include pole_a, pole_b, user_evidence
- emotion_map: each item must include emotion, context, intensity (0-1)
- value_conflicts
- stakeholders
- conversation_depth
- max_depth_reached
- total_rounds
- depth_trajectory
- key_expressions
- closing_readiness
"""

    def _build_card_enrichment_prompt(
        self,
        conversation_history: list[dict],
        depth_evaluations: list[DepthEvaluation],
        tension_cards: list[TensionCard],
    ) -> str:
        history_lines = []
        for message in conversation_history:
            role = "user" if message.get("role") == "user" else "assistant"
            history_lines.append(f"{role}: {message.get('content', '')}")

        cards_payload = [card.to_dict() for card in tension_cards]
        evaluation_lines = [
            f"{index}. depth={evaluation.depth_score:.2f}, layer={evaluation.depth_layer}, action={evaluation.recommended_action}"
            for index, evaluation in enumerate(depth_evaluations, start=1)
        ]
        return f"""Enrich a card-derived elicitation outcome by extracting true inner voices.

The tension cards describe external decision poles, actions, facts, or reality constraints.
Do not turn pole_a or pole_b directly into inner voice names.
Create 2-4 inner voices only when the conversation supports them.
Each inner voice must represent an internal stance, worry, protective intention, or self-directed sentence.
Voice names should sound like thoughts that could arise in the user's mind, for example "先看清代价", "不能再绕开那句话", or "需要保留退路".
Do not create one voice per tension card.
Do not preserve the same order as the cards.

Conversation history:
{chr(10).join(history_lines) or "(empty)"}

Depth evaluations:
{chr(10).join(evaluation_lines) or "(none)"}

Tension cards:
{json.dumps(cards_payload, ensure_ascii=False)}

Return JSON with optional keys:
- inner_voices: 2-4 items, each with name, core_concern, protective_intent, fear, language_style, typical_phrases, relationship_to_others, intensity
- emotion_map
- value_conflicts
- stakeholders
- key_expressions
"""

    def _usable_cards(self, tension_cards: list[TensionCard]) -> list[TensionCard]:
        return [card for card in tension_cards if is_usable_tension_card(card)]

    def _outcome_cards(self, tension_cards: list[TensionCard]) -> list[TensionCard]:
        return self._usable_cards(tension_cards)

    def _card_payload(
        self,
        tension_cards: list[TensionCard],
        depth_evaluations: list[DepthEvaluation],
    ) -> dict:
        trajectory = [evaluation.depth_score for evaluation in depth_evaluations]
        final_depth = trajectory[-1] if trajectory else 0.0
        max_depth = max(trajectory) if trajectory else 0.0
        final_readiness = depth_evaluations[-1].readiness_score if depth_evaluations else 0.0

        dilemma_layers = []
        inner_voices = []
        core_tensions = []
        key_expressions = []
        for card in tension_cards:
            key_expressions.append(card.raw_quote)
            if card.layers:
                for layer in card.layers:
                    dilemma_layers.append(
                        {
                            "description": layer.description,
                            "depth": "surface",
                            "user_language": layer.user_language,
                        }
                    )
                    key_expressions.append(layer.user_language)
            else:
                dilemma_layers.append(
                    {
                        "description": card.raw_quote,
                        "depth": "surface",
                        "user_language": card.raw_quote,
                    }
                )

            core_tensions.extend(self._card_to_core_tensions(card))

        return {
            "core_dilemma": "；".join(card.raw_quote for card in tension_cards),
            "dilemma_layers": dilemma_layers,
            "inner_voices": inner_voices,
            "core_tensions": core_tensions,
            "emotion_map": [],
            "value_conflicts": [],
            "stakeholders": [],
            "conversation_depth": final_depth,
            "max_depth_reached": max_depth,
            "total_rounds": len([evaluation for evaluation in depth_evaluations]),
            "depth_trajectory": trajectory,
            "key_expressions": self._dedupe_texts(key_expressions),
            "closing_readiness": final_readiness,
        }

    def _card_to_core_tensions(self, card: TensionCard) -> list[dict]:
        return _card_to_tension_dicts(card)

    def _merge_card_enrichment(self, base_payload: dict, enrichment: dict) -> dict:
        merged = copy_jsonable(base_payload)
        extra_voices = enrichment.get("inner_voices") if isinstance(enrichment, dict) else None
        valid_voices = self._valid_enrichment_voices(extra_voices)
        if valid_voices:
            merged["inner_voices"] = valid_voices
        elif not merged.get("inner_voices"):
            merged["inner_voices"] = self._fallback_card_voices(merged)

        for key in ("emotion_map", "value_conflicts", "stakeholders"):
            value = enrichment.get(key) if isinstance(enrichment, dict) else None
            if isinstance(value, list) and value:
                merged[key] = value
        key_expressions = enrichment.get("key_expressions") if isinstance(enrichment, dict) else None
        if isinstance(key_expressions, list) and key_expressions:
            merged["key_expressions"] = self._dedupe_texts((merged.get("key_expressions") or []) + key_expressions)

        return merged

    def _valid_enrichment_voices(self, voices: object) -> list[dict]:
        if not isinstance(voices, list):
            return []
        valid = []
        for voice in voices:
            if not isinstance(voice, dict):
                continue
            if all(str(voice.get(key) or "").strip() for key in ("name", "core_concern", "protective_intent")):
                valid.append(voice)
        if 2 <= len(valid) <= 4:
            return valid
        return []

    def _validate_voice_distinctness(self, payload: dict) -> list[str]:
        """Return human-readable failure reasons. Empty list means OK."""
        issues: list[str] = []
        voices = payload.get("inner_voices") or []
        if not isinstance(voices, list):
            return issues
        for index, voice in enumerate(voices):
            if not isinstance(voice, dict):
                continue
            core = str(voice.get("core_concern") or "").strip()
            protective = str(voice.get("protective_intent") or "").strip()
            if not protective:
                protective = str(voice.get("intent") or "").strip()
            if not protective:
                issues.append(f"voice[{index}] missing protective_intent")
            elif protective == core:
                issues.append(f"voice[{index}] protective_intent equals core_concern")
        return issues

    def _detect_voice_duplication(self, payload: dict) -> list[str]:
        """Flag inner voices that share a name or have near-identical bodies."""
        voices = [v for v in (payload.get("inner_voices") or []) if isinstance(v, dict)]
        issues: list[str] = []
        for i in range(len(voices)):
            for j in range(i + 1, len(voices)):
                ni = normalize_comparable_text(str(voices[i].get("name") or ""))
                nj = normalize_comparable_text(str(voices[j].get("name") or ""))
                if ni and ni == nj:
                    issues.append(f"voice[{i}] and voice[{j}] share the same name")
                    continue
                if _token_overlap(_voice_body_signature(voices[i]), _voice_body_signature(voices[j])) >= _VOICE_DUP_OVERLAP:
                    issues.append(f"voice[{i}] and voice[{j}] are near-duplicates")
        return issues

    def _build_distinctness_repair_prompt(
        self,
        payload: dict,
        issues: list[str],
    ) -> str:
        issues_block = "\n".join(f"- {issue}" for issue in issues) or "- (none)"
        payload_block = json.dumps(payload, ensure_ascii=False, indent=2)
        return f"""Previous outcome JSON has invalid inner voice protective_intent values.
Validity definition:
- core_concern describes what this voice fears would happen.
- protective_intent describes what this voice is trying to protect: a value, state, relationship, boundary, need, or action orientation.
- They must be different dimensions, must not be the same sentence, and protective_intent must not be empty.

Issues found:
{issues_block}

Original outcome JSON:
{payload_block}

Return a repaired complete outcome JSON with the same schema and keys. Repair each invalid voice protective_intent: add protective_intent if it is missing or replace it if it is collapsed/invalid, using a sentence distinct from that voice's core_concern. Preserve all other fields unchanged. Return JSON only, with no explanation."""

    async def _reextract_with_distinctness_hint(
        self,
        payload: dict,
        issues: list[str],
    ) -> dict:
        """Single repair LLM call. Returns repaired payload or original on failure."""
        prompt = self._build_distinctness_repair_prompt(payload, issues)
        try:
            raw = await self._llm(
                prompt,
                system="Return only JSON for the same outcome shape.",
                temperature=0.3,
                max_tokens=1024,
            )
        except Exception:
            logger.exception("Voice distinctness re-extraction failed.")
            return payload
        try:
            parsed = _parse_json(raw)
            if not isinstance(parsed, dict):
                return payload
            if not parsed:
                return payload
            if set(parsed.keys()) != set(payload.keys()):
                return payload
            for key, value in payload.items():
                if key != "inner_voices" and parsed.get(key) != value:
                    return payload
            original_voices = payload.get("inner_voices")
            parsed_voices = parsed.get("inner_voices")
            if not isinstance(parsed_voices, list):
                return payload
            if any(not isinstance(voice, dict) for voice in parsed_voices):
                return payload
            if isinstance(original_voices, list):
                if original_voices and not parsed_voices:
                    return payload
                if len(parsed_voices) != len(original_voices):
                    return payload
                for original_voice, repaired_voice in zip(original_voices, parsed_voices):
                    if not isinstance(original_voice, dict) or not isinstance(repaired_voice, dict):
                        return payload
                    original_keys = set(original_voice.keys())
                    repaired_keys = set(repaired_voice.keys())
                    expected_keys = set(original_keys)
                    expected_keys.add("protective_intent")
                    if repaired_keys != expected_keys:
                        return payload
                    if not str(repaired_voice.get("protective_intent") or "").strip():
                        return payload
                    for key, value in original_voice.items():
                        if key != "protective_intent" and repaired_voice.get(key) != value:
                            return payload
            if self._validate_voice_distinctness(parsed):
                return payload
        except Exception:
            logger.exception("Voice distinctness re-extraction parsing failed.")
            return payload
        return parsed

    def _build_dedup_repair_prompt(self, payload: dict, issues: list[str]) -> str:
        issues_block = "\n".join(f"- {issue}" for issue in issues) or "- (none)"
        payload_block = json.dumps(payload, ensure_ascii=False, indent=2)
        return f"""The following inner_voices contain same-name or near-duplicate entries.
Merge or differentiate them into FEWER, mutually distinct voices (2-4 total). Each voice must
keep name, core_concern, protective_intent, fear, language_style, typical_phrases,
relationship_to_others, intensity. No two voices may share a name or have near-identical bodies.

Issues found:
{issues_block}

Original outcome JSON:
{payload_block}

Return a repaired complete outcome JSON with the same top-level keys. Preserve all non-voice
fields unchanged. Return JSON only, with no explanation."""

    async def _reextract_with_dedup_hint(self, payload: dict, issues: list[str]) -> dict:
        """Single dedup repair LLM call. Returns repaired payload or the original on guard failure."""
        prompt = self._build_dedup_repair_prompt(payload, issues)
        try:
            raw = await self._llm(
                prompt,
                system="Return only JSON for the same outcome shape.",
                temperature=0.3,
                max_tokens=1024,
            )
        except Exception:
            logger.exception("Voice dedup re-extraction failed.")
            return payload
        try:
            parsed = _parse_json(raw)
            if not isinstance(parsed, dict) or not parsed:
                return payload
            if set(parsed.keys()) != set(payload.keys()):
                return payload
            for key, value in payload.items():
                if key != "inner_voices" and parsed.get(key) != value:
                    return payload
            original = payload.get("inner_voices")
            repaired = parsed.get("inner_voices")
            if not isinstance(original, list):
                return payload
            if not isinstance(repaired, list) or any(not isinstance(v, dict) for v in repaired):
                return payload
            if not (2 <= len(repaired) <= len(original)):
                return payload
            for voice in repaired:
                if not all(str(voice.get(k) or "").strip() for k in ("name", "core_concern", "protective_intent")):
                    return payload
            if self._detect_voice_duplication(parsed):
                return payload
        except Exception:
            logger.exception("Voice dedup re-extraction parsing failed.")
            return payload
        return parsed

    async def _apply_voice_dedup(self, payload: dict) -> dict:
        """Detect duplicate voices and apply the dedup repair, keeping the original on failure."""
        dup_issues = self._detect_voice_duplication(payload)
        if not dup_issues:
            return payload
        deduped = await self._reextract_with_dedup_hint(payload, dup_issues)
        if not self._detect_voice_duplication(deduped):
            return deduped
        return payload

    def _fallback_card_voices(self, base_payload: dict) -> list[dict]:
        tensions = base_payload.get("core_tensions") or []
        voices: list[dict] = []
        for tension in tensions[:2]:
            if not isinstance(tension, dict):
                continue
            for pole in (tension.get("pole_a"), tension.get("pole_b")):
                voice = self._fallback_voice_from_pole(str(pole or ""))
                if voice and self._voice_name_not_seen(voices, voice["name"]):
                    voices.append(voice)
                if len(voices) >= 2:
                    return voices
        return voices

    def _voice_name_not_seen(self, voices: list[dict], name: str) -> bool:
        return all(item.get("name") != name for item in voices)

    def _fallback_voice_from_pole(self, pole: str) -> dict | None:
        text = pole.strip()
        if not text:
            return None
        return {
            "name": "我得先把这件事看清",
            "core_concern": "担心没看清就行动会让自己更难。",
            "protective_intent": "想保护判断力和回撤空间。",
            "intensity": 0.6,
            "typical_phrases": [text],
        }

    async def _retry_card_language_if_needed(
        self,
        *,
        outcome: ElicitationOutcome,
        base_payload: dict,
        prompt: str,
        system_prompt: str,
        depth_evaluations: list[DepthEvaluation],
    ) -> ElicitationOutcome:
        initial_failures = self._language_failures(outcome)
        if not initial_failures:
            return outcome

        record_retry()
        try:
            retry_raw = await self._llm(
                prompt + _RETRY_SUFFIX,
                system=system_prompt,
                temperature=0.3,
                max_tokens=1024,
            )
        except Exception:
            record_failure()
            return outcome

        retry_payload = _parse_json(retry_raw)
        if not retry_payload:
            record_failure()
            return outcome

        distinctness_issues = self._validate_voice_distinctness(retry_payload)
        if distinctness_issues:
            record_failure()
            return outcome

        retry_outcome = self._outcome_from_payload(
            self._merge_card_enrichment(base_payload, retry_payload),
            depth_evaluations,
        )
        retry_failures = self._language_failures(retry_outcome)
        if not retry_failures:
            return retry_outcome
        record_failure()
        return retry_outcome if len(retry_failures) < len(initial_failures) else outcome

    async def _retry_for_language_if_needed(
        self,
        *,
        outcome: ElicitationOutcome,
        prompt: str,
        system_prompt: str,
        depth_evaluations: list[DepthEvaluation],
    ) -> ElicitationOutcome:
        initial_failures = self._language_failures(outcome)
        if not initial_failures:
            return outcome

        record_retry()
        try:
            retry_raw = await self._llm(
                prompt + _RETRY_SUFFIX,
                system=system_prompt,
                temperature=0.3,
                max_tokens=1024,
            )
        except Exception:
            logger.warning(
                "language_guard_warning: outcome extractor retry failed for %s",
                ", ".join(initial_failures),
            )
            record_failure()
            return outcome

        retry_payload = _parse_json(retry_raw)
        if not retry_payload:
            logger.warning(
                "language_guard_warning: outcome extractor retry returned invalid JSON for %s",
                ", ".join(initial_failures),
            )
            record_failure()
            return outcome

        distinctness_issues = self._validate_voice_distinctness(retry_payload)
        if distinctness_issues:
            logger.warning(
                "language_guard_warning: outcome extractor retry returned collapsed inner voice intent (%s)",
                ", ".join(distinctness_issues),
            )
            record_failure()
            return outcome

        retry_outcome = self._outcome_from_payload(retry_payload, depth_evaluations)
        retry_failures = self._language_failures(retry_outcome)
        if not retry_failures:
            return retry_outcome

        best_outcome = retry_outcome if len(retry_failures) < len(initial_failures) else outcome
        logger.warning(
            "language_guard_warning: outcome extractor fields still English-heavy after retry; keeping best parsed result (%s)",
            ", ".join(retry_failures),
        )
        record_failure()
        return best_outcome

    def _outcome_from_payload(
        self,
        payload: dict,
        depth_evaluations: list[DepthEvaluation],
    ) -> ElicitationOutcome:
        trajectory = [evaluation.depth_score for evaluation in depth_evaluations]
        final_depth = trajectory[-1] if trajectory else 0.0
        max_depth = max(trajectory) if trajectory else 0.0
        final_readiness = depth_evaluations[-1].readiness_score if depth_evaluations else 0.0

        outcome = ElicitationOutcome.from_dict(
            {
                **payload,
                "conversation_depth": payload.get("conversation_depth", final_depth),
                "max_depth_reached": payload.get("max_depth_reached", max_depth),
                "total_rounds": payload.get("total_rounds", len(trajectory)),
                "depth_trajectory": payload.get("depth_trajectory", trajectory),
                "closing_readiness": payload.get("closing_readiness", final_readiness),
            }
        )
        outcome.dilemma_layers = self._dedupe_layers(outcome.dilemma_layers)
        outcome.core_tensions = self._filter_tensions(outcome.core_tensions)
        outcome.emotion_map = self._fill_emotion_contexts(outcome)
        return outcome

    def _dedupe_layers(self, layers: list[DilemmaLayer]) -> list[DilemmaLayer]:
        seen: set[tuple[str, str, str]] = set()
        deduped: list[DilemmaLayer] = []

        for layer in layers:
            key = dilemma_layer_semantic_key(layer.depth, layer.description, layer.user_language)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(layer)

        return deduped

    def _dedupe_texts(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            deduped.append(text)
        return deduped

    def _filter_tensions(self, tensions: list[Tension]) -> list[Tension]:
        return [
            tension
            for tension in tensions
            if tension.pole_a.strip() and tension.pole_b.strip()
        ]

    def _fill_emotion_contexts(self, outcome: ElicitationOutcome) -> list[EmotionEntry]:
        fallback_context = best_outcome_context_text(outcome)
        return [
            EmotionEntry(
                emotion=item.emotion,
                context=item.context.strip() or fallback_context,
                intensity=item.intensity,
            )
            for item in outcome.emotion_map
        ]

    def _language_failures(self, outcome: ElicitationOutcome) -> list[str]:
        named_values: list[tuple[str, str]] = [("core_dilemma", outcome.core_dilemma)]

        for index, layer in enumerate(outcome.dilemma_layers):
            named_values.append((f"dilemma_layers[{index}].description", layer.description))

        for index, voice in enumerate(outcome.inner_voices):
            named_values.extend(
                [
                    (f"inner_voices[{index}].name", voice.name),
                    (f"inner_voices[{index}].core_concern", voice.core_concern),
                    (f"inner_voices[{index}].protective_intent", voice.protective_intent),
                    (f"inner_voices[{index}].fear", voice.fear),
                    (f"inner_voices[{index}].language_style", voice.language_style),
                ]
            )
            for phrase_index, phrase in enumerate(voice.typical_phrases):
                named_values.append((f"inner_voices[{index}].typical_phrases[{phrase_index}]", phrase))
            for relation_index, relation in enumerate(voice.relationship_to_others):
                named_values.extend(
                    [
                        (
                            f"inner_voices[{index}].relationship_to_others[{relation_index}].target",
                            str(relation.get("target") or ""),
                        ),
                        (
                            f"inner_voices[{index}].relationship_to_others[{relation_index}].dynamic",
                            str(relation.get("dynamic") or ""),
                        ),
                        (
                            f"inner_voices[{index}].relationship_to_others[{relation_index}].description",
                            str(relation.get("description") or ""),
                        ),
                    ]
                )

        for index, emotion in enumerate(outcome.emotion_map):
            named_values.append((f"emotion_map[{index}].context", emotion.context))

        for index, stakeholder in enumerate(outcome.stakeholders):
            named_values.append((f"stakeholders[{index}].role_in_dilemma", stakeholder.role_in_dilemma))

        return find_low_chinese_fields(named_values)

    def _fallback_outcome(
        self,
        conversation_history: list[dict],
        depth_evaluations: list[DepthEvaluation],
    ) -> ElicitationOutcome:
        evidence_history = filter_process_turns(conversation_history)
        user_messages = [msg.get("content", "").strip() for msg in evidence_history if msg.get("role") == "user"]
        last_user_message = next((msg for msg in reversed(user_messages) if msg), "")
        trajectory = [evaluation.depth_score for evaluation in depth_evaluations]
        final_depth = trajectory[-1] if trajectory else 0.0
        max_depth = max(trajectory) if trajectory else 0.0
        final_readiness = depth_evaluations[-1].readiness_score if depth_evaluations else 0.0

        dilemma_layers = []
        if last_user_message:
            dilemma_layers.append(
                DilemmaLayer(
                    description=last_user_message,
                    depth="surface",
                    user_language=last_user_message,
                )
            )

        key_expressions = [msg for msg in user_messages[-3:] if msg]

        return ElicitationOutcome(
            core_dilemma=last_user_message or "The user is exploring an unresolved dilemma.",
            dilemma_layers=dilemma_layers,
            inner_voices=[],
            core_tensions=[],
            emotion_map=[],
            value_conflicts=[],
            stakeholders=[],
            conversation_depth=final_depth,
            max_depth_reached=max_depth,
            total_rounds=len(user_messages),
            depth_trajectory=trajectory,
            key_expressions=key_expressions,
            closing_readiness=final_readiness,
        )
