"""Depth-driven elicitation service for Phase 1."""

import asyncio
import copy
import json
import logging
import re
from typing import Any, AsyncIterator, Optional

from app.models.elicitation import (
    DepthEvaluation,
    ElicitationOutcome,
    TensionCard,
    default_extracted_info,
    normalize_comparable_text,
)
from app.services.depth_evaluator import DepthEvaluator
from app.services.elicitation_control import filter_process_turns
from app.services.elicitation_moves import (
    COVERAGE_PROBE_MOVES,
    MoveDispatcher,
)
from app.services.intent_planner import IntentPlanner
from app.services.llm import (
    generate as llm_generate_async,
    generate_chat as llm_generate_chat_async,
    generate_chat_stream as llm_generate_chat_stream_async,
)
from app.services.outcome_extractor import OutcomeExtractor
from app.services.tension_tracker import TensionTracker
from app.services.turn_auditor import TurnAuditor
from app.utils.text import strip_markdown


logger = logging.getLogger(__name__)

EXTRACTION_FIELD_SPEC = """- core_dilemma: string | null
- inner_voices: [{"name":"", "core_concern":""}]
- values: string[]  // 用户根本看重的东西（价值语言，不是领域名词或简历条目）。例：被认可、不辜负心血、对家人有交代、安全感
- tactics: string[]  // 为达成目的采取的手段/策略/操作。例：内推、刷技术关键词、走某条筛选规则
- emotions: string[]
- stakeholders: string[]
- constraints: string[]"""


async def _default_llm(
    prompt: str,
    system: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 512,
) -> str:
    return await llm_generate_async(
        prompt,
        system=system,
        temperature=temperature,
        max_tokens=max_tokens,
    )


async def _default_llm_chat(messages: list[dict], system: Optional[str] = None) -> str:
    return await llm_generate_chat_async(messages, system=system, temperature=0.5, max_tokens=1024)


async def _default_llm_chat_stream(messages: list[dict], system: Optional[str] = None) -> AsyncIterator[str]:
    async for chunk in llm_generate_chat_stream_async(messages, system=system, temperature=0.5, max_tokens=1024):
        yield chunk


_PROMPT_IDENTITY = (
    "【角色】你是一位专注的困境访谈者。陪用户把他正在经历的核心困境慢慢说清楚，"
    "不要急着解决，也不要为了收集信息而偏离这场困境。"
)
_PROMPT_FORM = (
    "【交流原则】认真听用户刚说的话，像面对面谈话一样自然、平实，不做文学化概括。"
    "每轮只顺着用户最新表达中的一个重点继续听，不复述整段背景，也不把两边列成选项让用户选择。"
    "一次只问一个开放问题。"
    "问题要让用户继续展开，而不是确认他已经明确说过的内容。"
    "忠实理解，不替用户补全尚未表达的事实、感受或结论。"
    "如果用户没有听懂，先把上一问解释清楚，再继续当前话题。"
)


MIN_ROUNDS = 3
L1_MIN_ROUNDS = 2
L1_MAX_ROUNDS = 3
L2_MIN_ROUNDS = 3
L3_MIN_ROUNDS = 2
SOFT_TARGET_ROUND = 9
HARD_CLOSE_ROUND = 10
SELF_STATEMENT_MIN_ROUND = 6
ABSOLUTE_MAX_ROUNDS = 14
MAX_CONTAINMENT_ROUNDS = 2

_LAYER_PACE = {
    1: "【当前方向】先顺着用户最后提到的一边继续理解，后续轮次再自然转向另一边。每个问题都服务于理解核心困境为什么成立；不研究某个选项的理想标准或领域细节，也不判断偏好、推动选择、盘点进度或讨论下一步安排。",
    2: "【当前方向】仍不推动选择或规划行动。沿着同一个核心困境，继续理解两边分别牵动的担心、期待与代价。",
    3: "【当前方向】在已经说清的拉扯上，理解更深的价值、信念或自我期待。",
}
_CONTAINMENT_PACE = "【节奏】容纳：情绪强烈，沿焦点只问一个最简单、最直白的问题，不展开。"
_INTENT_DESC = {
    "probe_fact": "补足理解困境所需的事实",
    "probe_threshold": "理解判断边界",
    "probe_cost": "理解这一边带来的代价",
    "probe_fear": "理解最担心发生的结果",
    "probe_value_conflict": "理解两项诉求为何拉扯",
    "probe_identity_gap": "理解自我期待与行动的落差",
    "probe_unspoken": "理解仍未说出的关键部分",
    "probe_meaning": "理解这件事为何重要",
    "probe_self_image": "理解自我期待",
    "probe_belief": "理解背后的判断前提",
    "containment": "承接强烈情绪，不推进",
    "clarify_back": "解释上一问并修复理解",
    "hand_off": "停止追问并移交",
}

CLOSE_CANONICAL_TEXT = "这一段，你把卡住自己的地方一点点说清楚了。辛苦了，我们就先聊到这儿——下一阶段，一起看看这些话拼成的困境画像。"
CLOSE_CANONICAL_MARKER = "困境画像"
_NEUTRAL_ACK_TOKENS = ("好的", "明白", "收到", "理解", "了解", "嗯", "好")
_ACK_SEPARATORS = " \t\r\n。？，,!,.、：:；;！!"
_SENTENCE_END_CHARS = "。！？!?"
_TRAILING_CLOSERS = "”\"’'）)】"
_FIXED_FORM_FEEDBACK = "\n【改写反馈】请像自然访谈一样回应用户，只保留一个主要问题，并与用户刚说的内容连贯衔接。"
_DIRECTION_RETRY_FEEDBACK = (
    "\n【方向校正】现在仍是理解困境的阶段。忠实承接用户最后一句，同时回到对话罗盘指定的焦点，"
    "邀请他展开这个焦点背后的原因、担心或在意。不要把一个例子继续拆成类别清单，"
    "不要擅自改变事实属于哪一方，也不要讨论下一步方案。"
)
_PREMATURE_CHOICE_MARKERS = (
    "还是",
    "更偏向",
    "更想选",
    "更放不下",
    "更担心",
    "哪一头",
    "哪一边",
    "选哪",
    "选择哪",
)
_ACTION_PLANNING_MARKERS = (
    "怎么安排",
    "怎么处理",
    "打算怎么",
    "准备怎么",
    "接下来怎么",
    "应该怎么",
    "该怎么",
)
_PROGRESS_INVENTORY_MARKERS = (
    "卡在什么",
    "卡在哪里",
    "卡在哪",
    "没法收尾",
    "写到哪",
    "做到哪",
    "进行到哪",
    "还差多少",
    "完成多少",
    "进度怎么样",
    "目前进度",
)


def _drifts_into_decision_or_planning(text: str, evaluation: DepthEvaluation) -> bool:
    """Catch only high-level interview drift; wording remains model-owned."""
    if evaluation.depth_layer >= 3 or evaluation.recommended_action in {"prepare_closing", "close"}:
        return False
    content = str(text or "")
    markers = (
        *_PREMATURE_CHOICE_MARKERS,
        *_ACTION_PLANNING_MARKERS,
        *_PROGRESS_INVENTORY_MARKERS,
    )
    return any(marker in content for marker in markers)


def _build_direction_fallback(history: list[dict]) -> str:
    """Minimal open turn used only after every model attempt leaves interview mode."""
    user_turns = [
        message
        for message in history
        if isinstance(message, dict) and message.get("role") == "user"
    ]
    if len(user_turns) >= 2:
        return "你刚说的这一边已经清楚一些了。让你仍然犹豫的另一边，最牵动你的是什么？"
    return "你刚才提到的这个点，对你来说为什么重要？"


def _build_audit_fallback(*, fact_failed: bool) -> str:
    """Safe output after all otherwise-usable candidates fail audit."""
    if fact_failed:
        return "我先确认一下，避免把你的意思理解反了。你刚才说的这个影响，具体对应的是哪一种处境？"
    return "我想先回到这件事本身。这里还有哪个关键部分，是我们刚才没有说清的？"


def _split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    current: list[str] = []
    stripped = text.strip()
    pending_terminal = False
    for index, char in enumerate(stripped):
        if pending_terminal and char not in _TRAILING_CLOSERS:
            sentence = "".join(current).strip()
            if sentence:
                sentences.append(sentence)
            current = []
            pending_terminal = False

        current.append(char)
        if char in _SENTENCE_END_CHARS:
            next_char = stripped[index + 1] if index + 1 < len(stripped) else ""
            if next_char in _TRAILING_CLOSERS:
                pending_terminal = True
                continue
            sentence = "".join(current).strip()
            if sentence:
                sentences.append(sentence)
            current = []
            pending_terminal = False
    remainder = "".join(current).strip()
    if remainder:
        sentences.append(remainder)
    return sentences


def _ends_with_question(text: str) -> bool:
    return text.rstrip().rstrip(_TRAILING_CLOSERS).endswith(("？", "?"))


def _strip_leading_ack(text: str) -> str:
    stripped = text.lstrip()
    for token in _NEUTRAL_ACK_TOKENS:
        if not stripped.startswith(token):
            continue
        rest = stripped[len(token) :]
        if rest and rest[0] not in _ACK_SEPARATORS:
            continue
        return rest.lstrip(_ACK_SEPARATORS)
    return stripped


def _truncate_after_first_question(text: str) -> str:
    for index, char in enumerate(text):
        if char not in "？?":
            continue
        end = index + 1
        while end < len(text) and text[end] in _TRAILING_CLOSERS:
            end += 1
        return text[:end].strip()
    return text.strip()


def _post_process_main_output(text: str, evaluation: DepthEvaluation, *, intent: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""

    if evaluation.recommended_action == "close":
        return CLOSE_CANONICAL_TEXT

    sentences = _split_sentences(stripped)
    if intent == "containment" and evaluation.emotional_state == "intense":
        return sentences[0] if sentences else ""

    # Collapse to the first question when there are extra question marks OR a trailing
    # non-question tail (model wrote "承接。问？收尾。"). Salvaging here keeps good content
    # instead of discarding it to retry -> canned fallback.
    question_count = sum(1 for char in stripped if char in "？?")
    last_is_question = bool(sentences) and _ends_with_question(sentences[-1])
    if question_count and (question_count > 1 or not last_is_question):
        stripped = _truncate_after_first_question(stripped)

    sentences = _split_sentences(stripped)
    if len(sentences) > 4:
        stripped = "".join(sentences[:4]).strip()
        sentences = _split_sentences(stripped)
    if not sentences or not _ends_with_question(sentences[-1]):
        return ""

    return stripped


# Clean, quote-free last resort for the degraded path. Only used when the model produced
# no usable question at all across every attempt; it can never echo a truncated quote.
_DEGRADED_MINIMAL_QUESTION = "这件事里还有没说尽的地方。你现在最想先讲清楚的是哪一点？"


def _salvage_degraded_output(raw: str, evaluation: DepthEvaluation, *, intent: str) -> str:
    """Ship the model's own best (if imperfect) question instead of a canned fallback.

    The old canned _FALLBACK_TEMPLATES degraded reply (你刚才提到「…」。…) reads poorly and
    could echo a mid-phrase-truncated quote, so when the strict form gate rejected every
    attempt we now salvage the model's actual last question — a real question beats a
    canned template. Only when no question exists anywhere do we emit a clean, quote-free
    minimal probe (never the 你刚才提到「…」 template).
    """
    if evaluation.recommended_action == "close":
        return CLOSE_CANONICAL_TEXT
    text = _strip_leading_ack(strip_markdown(raw or "").strip())
    if "？" in text or "?" in text:
        salvaged = _truncate_after_first_question(text)
        sentences = _split_sentences(salvaged)
        if len(sentences) > 4:
            salvaged = "".join(sentences[-4:]).strip()
            sentences = _split_sentences(salvaged)
        if sentences and _ends_with_question(sentences[-1]):
            return salvaged
    return _DEGRADED_MINIMAL_QUESTION


_SELF_STATEMENT_POLES = re.compile(
    r"一边是?(?P<a>[^，。；！？,;!?]{2,40})[，,；;]\s*另一边是?(?P<b>[^，。；！？,;!?]{2,40})"
)


def _parse_self_statement_poles(text: str | None) -> tuple[str, str] | None:
    """Parse the canonical「一边是…，另一边是…」answer to a self_statement_request."""
    match = _SELF_STATEMENT_POLES.search(str(text or ""))
    if match is None:
        return None
    pole_a = match.group("a").strip()
    pole_b = match.group("b").strip()
    if not pole_a or not pole_b:
        return None
    return pole_a, pole_b


def _build_close_text(history: list[dict] | None) -> str:
    """Return the deterministic stage-close line.

    A short, quote-free recap that bridges into the portrait stage (承上启下).
    An earlier version mirrored the user's last clause back to them; product
    feedback (2026-06-14) replaced it with a generic recap, so the close no
    longer depends on ``history``.
    """
    return CLOSE_CANONICAL_TEXT


def _normalize_active_layer(value: int) -> int:
    return min(max(value, 1), 3)


class ElicitationService:
    """Generate counseling responses and decide convergence."""

    def __init__(
        self,
        llm_generate_fn=None,
        llm_chat_fn=None,
        llm_chat_stream_fn=None,
        depth_evaluator: Optional[DepthEvaluator] = None,
        outcome_extractor: Optional[OutcomeExtractor] = None,
        intent_planner: Optional[IntentPlanner] = None,
        turn_auditor: Optional[TurnAuditor] = None,
        move_dispatcher: Optional[MoveDispatcher] = None,
    ) -> None:
        self._llm = llm_generate_fn or _default_llm
        self._llm_chat = llm_chat_fn or _default_llm_chat
        self._llm_chat_stream = llm_chat_stream_fn or _default_llm_chat_stream
        self._depth_evaluator = depth_evaluator or DepthEvaluator(llm_fn=self._llm)
        self._outcome_extractor = outcome_extractor or OutcomeExtractor(llm_fn=self._llm)
        self._intent_planner = intent_planner or IntentPlanner(llm_fn=self._llm)
        self._turn_auditor = turn_auditor or TurnAuditor(llm_fn=self._llm)
        self._move_dispatcher = move_dispatcher or MoveDispatcher()
        self._tension_tracker = TensionTracker()
        self._close_gate_blocks = 0
        self._l1_graduate_blocked_by_tension = 0
        self._l2_graduate_blocked_by_probed_count = 0
        self._self_statement_requests = 0
        self._coverage_probes = 0

    async def generate_response(
        self,
        user_input: str,
        conversation_history: list[dict],
        extracted_info: dict,
        round_count: int,
        depth_evaluations: list[DepthEvaluation],
        closing_revert_count: int,
        current_layer: int,
        layer_round_count: int,
        is_containment: bool,
        containment_round_count: int,
        tension_cards: list[dict[str, Any] | TensionCard] | None = None,
        focus_card_id: str | None = None,
        l1_own_count: int = 0,
        tension_probed_seen: bool = False,
        user_display_name: str | None = None,
        consecutive_intervene_count: int = 0,
        focus_trace: list[dict[str, Any]] | None = None,
    ) -> tuple[
        str,
        bool,
        dict,
        list[dict],
        list[DepthEvaluation],
        Optional[ElicitationOutcome],
        dict[str, Any],
        dict[str, Any],
    ]:
        turn = await self._plan_and_generate(
            user_input=user_input,
            conversation_history=conversation_history,
            extracted_info=extracted_info,
            round_count=round_count,
            depth_evaluations=depth_evaluations,
            closing_revert_count=closing_revert_count,
            current_layer=current_layer,
            layer_round_count=layer_round_count,
            is_containment=is_containment,
            containment_round_count=containment_round_count,
            tension_cards=tension_cards,
            focus_card_id=focus_card_id,
            l1_own_count=l1_own_count,
            tension_probed_seen=tension_probed_seen,
            user_display_name=user_display_name,
            consecutive_intervene_count=consecutive_intervene_count,
            focus_trace=focus_trace,
            stream_first=False,
        )
        return (
            turn["response"],
            turn["should_continue"],
            turn["merged_info"],
            turn["history"],
            turn["new_evaluations"],
            turn["outcome"],
            turn["next_state"],
            turn["tension_state"],
        )

    async def generate_response_stream(
        self,
        user_input: str,
        conversation_history: list[dict],
        extracted_info: dict,
        round_count: int,
        depth_evaluations: list[DepthEvaluation],
        closing_revert_count: int,
        current_layer: int,
        layer_round_count: int,
        is_containment: bool,
        containment_round_count: int,
        tension_cards: list[dict[str, Any] | TensionCard] | None = None,
        focus_card_id: str | None = None,
        l1_own_count: int = 0,
        tension_probed_seen: bool = False,
        user_display_name: str | None = None,
        consecutive_intervene_count: int = 0,
        focus_trace: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        current_layer = _normalize_active_layer(current_layer)
        history = list(conversation_history)
        history.append({"role": "user", "content": user_input})
        current_cards = self._coerce_tension_cards(tension_cards)
        current_info = copy.deepcopy(extracted_info or default_extracted_info())

        extract_task = asyncio.create_task(self._extract_information(
            user_input=user_input,
            conversation_history=history,
            existing_info=copy.deepcopy(current_info),
        ))
        depth_task = asyncio.create_task(self._depth_evaluator.evaluate(
            history,
            depth_evaluations,
            current_layer=current_layer,
            layer_round_count=layer_round_count,
            is_containment=is_containment,
            tension_cards=current_cards,
        ))
        plan_task = asyncio.create_task(self._intent_planner.plan(
            history=history,
            tension_cards=current_cards,
            current_layer=current_layer,
            latest_extracted_info=current_info,
            current_focus_id=focus_card_id,
        ))
        merged_info, evaluation, planned = await asyncio.gather(extract_task, depth_task, plan_task)

        new_round = round_count + 1
        intent_plan = dict(planned) if isinstance(planned, dict) else {}
        next_focus_trace = self._record_answered_focus(
            focus_trace=focus_trace,
            previous_focus_card_id=focus_card_id,
            previous_round=round_count,
            user_input=user_input,
            semantic_relevance=intent_plan.get("focus_answer_relevant"),
        )
        if evaluation.recommended_action != "close" and intent_plan.get("intent") == "hand_off":
            intent_plan["intent"] = "probe_unspoken"

        updated_cards = self._tension_tracker.ingest(current_cards, user_input, new_round, merged_info)
        if depth_evaluations and getattr(depth_evaluations[-1], "strategy_hint", "") == "self_statement_request":
            poles = _parse_self_statement_poles(user_input)
            if poles is not None:
                updated_cards = self._tension_tracker.backfill_poles(
                    updated_cards, poles[0], poles[1], new_round
                )
        evaluation.unattended_card_ids = [
            card.id for card in self._tension_tracker.unattended(updated_cards, new_round)
        ]

        self_statement_already_requested = any(
            getattr(prior, "strategy_hint", "") == "self_statement_request"
            for prior in depth_evaluations
        )
        coverage_probe_on_cooldown = self._coverage_probe_on_cooldown(depth_evaluations)
        meaning_probe_pending = current_layer >= 3 and not any(
            getattr(prior, "coverage_probe", "") == "probe_values"
            for prior in depth_evaluations
        )
        evaluation = self._normalize_evaluation(
            evaluation=evaluation,
            round_count=new_round,
            closing_revert_count=closing_revert_count,
            current_layer=current_layer,
            layer_round_count=layer_round_count,
            is_containment=is_containment,
            containment_round_count=containment_round_count,
            self_statement_already_requested=self_statement_already_requested,
        )
        if evaluation.recommended_action != "close" and intent_plan.get("intent") == "hand_off":
            intent_plan["intent"] = "probe_unspoken"

        focus_card = self._select_focus_card(
            updated_cards=updated_cards,
            new_round=new_round,
            current_layer=current_layer,
            focus_card_id=focus_card_id,
            l1_own_count=l1_own_count,
            focus_trace=next_focus_trace,
        )
        intent_plan = self._move_dispatcher.dispatch(
            plan=intent_plan,
            focus_card=focus_card,
            history=history,
            evaluation=evaluation,
            current_layer=current_layer,
            extracted_info=merged_info,
            coverage_probe_on_cooldown=coverage_probe_on_cooldown,
            meaning_probe_pending=meaning_probe_pending,
        )
        intent_plan = self._bind_plan_to_focus(intent_plan, focus_card, merged_info)
        _move = intent_plan.get("move", "")
        fired_coverage_move = _move if _move in COVERAGE_PROBE_MOVES else ""
        if fired_coverage_move:
            self._coverage_probes += 1
        system_prompt = self._build_chat_system(
            extracted_info=merged_info,
            evaluation=evaluation,
            current_layer=current_layer,
            focus_card=focus_card,
            intent_plan=intent_plan,
            user_display_name=user_display_name,
        )

        yield {
            "type": "turn_start",
            "data": {
                "round": new_round,
                "current_layer": current_layer,
                "focus_tension": focus_card.to_dict() if focus_card is not None else None,
            },
        }

        response = ""
        raw_response = ""
        correction_applied = False
        correction_count = 0
        async for event in self._generate_audited_response_stream(
            history=history,
            evaluation=evaluation,
            intent_plan=intent_plan,
            system_prompt=system_prompt,
        ):
            event_type = event.get("type")
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            if event_type == "delta":
                yield {"type": "assistant_token", "data": {"content": str(data.get("content") or "")}}
            elif event_type == "correction":
                yield {
                    "type": "assistant_correction",
                    "data": {
                        "reason": str(data.get("reason") or ""),
                        "discard_prior": True,
                    },
                }
            elif event_type == "complete":
                response = str(data.get("response") or "")
                raw_response = str(data.get("raw_response") or "")
                correction_applied = bool(data.get("correction_applied", False))
                correction_count = int(data.get("correction_count", 0) or 0)

        history.append({"role": "assistant", "content": response})

        should_continue = evaluation.recommended_action != "close"
        outcome = None
        if not should_continue:
            outcome = await self._outcome_extractor.extract(filter_process_turns(history), depth_evaluations + [evaluation])

        next_state = self._next_layer_state(
            evaluation=evaluation,
            current_layer=current_layer,
            layer_round_count=layer_round_count,
            containment_round_count=containment_round_count,
            intent_plan=intent_plan,
            tension_cards=updated_cards,
            l1_own_count=l1_own_count,
            tension_probed_seen=tension_probed_seen,
            consecutive_intervene_count=consecutive_intervene_count,
        )
        new_evaluations = list(depth_evaluations) + [
            self._copy_evaluation(
                evaluation,
                depth_layer=next_state["current_layer"],
                coverage_probe=fired_coverage_move,
            )
        ]
        if focus_card is not None:
            updated_cards = self._tension_tracker.record_focus(updated_cards, focus_card.id, new_round)
        next_state["focus_trace"] = next_focus_trace

        final_evaluation = new_evaluations[-1]
        depth_payload = final_evaluation.to_dict()
        depth_payload["current_layer"] = final_evaluation.depth_layer
        yield {
            "type": "turn_end",
            "data": {
                "response": response,
                "raw_response": raw_response,
                "correction_applied": correction_applied,
                "correction_count": correction_count,
                "should_continue": should_continue,
                "round": new_round,
                "extracted_info": merged_info,
                "conversation_history": history,
                "depth_evaluations": [item.to_dict() for item in new_evaluations],
                "depth": depth_payload,
                "next_state": next_state,
                "elicitation_outcome": outcome.to_dict() if outcome is not None else None,
                "tension_cards": [card.to_dict() for card in updated_cards],
                "focus_card_id": focus_card.id if focus_card is not None else None,
            },
        }

    async def _plan_and_generate(
        self,
        *,
        user_input: str,
        conversation_history: list[dict],
        extracted_info: dict,
        round_count: int,
        depth_evaluations: list[DepthEvaluation],
        closing_revert_count: int,
        current_layer: int,
        layer_round_count: int,
        is_containment: bool,
        containment_round_count: int,
        tension_cards: list[dict[str, Any] | TensionCard] | None,
        focus_card_id: str | None,
        l1_own_count: int,
        tension_probed_seen: bool,
        user_display_name: str | None,
        consecutive_intervene_count: int = 0,
        focus_trace: list[dict[str, Any]] | None = None,
        stream_first: bool = False,
    ) -> dict[str, Any]:
        current_layer = _normalize_active_layer(current_layer)
        history = list(conversation_history)
        history.append({"role": "user", "content": user_input})
        current_cards = self._coerce_tension_cards(tension_cards)
        current_info = copy.deepcopy(extracted_info or default_extracted_info())

        extract_task = asyncio.create_task(self._extract_information(
            user_input=user_input,
            conversation_history=history,
            existing_info=copy.deepcopy(current_info),
        ))
        depth_task = asyncio.create_task(self._depth_evaluator.evaluate(
            history,
            depth_evaluations,
            current_layer=current_layer,
            layer_round_count=layer_round_count,
            is_containment=is_containment,
            tension_cards=current_cards,
        ))
        plan_task = asyncio.create_task(self._intent_planner.plan(
            history=history,
            tension_cards=current_cards,
            current_layer=current_layer,
            latest_extracted_info=current_info,
            current_focus_id=focus_card_id,
        ))
        merged_info, evaluation, planned = await asyncio.gather(extract_task, depth_task, plan_task)

        new_round = round_count + 1
        intent_plan = dict(planned) if isinstance(planned, dict) else {}
        next_focus_trace = self._record_answered_focus(
            focus_trace=focus_trace,
            previous_focus_card_id=focus_card_id,
            previous_round=round_count,
            user_input=user_input,
            semantic_relevance=intent_plan.get("focus_answer_relevant"),
        )
        if evaluation.recommended_action != "close" and intent_plan.get("intent") == "hand_off":
            intent_plan["intent"] = "probe_unspoken"

        updated_cards = self._tension_tracker.ingest(current_cards, user_input, new_round, merged_info)
        if depth_evaluations and getattr(depth_evaluations[-1], "strategy_hint", "") == "self_statement_request":
            poles = _parse_self_statement_poles(user_input)
            if poles is not None:
                updated_cards = self._tension_tracker.backfill_poles(
                    updated_cards, poles[0], poles[1], new_round
                )
        evaluation.unattended_card_ids = [
            card.id for card in self._tension_tracker.unattended(updated_cards, new_round)
        ]

        self_statement_already_requested = any(
            getattr(prior, "strategy_hint", "") == "self_statement_request"
            for prior in depth_evaluations
        )
        coverage_probe_on_cooldown = self._coverage_probe_on_cooldown(depth_evaluations)
        meaning_probe_pending = current_layer >= 3 and not any(
            getattr(prior, "coverage_probe", "") == "probe_values"
            for prior in depth_evaluations
        )
        evaluation = self._normalize_evaluation(
            evaluation=evaluation,
            round_count=new_round,
            closing_revert_count=closing_revert_count,
            current_layer=current_layer,
            layer_round_count=layer_round_count,
            is_containment=is_containment,
            containment_round_count=containment_round_count,
            self_statement_already_requested=self_statement_already_requested,
        )
        if evaluation.recommended_action != "close" and intent_plan.get("intent") == "hand_off":
            intent_plan["intent"] = "probe_unspoken"

        new_evaluations = list(depth_evaluations) + [evaluation]
        focus_card = self._select_focus_card(
            updated_cards=updated_cards,
            new_round=new_round,
            current_layer=current_layer,
            focus_card_id=focus_card_id,
            l1_own_count=l1_own_count,
            focus_trace=next_focus_trace,
        )
        intent_plan = self._move_dispatcher.dispatch(
            plan=intent_plan,
            focus_card=focus_card,
            history=history,
            evaluation=evaluation,
            current_layer=current_layer,
            extracted_info=merged_info,
            coverage_probe_on_cooldown=coverage_probe_on_cooldown,
            meaning_probe_pending=meaning_probe_pending,
        )
        intent_plan = self._bind_plan_to_focus(intent_plan, focus_card, merged_info)
        _move = intent_plan.get("move", "")
        fired_coverage_move = _move if _move in COVERAGE_PROBE_MOVES else ""
        if fired_coverage_move:
            self._coverage_probes += 1
        system_prompt = self._build_chat_system(
            extracted_info=merged_info,
            evaluation=evaluation,
            current_layer=current_layer,
            focus_card=focus_card,
            intent_plan=intent_plan,
            user_display_name=user_display_name,
        )
        response, raw_response = await self._generate_audited_response(
            history=history,
            evaluation=evaluation,
            intent_plan=intent_plan,
            system_prompt=system_prompt,
            stream_first=stream_first,
        )
        history.append({"role": "assistant", "content": response})

        should_continue = evaluation.recommended_action != "close"
        outcome = None
        if not should_continue:
            outcome = await self._outcome_extractor.extract(filter_process_turns(history), new_evaluations)

        next_state = self._next_layer_state(
            evaluation=evaluation,
            current_layer=current_layer,
            layer_round_count=layer_round_count,
            containment_round_count=containment_round_count,
            intent_plan=intent_plan,
            tension_cards=updated_cards,
            l1_own_count=l1_own_count,
            tension_probed_seen=tension_probed_seen,
            consecutive_intervene_count=consecutive_intervene_count,
        )
        new_evaluations[-1] = self._copy_evaluation(
            evaluation,
            depth_layer=next_state["current_layer"],
            coverage_probe=fired_coverage_move,
        )
        if focus_card is not None:
            updated_cards = self._tension_tracker.record_focus(updated_cards, focus_card.id, new_round)
        next_state["focus_trace"] = next_focus_trace

        return {
            "response": response,
            "raw_response": raw_response,
            "should_continue": should_continue,
            "merged_info": merged_info,
            "history": history,
            "new_evaluations": new_evaluations,
            "outcome": outcome,
            "next_state": next_state,
            "new_round": new_round,
            "current_layer": current_layer,
            "focus_card": focus_card,
            "tension_state": {
                "tension_cards": [card.to_dict() for card in updated_cards],
                "focus_card_id": focus_card.id if focus_card is not None else None,
                "l1_own_count": next_state["l1_own_count"],
                "tension_probed_seen": next_state["tension_probed_seen"],
            },
        }

    def _normalize_evaluation(
        self,
        evaluation: DepthEvaluation,
        round_count: int,
        closing_revert_count: int,
        current_layer: int,
        is_containment: bool,
        containment_round_count: int,
        layer_round_count: int = 0,
        self_statement_already_requested: bool = False,
    ) -> DepthEvaluation:
        normalized = self._copy_evaluation(
            evaluation,
            depth_layer=current_layer,
        )

        if round_count < MIN_ROUNDS and normalized.recommended_action in {"prepare_closing", "close"}:
            normalized = self._copy_evaluation(
                normalized,
                recommended_action="continue",
                strategy_hint=f"layer_{current_layer}",
                depth_layer=current_layer,
            )

        if (
            normalized.recommended_action == "close"
            and round_count < ABSOLUTE_MAX_ROUNDS
            and normalized.unattended_card_ids
        ):
            normalized = self._copy_evaluation(
                normalized,
                recommended_action="continue",
                strategy_hint="confront_unattended",
                depth_layer=current_layer,
            )
            self._close_gate_blocks += 1

        if round_count >= ABSOLUTE_MAX_ROUNDS:
            return self._copy_evaluation(
                normalized,
                readiness_score=max(normalized.readiness_score, 0.8),
                recommended_action="close",
                strategy_hint="closing",
                depth_layer=current_layer,
            )

        if round_count >= HARD_CLOSE_ROUND:
            if current_layer < 3:
                logger.warning(
                    "Elicitation hard close forced before layer 3",
                    extra={"round_count": round_count, "current_layer": current_layer},
                )
            return self._copy_evaluation(
                normalized,
                readiness_score=max(normalized.readiness_score, 0.8),
                recommended_action="close",
                strategy_hint="closing",
                depth_layer=current_layer,
            )

        if current_layer < 3 and normalized.recommended_action in {"prepare_closing", "close"}:
            normalized = self._copy_evaluation(
                normalized,
                recommended_action="continue",
                strategy_hint=f"layer_{current_layer}",
                depth_layer=current_layer,
            )

        if (
            current_layer == 3
            and layer_round_count < L3_MIN_ROUNDS - 1
            and normalized.recommended_action in {"prepare_closing", "close"}
        ):
            normalized = self._copy_evaluation(
                normalized,
                recommended_action="continue",
                strategy_hint="layer_3",
                depth_layer=current_layer,
            )

        if (
            normalized.recommended_action in {"close", "prepare_closing"}
            and round_count < ABSOLUTE_MAX_ROUNDS
            and not normalized.tension_owned
            and normalized.depth_score >= 0.6
            and not self_statement_already_requested
        ):
            normalized = self._copy_evaluation(
                normalized,
                recommended_action="continue",
                strategy_hint="self_statement_request",
                depth_layer=current_layer,
            )
            self._self_statement_requests += 1

        # Mid-session window: a visible-but-unowned tension by round 6 gets one
        # first-person naming request without waiting for the close boundary
        # (session 380950ab never reached the old close-only window and shipped
        # all cards with empty poles).
        if (
            not self_statement_already_requested
            and current_layer >= 2
            and normalized.recommended_action == "continue"
            and normalized.strategy_hint
            not in {"containment", "closing", "self_statement_request", "confront_unattended"}
            and normalized.emotional_state != "intense"
            and round_count >= SELF_STATEMENT_MIN_ROUND
            and normalized.tension_visible
            and not normalized.tension_owned
        ):
            normalized = self._copy_evaluation(
                normalized,
                strategy_hint="self_statement_request",
                depth_layer=current_layer,
            )
            self._self_statement_requests += 1

        if (
            round_count >= SOFT_TARGET_ROUND
            and current_layer >= 3
            and layer_round_count >= L3_MIN_ROUNDS - 1
            and normalized.recommended_action == "continue"
            and normalized.strategy_hint != "self_statement_request"
        ):
            normalized = self._copy_evaluation(
                normalized,
                recommended_action="prepare_closing",
                strategy_hint="closing",
                depth_layer=current_layer,
            )

        if closing_revert_count > 0 and normalized.recommended_action == "prepare_closing":
            normalized = self._copy_evaluation(
                normalized,
                recommended_action="continue",
                strategy_hint=f"layer_{current_layer}",
                depth_layer=current_layer,
            )

        if normalized.emotional_state == "intense" and containment_round_count < MAX_CONTAINMENT_ROUNDS:
            normalized = self._copy_evaluation(
                normalized,
                recommended_action="continue",
                strategy_hint="containment",
                depth_layer=current_layer,
            )

        if containment_round_count >= MAX_CONTAINMENT_ROUNDS and (
            is_containment or normalized.strategy_hint == "containment"
        ):
            normalized = self._copy_evaluation(
                normalized,
                recommended_action="continue",
                strategy_hint=f"layer_{current_layer}",
                depth_layer=current_layer,
            )

        return normalized

    def _is_useful_focus_answer(
        self,
        user_input: str,
        *,
        semantic_relevance: object = None,
    ) -> bool:
        text = " ".join(str(user_input or "").split())
        thin_answers = {
            "是",
            "是的",
            "对",
            "嗯",
            "哦",
            "好",
            "一件产品吧",
            "不知道",
            "说不清",
            "没想过",
        }
        if text in thin_answers:
            return False
        if semantic_relevance is False:
            return False
        if semantic_relevance is True:
            return len(text.strip("。！？!?，, ")) >= 2
        return len(text.strip("。！？!?，, ")) >= 8

    def _record_answered_focus(
        self,
        *,
        focus_trace: list[dict[str, Any]] | None,
        previous_focus_card_id: str | None,
        previous_round: int,
        user_input: str,
        semantic_relevance: object,
    ) -> list[dict[str, Any]]:
        """Attach a user answer to the focus of the question it actually follows."""
        trace = [dict(item) for item in (focus_trace or []) if isinstance(item, dict)]
        if not previous_focus_card_id or previous_round <= 0:
            return trace

        entry = {
            "round": previous_round,
            "card_id": previous_focus_card_id,
            "useful": self._is_useful_focus_answer(
                user_input,
                semantic_relevance=semantic_relevance,
            ),
        }
        for index in range(len(trace) - 1, -1, -1):
            existing = trace[index]
            if (
                existing.get("round") == previous_round
                and existing.get("card_id") == previous_focus_card_id
            ):
                trace[index] = entry
                break
        else:
            trace.append(entry)
        return trace

    def _bind_plan_to_focus(
        self,
        plan: dict[str, Any],
        focus_card: TensionCard | None,
        extracted_info: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Make the scheduler's selected focus authoritative for generation and audit."""
        bound = dict(plan)
        planned_quote = str(bound.get("focus_quote") or "").strip()
        if focus_card is not None:
            selected_quote = str(focus_card.raw_quote or "").strip()
            if planned_quote and planned_quote != selected_quote:
                bound["latest_evidence_quote"] = planned_quote
            bound["focus_card_id"] = focus_card.id
            bound["focus_quote"] = selected_quote
            bound["focus_kind"] = focus_card.kind

        core_dilemma = str((extracted_info or {}).get("core_dilemma") or "").strip()
        if core_dilemma:
            bound["core_dilemma"] = core_dilemma
        return bound

    def _select_focus_card(
        self,
        *,
        updated_cards: list[TensionCard],
        new_round: int,
        current_layer: int,
        focus_card_id: str | None,
        l1_own_count: int,
        focus_trace: list[dict[str, Any]] | None,
    ) -> TensionCard | None:
        selected = self._tension_tracker.select_sampling_focus(
            updated_cards,
            new_round,
            focus_trace=focus_trace,
            current_focus_id=focus_card_id,
        )
        if selected is None:
            return None
        if current_layer in {2, 3}:
            return selected
        # Layer 1: round 1 stays pure narrative grounding (no card); from round 2 on,
        # expose the focus card so name_both_poles can surface the tension early.
        if current_layer == 1 and new_round >= 2:
            return selected
        return None

    def _copy_evaluation(self, evaluation: DepthEvaluation, **overrides: Any) -> DepthEvaluation:
        payload = evaluation.to_dict()
        payload.update(overrides)
        return DepthEvaluation.from_dict(payload)

    def _coverage_probe_on_cooldown(self, depth_evaluations: list[DepthEvaluation]) -> bool:
        if not depth_evaluations:
            return False
        return getattr(depth_evaluations[-1], "coverage_probe", "") in COVERAGE_PROBE_MOVES

    def metrics(self) -> dict[str, int]:
        return {
            "close_gate_blocks": self._close_gate_blocks,
            "l1_graduate_blocked_by_tension": self._l1_graduate_blocked_by_tension,
            "l2_graduate_blocked_by_probed_count": self._l2_graduate_blocked_by_probed_count,
            "self_statement_requests": self._self_statement_requests,
            "coverage_probes": self._coverage_probes,
        }

    def _coerce_tension_cards(self, cards: list[dict[str, Any] | TensionCard] | None) -> list[TensionCard]:
        if not isinstance(cards, list):
            return []

        normalized: list[TensionCard] = []
        for item in cards:
            if isinstance(item, TensionCard):
                candidate = TensionCard.from_dict(item.to_dict())
            elif isinstance(item, dict):
                candidate = TensionCard.from_dict(item)
            else:
                continue
            if candidate.id and candidate.raw_quote:
                normalized.append(candidate)
        return normalized

    def _next_layer_state(
        self,
        evaluation: DepthEvaluation,
        current_layer: int,
        layer_round_count: int,
        containment_round_count: int,
        intent_plan: dict[str, Any] | None = None,
        tension_cards: list[dict[str, Any] | TensionCard] | None = None,
        l1_own_count: int = 0,
        tension_probed_seen: bool = False,
        consecutive_intervene_count: int = 0,
    ) -> dict[str, Any]:
        current_layer = _normalize_active_layer(current_layer)
        plan = intent_plan if isinstance(intent_plan, dict) else {}
        next_l1_own_count = max(int(l1_own_count or 0), 0)
        if current_layer == 1 and plan.get("user_turn_kind") == "own_a_struggle":
            next_l1_own_count += 1

        coerced_cards = self._coerce_tension_cards(tension_cards)
        next_probed_seen = bool(tension_probed_seen) or any(
            card.status in {"probed", "layered", "saturated"} for card in coerced_cards
        )

        if plan.get("user_turn_kind") == "clarify_request" or plan.get("intent") == "clarify_back":
            return {
                "current_layer": current_layer,
                "layer_round_count": layer_round_count,
                "is_containment": False,
                "containment_round_count": 0,
                "l1_own_count": next_l1_own_count,
                "tension_probed_seen": next_probed_seen,
                "consecutive_intervene_count": 0,
            }
        probed_card_count = sum(
            1 for card in coerced_cards
            if card.status in {"probed", "layered", "saturated"}
        )

        if evaluation.strategy_hint == "containment":
            return {
                "current_layer": current_layer,
                "layer_round_count": layer_round_count,
                "is_containment": True,
                "containment_round_count": containment_round_count + 1,
                "l1_own_count": next_l1_own_count,
                "tension_probed_seen": next_probed_seen,
                "consecutive_intervene_count": 0,
            }

        points_to_layer_up = (
            evaluation.recommended_action == "intervene"
            or (
                isinstance(evaluation.strategy_hint, str)
                and evaluation.strategy_hint.startswith("layer_")
                and evaluation.strategy_hint != f"layer_{current_layer}"
            )
        )
        next_intervene_count = (consecutive_intervene_count + 1) if points_to_layer_up else 0

        next_layer = current_layer
        next_layer_round_count = layer_round_count + 1

        hard_graduate = (
            evaluation.graduation_ready
            and evaluation.recommended_action != "close"
            and evaluation.strategy_hint != "containment"
            and (
                current_layer != 1
                or (evaluation.tension_visible and evaluation.tension_owned)
            )
            and (
                current_layer != 2
                or probed_card_count >= 2
            )
        )
        soft_graduate_l1_to_l2 = (
            current_layer == 1
            and evaluation.readiness_score >= 0.80
            and evaluation.depth_score >= 0.65
            and layer_round_count >= L1_MIN_ROUNDS - 1
            and next_l1_own_count >= 1
            and evaluation.tension_visible
            and evaluation.tension_owned
        )
        soft_graduate_l2_to_l3 = (
            current_layer == 2
            and evaluation.readiness_score >= 0.80
            and evaluation.depth_score >= 0.70
            and layer_round_count >= L2_MIN_ROUNDS - 1
            and probed_card_count >= 2
        )
        forced_graduate = next_intervene_count >= 2
        # layer_round_count is 0-indexed rounds-already-completed at this layer, so
        # `>= L1_MAX_ROUNDS - 1` fires on the LAST allowed L1 round (e.g. index 2 when
        # the cap is 3). This is a maximum ceiling, unlike the `*_MIN_ROUNDS - 1` checks
        # elsewhere which gate a minimum. Forces L1->L2 even if the user never owned a tension.
        forced_l1_timeout = (
            current_layer == 1
            and layer_round_count >= L1_MAX_ROUNDS - 1
            and evaluation.recommended_action not in {"prepare_closing", "close"}
            and evaluation.strategy_hint != "containment"
        )

        can_graduate = (
            hard_graduate
            or soft_graduate_l1_to_l2
            or soft_graduate_l2_to_l3
            or forced_graduate
            or forced_l1_timeout
        )

        if (
            current_layer == 1
            and not (evaluation.tension_visible and evaluation.tension_owned)
            and (
                evaluation.graduation_ready
                or (
                    evaluation.readiness_score >= 0.80
                    and evaluation.depth_score >= 0.65
                    and layer_round_count >= L1_MIN_ROUNDS - 1
                    and next_l1_own_count >= 1
                )
            )
        ):
            self._l1_graduate_blocked_by_tension += 1

        if (
            current_layer == 2
            and probed_card_count < 2
            and (
                evaluation.graduation_ready
                or (
                    evaluation.readiness_score >= 0.80
                    and evaluation.depth_score >= 0.70
                    and layer_round_count >= L2_MIN_ROUNDS - 1
                )
            )
        ):
            self._l2_graduate_blocked_by_probed_count += 1

        if (
            can_graduate
            and current_layer == 1
            and (
                forced_graduate
                or forced_l1_timeout
                or (layer_round_count >= L1_MIN_ROUNDS - 1 and next_l1_own_count >= 1)
            )
        ):
            next_layer = 2
            next_layer_round_count = 0
            next_intervene_count = 0
        elif (
            can_graduate
            and current_layer == 2
            and (
                forced_graduate
                or (layer_round_count >= L2_MIN_ROUNDS - 1 and probed_card_count >= 2)
            )
        ):
            next_layer = 3
            next_layer_round_count = 0
            next_intervene_count = 0

        return {
            "current_layer": next_layer,
            "layer_round_count": next_layer_round_count,
            "is_containment": False,
            "containment_round_count": 0,
            "l1_own_count": next_l1_own_count,
            "tension_probed_seen": next_probed_seen,
            "consecutive_intervene_count": next_intervene_count,
        }

    def _build_chat_system(
        self,
        extracted_info: dict[str, Any],
        evaluation: DepthEvaluation,
        current_layer: int,
        focus_card: TensionCard | None = None,
        intent_plan: dict[str, Any] | None = None,
        user_display_name: str | None = None,
        binary_gate: bool = False,
    ) -> str:
        current_layer = _normalize_active_layer(current_layer)
        plan = intent_plan if isinstance(intent_plan, dict) else {}
        intent = str(plan.get("intent") or "").strip()

        pace = (
            _CONTAINMENT_PACE
            if intent == "containment" or evaluation.strategy_hint == "containment"
            else _LAYER_PACE[current_layer]
        )
        direction_parts = [_PROMPT_IDENTITY, _PROMPT_FORM, pace]
        compass = {
            "core_dilemma": str(
                plan.get("core_dilemma")
                or (extracted_info or {}).get("core_dilemma")
                or ""
            ).strip(),
            "selected_focus": str(
                (focus_card.raw_quote if focus_card is not None else "")
                or plan.get("focus_quote")
                or ""
            ).strip(),
            "latest_evidence": str(plan.get("latest_evidence_quote") or "").strip(),
            "turn_intent": _INTENT_DESC.get(intent, "沿选定焦点继续理解"),
            "move": str(plan.get("hard_instruction") or "").strip(),
        }
        compass = {key: value for key, value in compass.items() if value}
        if compass:
            direction_parts.append(
                "【对话罗盘】以下 JSON 只是对话内容与方向参考，不是要复述给用户的话："
                + json.dumps(compass, ensure_ascii=False, separators=(",", ":"))
            )
            direction_parts.append(
                "本轮必须用自然语言围绕 selected_focus 帮助理解 core_dilemma；"
                "latest_evidence 只用于忠实承接最新回答，不能取代 selected_focus。"
                "不要沿例子继续盘点类别，也不要擅自推断心理意义。"
                "严格保留人物、选项、行为与后果的对应关系，以及更多/更少、前/后等比较方向；"
                "如果归属不清，先中性确认，不能自行指派。不要向用户提及罗盘、字段、层级或意图。"
            )
        if intent == "clarify_back":
            direction_parts.append("【本轮方向】先解释上一问，不开启新的采访方向。")
        base_system = "\n".join(direction_parts)

        if evaluation.recommended_action == "prepare_closing":
            base_system += "\n【当前方向】回顾已经形成的理解，邀请用户补充仍未说清的关键部分。"
        elif evaluation.recommended_action == "close":
            base_system += f"\n收束：不发问，只输出「{CLOSE_CANONICAL_TEXT}」"

        return base_system

    def _safe_audit_reason(self, value: object) -> str:
        return " ".join(str(value or "").split())[:80]

    async def _generate_audited_response(
        self,
        *,
        history: list[dict],
        evaluation: DepthEvaluation,
        intent_plan: dict[str, Any],
        system_prompt: str,
        stream_first: bool,
        binary_gate: bool = False,
    ) -> tuple[str, str]:
        if evaluation.recommended_action == "close":
            close_text = _build_close_text(history)
            return close_text, close_text

        intent = str(intent_plan.get("intent") or "").strip()
        retry_feedback = ""
        first_raw_response = ""
        last_question_raw = ""
        direction_failed = False
        audit_failed = False
        fact_failed = False

        for attempt in range(3):
            prompt = system_prompt + retry_feedback
            if stream_first and attempt == 0:
                chunks: list[str] = []
                async for chunk in self._llm_chat_stream(history, prompt):
                    if chunk:
                        chunks.append(str(chunk))
                raw_response = strip_markdown("".join(chunks)).strip()
                first_raw_response = raw_response
            else:
                raw_response = strip_markdown(await self._llm_chat(history, prompt) or "").strip()
                if attempt == 0:
                    first_raw_response = raw_response

            if "？" in raw_response or "?" in raw_response:
                last_question_raw = raw_response

            candidate = _post_process_main_output(raw_response, evaluation, intent=intent)
            if not candidate:
                retry_feedback = _FIXED_FORM_FEEDBACK
                continue

            if intent not in {"clarify_back", "containment"} and _drifts_into_decision_or_planning(candidate, evaluation):
                direction_failed = True
                if attempt < 2:
                    retry_feedback = _DIRECTION_RETRY_FEEDBACK
                    continue
                break

            verdict = await self._turn_auditor.audit(
                candidate_text=candidate,
                plan=intent_plan,
                history=history,
            )
            if str(verdict.get("verdict") or "").strip() != "rewrite":
                return candidate, first_raw_response

            audit_failed = True
            fact_failed = fact_failed or verdict.get("fact_consistent") is False
            reason = self._safe_audit_reason(verdict.get("reason"))
            retry_feedback = (
                f"\n【改写反馈】审核理由：{reason or '候选未通过意图或形式检查'}。"
                "请遵循本轮意图重新生成。"
            )

        if direction_failed:
            degraded = _build_direction_fallback(history)
        elif audit_failed:
            # Never ship a form-valid turn that the semantic/factual audit rejected.
            degraded = _build_audit_fallback(fact_failed=fact_failed)
        else:
            degraded = _salvage_degraded_output(
                last_question_raw or first_raw_response, evaluation, intent=intent
            )
        return degraded, first_raw_response

    async def _generate_audited_response_stream(
        self,
        *,
        history: list[dict],
        evaluation: DepthEvaluation,
        intent_plan: dict[str, Any],
        system_prompt: str,
        binary_gate: bool = False,
    ) -> AsyncIterator[dict[str, Any]]:
        if evaluation.recommended_action == "close":
            close_text = _build_close_text(history)
            yield {"type": "delta", "data": {"content": close_text}}
            yield {
                "type": "complete",
                "data": {
                    "response": close_text,
                    "raw_response": close_text,
                    "correction_applied": False,
                    "correction_count": 0,
                },
            }
            return

        intent = str(intent_plan.get("intent") or "").strip()
        correction_count = 0
        last_raw_response = ""
        last_question_raw = ""
        retry_feedback = ""
        direction_failed = False
        audit_failed = False
        fact_failed = False

        # All usable attempts are audited. A rewritten retry must not bypass factual or
        # focus checks merely because it is the second streamed candidate.
        for attempt in range(3):
            chunks: list[str] = []
            async for chunk in self._llm_chat_stream(history, system_prompt + retry_feedback):
                if not chunk:
                    continue
                text = str(chunk)
                chunks.append(text)
                yield {"type": "delta", "data": {"content": text}}

            raw_response = strip_markdown("".join(chunks)).strip()
            last_raw_response = raw_response
            if "？" in raw_response or "?" in raw_response:
                last_question_raw = raw_response
            candidate = _post_process_main_output(raw_response, evaluation, intent=intent)

            if not candidate:
                if attempt < 2:
                    correction_count += 1
                    retry_feedback = _FIXED_FORM_FEEDBACK
                    yield {"type": "correction", "data": {"reason": "形式约束不通过"}}
                    continue
                break  # final attempt also failed the form check -> fallback

            if intent not in {"clarify_back", "containment"} and _drifts_into_decision_or_planning(candidate, evaluation):
                direction_failed = True
                correction_count += 1
                yield {"type": "correction", "data": {"reason": "访谈方向偏移"}}
                if attempt < 2:
                    retry_feedback = _DIRECTION_RETRY_FEEDBACK
                    continue
                break

            verdict = await self._turn_auditor.audit(
                candidate_text=candidate,
                plan=intent_plan,
                history=history,
            )
            if str(verdict.get("verdict") or "").strip() != "rewrite":
                yield {
                    "type": "complete",
                    "data": {
                        "response": candidate,
                        "raw_response": raw_response,
                        "correction_applied": correction_count > 0,
                        "correction_count": correction_count,
                    },
                }
                return

            audit_failed = True
            fact_failed = fact_failed or verdict.get("fact_consistent") is False
            correction_count += 1
            reason = self._safe_audit_reason(verdict.get("reason"))
            retry_feedback = (
                f"\n【改写反馈】审核理由：{reason or '候选未通过意图或形式检查'}。"
                "请遵循本轮意图重新生成。"
            )
            yield {"type": "correction", "data": {"reason": reason or "audit_rewrite"}}

        if direction_failed:
            degraded = _build_direction_fallback(history)
        elif audit_failed:
            # Streaming retries use the same semantic/factual gate as the first attempt.
            degraded = _build_audit_fallback(fact_failed=fact_failed)
        else:
            degraded = _salvage_degraded_output(
                last_question_raw or last_raw_response, evaluation, intent=intent
            )
        yield {
            "type": "complete",
            "data": {
                "response": degraded,
                "raw_response": last_raw_response,
                "correction_applied": True,
                "correction_count": correction_count + 1,
            },
        }

    async def _extract_information(
        self,
        user_input: str,
        conversation_history: list[dict],
        existing_info: dict[str, Any],
    ) -> dict[str, Any]:
        info = copy.deepcopy(existing_info or default_extracted_info())

        try:
            recent_lines = []
            for message in conversation_history[-8:]:
                role = "用户" if message.get("role") == "user" else "引导者"
                recent_lines.append(f"{role}: {message.get('content', '')}")
            prompt = f"""从以下对话中只提取新出现的信息，返回 JSON：

最近对话：
{chr(10).join(recent_lines)}

已有信息：
{json.dumps(info, ensure_ascii=False)}

字段：
{EXTRACTION_FIELD_SPEC}
- deltas: [
    {{
      "card_id": "optional stable id",
      "kind": "bipolar | undecided | tangled",
      "raw_quote": "exact user-language fragment, never paraphrase",
      "pole_a": "for bipolar only, exact user fragment",
      "pole_b": "for bipolar only, exact user fragment",
      "candidates": ["for undecided only, exact user fragments"],
      "threads": ["for tangled only, two or more exact user fragments"],
      "intensity_hint": 0.0,
      "layers": [{{"description":"", "user_language":""}}]
    }}
  ]

deltas 约束：
- raw_quote must be an exact user-language fragment copied from the user, not a paraphrase.
- Do not invent oppositions or fabricated poles.
- kind=bipolar only when the user's language explicitly contains both sides of a tension; only bipolar cards may include pole_a/pole_b.
- kind=undecided is for unresolved choices such as several options, whether-or-not language, or not knowing what to choose; candidates must be exact user fragments.
- If the user gives a single unresolved option, put one item in candidates rather than faking a pole_a/pole_b pair.
- kind=tangled is for multiple interwoven threads; threads must contain at least 2 exact user fragments.
- card_id should be stable when the same raw_quote reappears; omit it if unsure.
- intensity_hint is optional, 0.0-1.0, only when the user's language shows intensity.
- layers is optional and must also use exact user-language fragments in user_language.

如果没有新信息，请返回 deltas: []。只返回 JSON。"""
            raw = await self._llm(prompt, "你只输出合法 JSON，不要解释。")
            parsed = self._parse_json(raw)
            if parsed:
                self._merge_info(info, parsed)
                return info
        except Exception:
            pass

        return info

    def _merge_info(self, target: dict[str, Any], payload: dict[str, Any]) -> None:
        if payload.get("core_dilemma"):
            target["core_dilemma"] = payload["core_dilemma"]

        if isinstance(payload.get("inner_voices"), list):
            existing = target.get("inner_voices") or []
            for item in payload["inner_voices"]:
                if isinstance(item, dict) and item not in existing:
                    existing.append(item)
            target["inner_voices"] = existing

        for key in ("values", "tactics", "emotions", "stakeholders", "constraints"):
            existing_values = [str(item) for item in (target.get(key) or [])]
            for item in payload.get(key) or []:
                text = str(item).strip()
                if text and text not in existing_values:
                    existing_values.append(text)
            target[key] = existing_values

        if isinstance(payload.get("deltas"), list):
            existing_deltas = target.get("deltas") if isinstance(target.get("deltas"), list) else []
            # Dedup by normalized raw_quote so a sentence the LLM re-emits across
            # rounds keeps only its richest version, instead of piling up copies
            # that pollute the portrait / debate handoff (A3 / RC-0).
            index_by_quote: dict[str, int] = {}
            for pos, existing in enumerate(existing_deltas):
                if isinstance(existing, dict):
                    key = normalize_comparable_text(existing.get("raw_quote") or existing.get("quote"))
                    if key:
                        index_by_quote.setdefault(key, pos)
            for item in payload["deltas"]:
                if not isinstance(item, dict):
                    continue
                raw_quote = str(item.get("raw_quote") or item.get("quote") or "").strip()
                if not raw_quote:
                    continue
                incoming = dict(item)
                key = normalize_comparable_text(raw_quote)
                if key and key in index_by_quote:
                    pos = index_by_quote[key]
                    if self._delta_completeness(incoming) > self._delta_completeness(existing_deltas[pos]):
                        existing_deltas[pos] = incoming
                else:
                    if key:
                        index_by_quote[key] = len(existing_deltas)
                    existing_deltas.append(incoming)
            target["deltas"] = existing_deltas
        elif "deltas" not in target:
            target["deltas"] = []

    @staticmethod
    def _delta_completeness(delta: dict[str, Any]) -> int:
        """Rough fill score so dedup keeps the richest version of a repeated quote."""
        score = 0
        for key in ("pole_a", "pole_b"):
            if str(delta.get(key) or "").strip():
                score += 1
        for key in ("candidates", "threads", "layers"):
            value = delta.get(key)
            if isinstance(value, list):
                score += sum(1 for entry in value if entry)
        return score

    def _parse_json(self, text: str) -> Optional[dict[str, Any]]:
        if not text or not text.strip():
            return None

        try:
            parsed = json.loads(text.strip())
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
