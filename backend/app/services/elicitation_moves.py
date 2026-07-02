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

_PROBE_DEFER_MARKERS: tuple[str, ...] = ("说不清", "理不清", "分不清", "不知道")
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

        if evaluation.strategy_hint == "self_statement_request":
            return self._with_move(
                result,
                move="self_statement_request",
                intent=_clamp_intent("probe_threshold", layer),
                original_intent=original_intent,
                hard_instruction=(
                    "用户已经把困境讲到了相当深度，但还没用第一人称把两边说出来。"
                    "这一轮只问一个问题，让用户自己用一句话填出两边：\n"
                    "「如果让你自己说，你心里这两股力量在拉扯——一边是 _____，"
                    "另一边是 _____。你来填这两边。」\n"
                    "可以微调措辞但模板骨架不变；问完就停，不要替用户填、不要追问其他、不要解释。"
                ),
            )

        if _trigger_name_both_poles(focus_card, evaluation):
            target_intent = "probe_threshold" if layer == 1 else "probe_cost"
            return self._with_move(
                result,
                move="name_both_poles",
                intent=_clamp_intent(target_intent, layer),
                original_intent=original_intent,
                hard_instruction=_name_both_poles_instruction(focus_card),
            )

        abstraction = _trigger_make_concrete(
            history,
            focus_card,
            evaluation,
            self._abstraction_words,
        )
        if abstraction:
            return self._with_move(
                result,
                move="make_concrete",
                intent=_clamp_intent("probe_threshold", layer),
                original_intent=original_intent,
                hard_instruction=(
                    f"用户用了抽象词“{abstraction}”。这一轮不要让用户继续用抽象词解释，"
                    "用一个开放的问题，把它还原成一个具体画面：那一刻具体发生了什么、"
                    "用户在做什么、心里或身体是什么感觉。如果要问到旁边的人，"
                    "就落在「是谁、对方说了或做了什么」上；"
                    "不要问「身边有没有人」这种只能回答有或没有、问完就断的问题。"
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
                    "先承认这个问题很重要，然后回到张力：这件事里两个声音在拉扯，"
                    "先听清它们各自害怕什么、想保护什么。"
                ),
            )

        interrupting = _coverage_probe_would_interrupt(history, extracted_info)

        # meaning_probe_pending: by layer 3 the session is owed one meaning-layer
        # question even when the (possibly parroted) values list looks covered.
        if _trigger_probe_values(
            extracted_info, evaluation, layer, coverage_probe_on_cooldown, interrupting
        ) or (
            meaning_probe_pending
            and extracted_info is not None
            and _coverage_gates(evaluation, layer, coverage_probe_on_cooldown, interrupting)
        ):
            target_intent = "probe_meaning" if layer == 2 else "probe_value_conflict"
            return self._with_move(
                result,
                move="probe_values",
                intent=_clamp_intent(target_intent, layer),
                original_intent=original_intent,
                hard_instruction=(
                    "用户已经把困境讲到了一定深度，但还没说清这件事对他意味着什么、"
                    "他真正在乎的是什么。这一轮只问一个问题，把价值那一层请出来，"
                    "用平实、口语的说法，比如「做这件事对你来说最重要的是什么？」，"
                    "或者「如果过几年回头看现在，你最不想丢掉的是什么？」。"
                    "开头先用半句承接用户上一句里的一个具体说法，再自然转到这个问题，"
                    "不要让话题显得被硬切。"
                    "不要替用户总结价值，让他自己说；问完就停，不要追问其他、不要给建议。"
                ),
            )

        if _trigger_probe_stakeholders(
            extracted_info, evaluation, layer, coverage_probe_on_cooldown, interrupting
        ):
            return self._with_move(
                result,
                move="probe_stakeholders",
                intent=_clamp_intent("probe_cost", layer),
                original_intent=original_intent,
                hard_instruction=(
                    "到现在为止，用户基本只在讲自己，还没提到这件事里还有谁被牵动。"
                    "这一轮只问一个问题，把相关的人请进来：这个决定里，除了你自己，"
                    "还有谁会被影响？谁的反应或感受是你最放不下的？"
                    "开头先用半句承接用户上一句里的一个具体说法，再自然转到这个问题，"
                    "不要让话题显得被硬切。不要替用户列举，"
                    "让他自己说；问完就停，不要追问其他、不要给建议。"
                ),
            )

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
