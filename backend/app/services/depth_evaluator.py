"""LLM-based depth evaluator for Phase 1 elicitation."""

import json
import logging
import re
from typing import Awaitable, Callable, Optional

from app.models.elicitation import DepthEvaluation, SaturationSignals
from app.services.llm import generate as llm_generate

logger = logging.getLogger(__name__)

LlmFn = Callable[..., Awaitable[str]]


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


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _coerce_float(value: object, default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off", ""}:
            return False
        return default
    if value is None:
        return default
    return bool(value)


def _coerce_text(value: object, default: str) -> str:
    if value is None:
        return default

    text = str(value).strip()
    if not text:
        return default
    return text


def _normalize_layer(value: object, default: int = 1) -> int:
    return int(_clamp(_coerce_float(value, default), 1, 3))


_DEPTH_LAYER_BASE = {1: 0.2, 2: 0.5, 3: 0.8}


def derive_depth_score(depth_layer: int, tension_visible: bool, tension_owned: bool) -> float:
    """Deterministic depth score from the signals the LLM reports reliably.

    The LLM is good at the categorical judgments (which layer, whether a tension is
    visible / first-person owned) but cannot calibrate an undefined 0-1 score, so we
    derive the number here. Weights leave margin to clear the graduation gates: a
    layer-2 turn with a visible + owned tension yields 0.75 (> the 0.70 L2->L3 gate).
    """
    base = _DEPTH_LAYER_BASE.get(int(depth_layer), 0.2)
    score = base
    if tension_visible:
        score += 0.15
    if tension_owned:
        score += 0.10
    return _clamp(score, 0.0, 1.0)


def derive_readiness_score(
    tension_owned: bool,
    tension_visible: bool,
    saturation_count: int,
    graduation_ready: bool,
) -> float:
    """Deterministic readiness score. Owning + naming the tension is the core signal
    (0.60 + 0.25 = 0.85 clears the 0.80 graduation gate); saturation contributes a
    small, capped amount because the LLM reports it unreliably.
    """
    score = 0.0
    if tension_owned:
        score += 0.60
    if tension_visible:
        score += 0.25
    score += 0.05 * min(int(saturation_count), 2)
    if graduation_ready:
        score += 0.10
    return _clamp(score, 0.0, 1.0)


def latch_tension_owned(
    current_owned: bool,
    previous_evaluations: list[DepthEvaluation],
    depth_layer: int,
) -> bool:
    """Per-layer ratchet for tension ownership.

    The LLM re-judges tension_owned each turn and drops it back to False once the
    user stops using explicit first-person ownership language, even though ownership
    was already established earlier in the same depth layer (A2). Once owned is True
    for a layer, latch it True for the rest of that layer; advancing to a new layer
    resets the ratchet so a layer-2 claim never stands in for the deeper layer-3 one.
    """
    if current_owned:
        return True
    return any(
        ev.tension_owned and ev.depth_layer == depth_layer
        for ev in previous_evaluations
    )


def latch_tension_visible(
    current_visible: bool,
    previous_evaluations: list[DepthEvaluation],
    depth_layer: int,
) -> bool:
    """Per-layer ratchet for tension visibility (mirror of latch_tension_owned).

    The LLM re-judges tension_visible each turn and flickers it back to False on the
    same input even though both poles were already surfaced earlier in this depth layer
    (RC-3), which drops depth_score back to the layer floor and stalls graduation. Once
    visible is True for a layer, latch it True for the rest of that layer; advancing to a
    new layer resets the ratchet. (2-B latched owned but deliberately not visible; live
    repro on 2026-06-11 showed visible does flicker, so it gets the same treatment.)
    """
    if current_visible:
        return True
    return any(
        ev.tension_visible and ev.depth_layer == depth_layer
        for ev in previous_evaluations
    )


_EXPLICIT_STRUGGLE_MARKERS: tuple[str, ...] = (
    "纠结",
    "两难",
    "左右为难",
    "拿不定",
    "不知道该选",
)

_OWNERSHIP_MARKERS: tuple[str, ...] = (
    "更怕",
    "我最怕",
    "舍不得",
    "不甘心",
    "怕得要死",
    "心里两",
    "我自己拉扯",
)


def detect_ownership_language(text: str | None) -> bool:
    """Deterministic first-person ownership cue (e.g. 「更怕放弃…」).

    Session 380950ab round 9: the user named their deeper fear in first person but
    the LLM only flipped tension_owned one round later, after the hard close. These
    markers flip it the turn it happens (when a tension is already visible); the
    existing per-layer latch then holds it.
    """
    if not text:
        return False
    return any(marker in text for marker in _OWNERSHIP_MARKERS)


def detect_explicit_struggle(text: str | None) -> bool:
    """Ordinary first-person dilemma language already owns the struggle."""
    if not text:
        return False
    return any(marker in text for marker in _EXPLICIT_STRUGGLE_MARKERS)


def _last_user_text(conversation_history: list[dict]) -> str:
    for message in reversed(conversation_history or []):
        if isinstance(message, dict) and message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _fallback_evaluation() -> DepthEvaluation:
    return DepthEvaluation(
        depth_score=0.0,
        depth_layer=1,
        saturation_signals=SaturationSignals(),
        readiness_score=0.0,
        recommended_action="continue",
        strategy_hint="layer_1",
        reasoning="Fallback evaluation due to missing or invalid evaluator output.",
        emotional_state="calm",
        graduation_ready=False,
        graduation_evidence="",
    )


class DepthEvaluator:
    """Evaluate current conversation depth and closing readiness."""

    def __init__(self, llm_fn: Optional[LlmFn] = None) -> None:
        self._llm = llm_fn or llm_generate

    async def evaluate(
        self,
        conversation_history: list[dict],
        previous_evaluations: list[DepthEvaluation],
        current_layer: int,
        layer_round_count: int,
        is_containment: bool,
        tension_cards: list | None = None,
    ) -> DepthEvaluation:
        normalized_layer = _normalize_layer(current_layer)
        prompt = self._build_prompt(
            conversation_history,
            previous_evaluations,
            current_layer=normalized_layer,
            layer_round_count=layer_round_count,
            is_containment=is_containment,
            tension_cards=tension_cards,
        )

        try:
            raw = await self._llm(
                prompt,
                system="Return only JSON.",
                temperature=0.3,
                max_tokens=512,
            )
        except Exception:
            logger.exception("Depth evaluator LLM call failed.")
            return _fallback_evaluation()

        parsed = _parse_json(raw)
        if not parsed:
            return _fallback_evaluation()

        saturation = SaturationSignals.from_dict(parsed.get("saturation_signals") or {})
        depth_layer = _normalize_layer(parsed.get("depth_layer"), normalized_layer)
        last_user_text = _last_user_text(conversation_history)
        explicit_struggle = detect_explicit_struggle(last_user_text)
        tension_visible = _coerce_bool(parsed.get("tension_visible"), False) or explicit_struggle
        tension_visible = latch_tension_visible(tension_visible, previous_evaluations, depth_layer)
        tension_owned = _coerce_bool(parsed.get("tension_owned"), False)
        if not tension_owned and tension_visible and (
            explicit_struggle or detect_ownership_language(last_user_text)
        ):
            tension_owned = True
        tension_owned = latch_tension_owned(tension_owned, previous_evaluations, depth_layer)
        graduation_ready = _coerce_bool(parsed.get("graduation_ready"), False)
        # depth_score / readiness_score are derived, not read from the LLM: the prompt
        # never defines them so the model just echoes 0.0 (A1). Derive deterministically
        # from the categorical signals the model does report reliably.
        return DepthEvaluation(
            depth_score=derive_depth_score(depth_layer, tension_visible, tension_owned),
            depth_layer=depth_layer,
            saturation_signals=saturation,
            readiness_score=derive_readiness_score(
                tension_owned, tension_visible, saturation.count_true(), graduation_ready
            ),
            recommended_action=_coerce_text(parsed.get("recommended_action"), "continue"),
            strategy_hint=_coerce_text(parsed.get("strategy_hint"), f"layer_{normalized_layer}"),
            reasoning=_coerce_text(parsed.get("reasoning"), ""),
            emotional_state=_coerce_text(parsed.get("emotional_state"), "calm"),
            graduation_ready=graduation_ready,
            graduation_evidence=_coerce_text(parsed.get("graduation_evidence"), ""),
            tension_visible=tension_visible,
            tension_owned=tension_owned,
            layer_up_gap=_coerce_text(parsed.get("layer_up_gap"), ""),
        )

    def _build_prompt(
        self,
        conversation_history: list[dict],
        previous_evaluations: list[DepthEvaluation],
        current_layer: int,
        layer_round_count: int,
        is_containment: bool,
        tension_cards: list | None = None,
    ) -> str:
        history_lines = []
        for message in conversation_history[-12:]:
            role = "user" if message.get("role") == "user" else "assistant"
            history_lines.append(f"{role}: {message.get('content', '')}")

        previous_lines = []
        for index, evaluation in enumerate(previous_evaluations[-5:], start=1):
            previous_lines.append(
                f"{index}. depth={evaluation.depth_score:.2f}, "
                f"layer={evaluation.depth_layer}, "
                f"readiness={evaluation.readiness_score:.2f}, "
                f"action={evaluation.recommended_action}, "
                f"strategy={evaluation.strategy_hint}, "
                f"emotion={evaluation.emotional_state}"
            )

        history_block = "\n".join(history_lines) or "(empty)"
        previous_block = "\n".join(previous_lines) or "(none)"
        context_block = "\n".join(
            [
                f"current_layer: {current_layer}",
                f"layer_round_count: {layer_round_count}",
                f"is_containment: {str(is_containment).lower()}",
            ]
        )

        card_lines = []
        for card in (tension_cards or [])[:5]:
            pole_state = (
                "both"
                if (getattr(card, "pole_a", None) and getattr(card, "pole_b", None))
                else ("one" if getattr(card, "pole_a", None) else "none")
            )
            card_lines.append(
                f"- id={getattr(card, 'id', '')}, kind={getattr(card, 'kind', '')}, "
                f"poles={pole_state}, status={getattr(card, 'status', '')}, "
                f"intensity={getattr(card, 'intensity_hint', 0.0)}"
            )
        cards_block = "\n".join(card_lines) or "(none)"

        return f"""Evaluate the current depth of this dilemma interview using a three-layer progressive deepening model.

Three-layer progressive deepening:
- layer 1: understand the user's whole situation and what they see as the central dilemma. Do not require exhaustive factual detail.
- layer 2: understand why the central dilemma is hard: the cost, fear, hope, or attachment carried by either side.
- layer 3: deep conflict involving a stated-self/actual-action gap or unsaid sentence.

Emotional states must be one of: calm, activated, intense.
If emotional_state is "intense", then strategy_hint must be "containment".
If the conversation should close, return strategy_hint as "closing".
graduation_ready applies only to the current active transition (1 -> 2 or 2 -> 3), not the whole conversation.
depth_layer must be 1, 2, or 3.
tension_visible is true when the user names a choice, conflict, or undecided situation. "我在 A 和 B 之间纠结" already makes the tension visible; costs do not need to be listed first.
tension_owned is true when the user presents the struggle as their own, including ordinary wording such as "我在 A 和 B 之间纠结/两难". Never require ritual wording such as "两个声音" or "两股力量".

Conversation history:
{history_block}

Previous depth evaluations:
{previous_block}

Current progressive deepening context:
{context_block}

Surfaced tension cards (deterministic tracker state - a tension may be visible even when its two sides were said in different turns):
{cards_block}

If your scores and layer would match the previous evaluation, reasoning MUST state what NEW information this turn added; do not repeat the previous reasoning. Always fill layer_up_gap with the single most important thing still missing to advance to the next layer (use "" only when ready to advance).

Return a JSON object with this shape:
{{
  "depth_layer": {current_layer},
  "saturation_signals": {{
    "depth_saturated": false,
    "theme_saturated": false,
    "emotion_settled": false,
    "spontaneous_integration": false
  }},
  "recommended_action": "continue",
  "strategy_hint": "layer_{current_layer}",
  "reasoning": "brief explanation",
  "emotional_state": "calm",
  "graduation_ready": false,
  "graduation_evidence": "",
  "tension_visible": false,
  "tension_owned": false,
  "layer_up_gap": ""
}}
"""
