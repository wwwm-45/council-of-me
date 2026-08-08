"""Deterministic move primitives used by the elicitation MoveDispatcher."""

from typing import Any, Iterable

from app.models.elicitation import DepthEvaluation, TensionCard
from app.services.intent_planner import LAYER_ALLOWED, LAYER_FALLBACK_INTENT


DEFAULT_ABSTRACTION_WORDS: tuple[str, ...] = (
    "准备好",
    "底气",
    "满意",
    "配得上",
    "够格",
    "完美",
    "追不上",
    "不适合",
    "不行",
    "跟不上",
    "真正想要的",
    "应该的样子",
    "合适的",
    "差不多",
)

DEFAULT_ADVICE_PHRASES: tuple[str, ...] = (
    "怎么办",
    "该怎么",
    "该不该",
    "应不应该",
    "能不能告诉我",
    "你觉得我该",
    "给我建议",
    "帮我决定",
    "帮我看看",
    "你说我",
    "换你会",
    "如果是你",
)

COVERAGE_PROBE_MOVES: tuple[str, ...] = ("probe_values", "probe_stakeholders")

_BINARY_QUESTION_MARKERS: tuple[str, ...] = (
    "还是",
    "哪一边",
    "哪一个",
    "哪一种",
    "哪一头",
    "哪个",
    "哪边",
    "哪件",
)


def is_binary_choice_question(text: str | None) -> bool:
    """Heuristic: a question that forces the user to pick between offered options.

    Deliberately broad ("哪个" can catch some open questions): the cost of a false
    positive is only one extra form rotation, while the missed-detection cost is the
    binary-question monotony seen in session 380950ab (5 of 10 turns).
    """
    if not text:
        return False
    content = str(text)
    if "？" not in content and "?" not in content:
        return False
    return any(marker in content for marker in _BINARY_QUESTION_MARKERS)


def consecutive_binary_questions(history: list[dict] | None) -> int:
    """Length of the trailing streak of binary-choice assistant questions."""
    count = 0
    for message in reversed(history or []):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        if is_binary_choice_question(str(message.get("content") or "")):
            count += 1
        else:
            break
    return count

_VALUE_MIN_COUNT = 2

_PROBE_DEFER_MARKERS: tuple[str, ...] = (
    "说不清",
    "理不清",
    "分不清",
    "不知道",
    "什么意思",
    "没听懂",
    "没看懂",
    "没明白",
    "不明白",
    "不理解",
)
_PROBE_DEFER_INTENSITY = 0.8


def _coverage_probe_would_interrupt(
    history: list[dict],
    extracted_info: dict[str, Any] | None,
) -> bool:
    """True when a coverage probe this turn would cut off a charged moment.

    Session 380950ab round 4: the stakeholder probe fired verbatim right after the
    user said they could no longer tell what was blocking them. Defer probes one
    turn when the latest user turn shows confusion markers or matches a
    high-intensity extracted delta; the empty-field trigger still holds next turn.
    """
    last_text = _last_user_text(history)
    if not last_text:
        return False
    if any(marker in last_text for marker in _PROBE_DEFER_MARKERS):
        return True
    for delta in (extracted_info or {}).get("deltas") or []:
        if not isinstance(delta, dict):
            continue
        quote = str(delta.get("raw_quote") or "")
        if not quote or (quote not in last_text and last_text not in quote):
            continue
        try:
            intensity = float(delta.get("intensity_hint") or 0.0)
        except (TypeError, ValueError):
            intensity = 0.0
        if intensity >= _PROBE_DEFER_INTENSITY:
            return True
    return False


def _detect_abstraction(text: str | None, words: Iterable[str]) -> str | None:
    if not text:
        return None
    for word in words:
        if word and word in text:
            return word
    return None


def _detect_advice_seeking(text: str | None, phrases: Iterable[str]) -> bool:
    if not text:
        return False
    return any(bool(phrase) and phrase in text for phrase in phrases)


def _abstraction_already_grounded(card: TensionCard | None, word: str) -> bool:
    if card is None or not word:
        return False
    return any(word in (layer.user_language or "") for layer in card.layers)


def _last_user_text(history: list[dict]) -> str:
    for message in reversed(history):
        if isinstance(message, dict) and message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _name_both_poles_pair(card: TensionCard | None) -> tuple[str, str] | None:
    """Return (left, right) labels to contrast, or None if there isn't enough material.

    For bipolar cards the right side is intentionally empty: one pole is named and the
    move asks the user to name the opposite. For undecided/tangled the two strongest
    candidates/threads are the pair.
    """
    if card is None:
        return None
    if card.kind == "bipolar":
        pole_a = (card.pole_a or "").strip()
        pole_b = (card.pole_b or "").strip()
        if pole_a and not pole_b:
            return (pole_a, "")
        return None
    if card.kind == "undecided":
        items = [c.strip() for c in (card.candidates or []) if c and c.strip()]
        return (items[0], items[1]) if len(items) >= 2 else None
    if card.kind == "tangled":
        items = [t.strip() for t in (card.threads or []) if t and t.strip()]
        return (items[0], items[1]) if len(items) >= 2 else None
    return None


def _name_both_poles_instruction(card: TensionCard) -> str:
    pair = _name_both_poles_pair(card)
    if pair is None:
        return ""
    left, right = pair
    if card.kind == "bipolar":
        return (
            f'用户已经说出“{left}”这一头，但对面那一头还没有说出来。'
            "这一轮不要继续深化这一头，要把相反一头请出来：如果不走这边，"
            "他最害怕看到什么、最不愿意放开什么？不要替用户命名对面，让用户自己说。"
        )
    return (
        f'用户提到了“{left}”和“{right}”这两边，但还没把它们当成互相拉扯的两股力量来谈。'
        "这一轮把这两边并排摆出来，问用户：现在哪一边的声音更大，"
        "另一边让他最放不下、最不愿意放掉的是什么？不要替用户下判断，让他自己说。"
    )


def _trigger_name_both_poles(
    focus_card: TensionCard | None,
    evaluation: DepthEvaluation,
) -> bool:
    if focus_card is None:
        return False
    if evaluation.recommended_action in {"prepare_closing", "close"}:
        return False
    if evaluation.tension_visible and evaluation.tension_owned:
        return False
    return _name_both_poles_pair(focus_card) is not None


def _trigger_make_concrete(
    history: list[dict],
    focus_card: TensionCard | None,
    evaluation: DepthEvaluation,
    abstraction_words: Iterable[str],
) -> str | None:
    if evaluation.emotional_state == "intense":
        return None
    word = _detect_abstraction(_last_user_text(history), abstraction_words)
    if word is None or _abstraction_already_grounded(focus_card, word):
        return None
    return word


def _trigger_block_advice(
    history: list[dict],
    evaluation: DepthEvaluation,
    advice_phrases: Iterable[str],
) -> bool:
    if evaluation.emotional_state == "intense":
        return False
    return _detect_advice_seeking(_last_user_text(history), advice_phrases)


def _coverage_gates(
    evaluation: DepthEvaluation,
    current_layer: int,
    on_cooldown: bool,
    interrupting: bool = False,
) -> bool:
    return (
        current_layer >= 2
        and evaluation.emotional_state != "intense"
        and evaluation.recommended_action not in {"prepare_closing", "close"}
        and not on_cooldown
        and not interrupting
    )


def _distinct_value_count(extracted_info: dict[str, Any] | None) -> int:
    """Values copied verbatim out of the core dilemma are domain nouns, not values.

    Session 380950ab: round-1 extraction parroted 项目经验/时间效率 straight from the
    dilemma statement, satisfying the >=2 gate and permanently disarming the meaning
    probe. Only count value entries that add language beyond the dilemma itself.
    """
    info = extracted_info or {}
    core = str(info.get("core_dilemma") or "")
    count = 0
    for item in info.get("values") or []:
        text = str(item).strip()
        if text and text not in core:
            count += 1
    return count


def _trigger_probe_values(
    extracted_info: dict[str, Any] | None,
    evaluation: DepthEvaluation,
    current_layer: int,
    on_cooldown: bool,
    interrupting: bool = False,
) -> bool:
    if extracted_info is None:
        return False
    if not _coverage_gates(evaluation, current_layer, on_cooldown, interrupting):
        return False
    return _distinct_value_count(extracted_info) < _VALUE_MIN_COUNT


def _trigger_probe_stakeholders(
    extracted_info: dict[str, Any] | None,
    evaluation: DepthEvaluation,
    current_layer: int,
    on_cooldown: bool,
    interrupting: bool = False,
) -> bool:
    if extracted_info is None:
        return False
    if not _coverage_gates(evaluation, current_layer, on_cooldown, interrupting):
        return False
    return not ((extracted_info or {}).get("stakeholders") or [])


def _normalize_layer(layer: int) -> int:
    try:
        value = int(layer)
    except (TypeError, ValueError):
        value = 1
    return min(max(value, 1), 3)


def _clamp_intent(intent: str, layer: int) -> str:
    return intent if intent in LAYER_ALLOWED[layer] else LAYER_FALLBACK_INTENT[layer]


class MoveDispatcher:
    """Deterministic move dispatcher that post-processes IntentPlanner output."""

    def __init__(
        self,
        abstraction_words: Iterable[str] | None = None,
        advice_phrases: Iterable[str] | None = None,
    ) -> None:
        self._abstraction_words = (
            DEFAULT_ABSTRACTION_WORDS if abstraction_words is None else tuple(abstraction_words)
        )
        self._advice_phrases = (
            DEFAULT_ADVICE_PHRASES if advice_phrases is None else tuple(advice_phrases)
        )

    def dispatch(
        self,
        *,
        plan: dict[str, Any],
        focus_card: TensionCard | None,
        history: list[dict],
        evaluation: DepthEvaluation,
        current_layer: int,
        extracted_info: dict[str, Any] | None = None,
        coverage_probe_on_cooldown: bool = False,
        meaning_probe_pending: bool = False,
    ) -> dict[str, Any]:
        layer = _normalize_layer(current_layer)
        result = dict(plan)
        result["move"] = None
        result["hard_instruction"] = None
        result["original_intent"] = None

        original_intent = str(plan.get("intent") or "")

        if plan.get("user_turn_kind") == "clarify_request" or original_intent == "clarify_back":
            return self._with_move(
                result,
                move="clarify_back",
                intent="clarify_back",
                original_intent=original_intent,
                hard_instruction=(
                    "用户没有听懂上一问。先用一句平实的话说明上一问真正想了解什么，"
                    "再结合当前困境换成一个更简单的问题。不要推进层级，不要补采访字段，"
                    "也不要转向价值、利益相关者或其他新话题。"
                ),
            )

        if evaluation.strategy_hint == "self_statement_request":
            return self._with_move(
                result,
                move="self_statement_request",
                intent=_clamp_intent("probe_threshold", layer),
                original_intent=original_intent,
                hard_instruction=(
                    "用户已经谈到困境的两边，但还没有说清各自为什么难以放下。"
                    "沿着用户自己的说法，自然地邀请他讲讲其中一边最牵动自己的地方。"
                    "不要使用填空题，不要要求他说‘两股力量’或‘两个声音’，也不要替他组织标准答案。"
                ),
            )

        if _trigger_block_advice(history, evaluation, self._advice_phrases):
            target_intent = "probe_meaning" if layer == 1 else "probe_value_conflict"
            return self._with_move(
                result,
                move="block_advice_then_pivot",
                intent=_clamp_intent(target_intent, layer),
                original_intent=original_intent,
                hard_instruction=(
                    "用户在索要建议。这一轮不直接给建议、方向或答案。"
                    "先承认这个问题很重要，然后回到用户正在权衡的困境，"
                    "继续理解他担心失去什么、又想保护什么。"
                ),
            )

        # Values and stakeholders used to be injected here whenever an extraction field
        # was empty. That made the harness chase its schema instead of the user's current
        # dilemma (e.g. answering “什么意思” with a brand-new values question). The planner
        # may still choose a meaning/cost direction when it fits the conversation, but an
        # empty field no longer overrides that direction.
        return result

    def _with_move(
        self,
        result: dict[str, Any],
        *,
        move: str,
        intent: str,
        original_intent: str,
        hard_instruction: str,
    ) -> dict[str, Any]:
        result["move"] = move
        result["intent"] = intent
        result["hard_instruction"] = hard_instruction
        result["original_intent"] = original_intent
        return result
