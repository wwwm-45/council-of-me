"""Intake LLM supervisor: decide the next-turn intent and focus quote."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable, Optional

from app.models.elicitation import TensionCard, normalize_comparable_text
from app.services.llm import generate as llm_generate

logger = logging.getLogger(__name__)

LlmFn = Callable[..., Awaitable[str]]

ALLOWED_INTENTS = {
    "probe_fact",
    "probe_threshold",
    "probe_cost",
    "probe_fear",
    "probe_value_conflict",
    "probe_identity_gap",
    "probe_unspoken",
    "probe_meaning",
    "probe_self_image",
    "probe_belief",
    "containment",
    "clarify_back",
    "hand_off",
}

LAYER_ALLOWED = {
    1: {"probe_fact", "probe_threshold", "probe_meaning", "containment", "clarify_back"},
    2: {
        "probe_threshold",
        "probe_cost",
        "probe_fear",
        "probe_value_conflict",
        "probe_unspoken",
        "probe_meaning",
        "probe_self_image",
        "probe_belief",
        "containment",
        "clarify_back",
    },
    3: {
        "probe_cost",
        "probe_fear",
        "probe_value_conflict",
        "probe_identity_gap",
        "probe_unspoken",
        "probe_self_image",
        "probe_belief",
        "containment",
        "clarify_back",
        "hand_off",
    },
}

LAYER_FALLBACK_INTENT = {
    1: "probe_fact",
    2: "probe_cost",
    3: "probe_unspoken",
}

ALLOWED_USER_TURN_KINDS = {
    "narrate_fact",
    "own_a_struggle",
    "answer_with_evidence",
    "clarify_request",
    "off_topic",
    "emotional_spike",
}

_INTENT_NEAR_MATCH = {
    "probe_identity_gap": "probe_self_image",
    "probe_value_conflict": "probe_cost",
    "probe_unspoken": "probe_fear",
    "probe_fear": "probe_cost",
    "probe_cost": "probe_threshold",
    "probe_threshold": "probe_fact",
    "probe_meaning": "probe_threshold",
    "probe_self_image": "probe_value_conflict",
    "probe_belief": "probe_meaning",
    "hand_off": "containment",
}


def _parse_json(text: str) -> Optional[dict[str, Any]]:
    if not text or not text.strip():
        return None

    candidates = [text.strip()]
    fenced = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.append(fenced.group(1).strip())

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed

    decoder = json.JSONDecoder()
    for start, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _normalize_layer(value: object) -> int:
    try:
        layer = int(value or 1)
    except (TypeError, ValueError):
        layer = 1
    return min(max(layer, 1), 3)


def _user_messages_window(history: list[dict], n: int = 6) -> list[str]:
    users = [
        str(message.get("content") or "")
        for message in history
        if isinstance(message, dict) and message.get("role") == "user"
    ]
    return users[-n:]


def _focus_quote_is_substring(focus_quote: str, history: list[dict]) -> bool:
    if not (4 <= len(focus_quote) <= 30):
        return False
    if not normalize_comparable_text(focus_quote):
        return False
    return any(focus_quote in content for content in _user_messages_window(history))


# Clause-boundary punctuation only — deliberately excludes space, since a space inside a
# mixed CJK+English phrase ("死磕 'The Council'") is not a clause boundary and clipping
# there strands a half English token ("…死磕 'The").
_QUOTE_BOUNDARY_CHARS = "。．.！!？?，,、；;：:…"


def _clip_quote_at_boundary(text: str, *, lo: int = 4, hi: int = 16) -> str:
    """Clip a grounded quote at a clause boundary so it never cuts mid-phrase.

    A hard char-count slice (the old cleaned[:12]) sliced user text mid-word, e.g.
    "我目前研一，正在为实习和" — which then read as broken when echoed inside a fallback
    question. Prefer the last clause boundary within [lo, hi]; only fall back to a raw
    window when the fragment has no usable boundary.
    """
    if len(text) <= hi:
        return text.rstrip(_QUOTE_BOUNDARY_CHARS) or text
    window = text[:hi]
    boundary = max((window.rfind(char) for char in _QUOTE_BOUNDARY_CHARS), default=-1)
    if boundary >= lo:
        return window[:boundary]
    return window


def _extract_fallback_quote(history: list[dict]) -> str:
    """Pick a grounded, clause-bounded fragment from the newest usable user turn."""
    for content in reversed(_user_messages_window(history)):
        text = content.strip()
        cleaned = re.sub(r"^[\s。？?！!，,、；;：:]+", "", text)
        if len(cleaned) >= 4 and normalize_comparable_text(cleaned):
            return _clip_quote_at_boundary(cleaned)
    return ""


def _clamp_intent(intent: str, layer: int) -> str:
    allowed = LAYER_ALLOWED[layer]
    if intent in allowed:
        return intent
    candidate = _INTENT_NEAR_MATCH.get(intent)
    if candidate in allowed:
        return candidate
    return LAYER_FALLBACK_INTENT[layer]


class IntentPlanner:
    """Decide which intent the next main-agent turn should pursue."""

    def __init__(self, llm_fn: Optional[LlmFn] = None) -> None:
        self._llm = llm_fn or llm_generate

    async def plan(
        self,
        *,
        history: list[dict],
        tension_cards: list[TensionCard | dict],
        current_layer: int,
        latest_extracted_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        layer = _normalize_layer(current_layer)
        prompt = self._build_prompt(
            history,
            tension_cards,
            layer,
            latest_extracted_info or {},
        )
        try:
            raw = await self._llm(
                prompt,
                system="你只输出 JSON，不要解释。",
                temperature=0.1,
                max_tokens=256,
            )
        except Exception:
            logger.exception("IntentPlanner LLM call failed")
            return self._fallback(history=history, layer=layer)

        return self._normalize(_parse_json(raw), history=history, layer=layer)

    def _normalize(
        self,
        parsed: dict[str, Any] | None,
        *,
        history: list[dict],
        layer: int,
    ) -> dict[str, Any]:
        if not parsed:
            return self._fallback(history=history, layer=layer)

        focus_quote = str(parsed.get("focus_quote") or "").strip()
        if not _focus_quote_is_substring(focus_quote, history):
            return self._fallback(history=history, layer=layer)

        intent = str(parsed.get("intent") or "").strip()
        if intent not in ALLOWED_INTENTS:
            intent = LAYER_FALLBACK_INTENT[layer]
        intent = _clamp_intent(intent, layer)

        user_turn_kind = str(parsed.get("user_turn_kind") or "").strip()
        if user_turn_kind not in ALLOWED_USER_TURN_KINDS:
            user_turn_kind = "narrate_fact"

        avoid_quotes: list[str] = []
        seen_avoid: set[str] = set()
        avoid_raw = parsed.get("avoid_quotes")
        if isinstance(avoid_raw, list):
            for item in avoid_raw:
                if len(avoid_quotes) >= 6 or not isinstance(item, str):
                    continue
                text = item.strip()
                comparable = normalize_comparable_text(text)
                if text and comparable and comparable not in seen_avoid:
                    avoid_quotes.append(text)
                    seen_avoid.add(comparable)

        focus_card_id = parsed.get("focus_card_id")
        if not isinstance(focus_card_id, str) or not focus_card_id.strip():
            focus_card_id = None
        else:
            focus_card_id = focus_card_id.strip()

        rationale = str(parsed.get("rationale") or "").strip()[:30]
        return {
            "user_turn_kind": user_turn_kind,
            "intent": intent,
            "focus_quote": focus_quote,
            "focus_card_id": focus_card_id,
            "avoid_quotes": avoid_quotes,
            "rationale": rationale or "planner_ok",
        }

    def _fallback(self, *, history: list[dict], layer: int) -> dict[str, Any]:
        return {
            "user_turn_kind": "narrate_fact",
            "intent": LAYER_FALLBACK_INTENT[layer],
            "focus_quote": _extract_fallback_quote(history),
            "focus_card_id": None,
            "avoid_quotes": [],
            "rationale": "planner_fallback",
        }

    def _build_prompt(
        self,
        history: list[dict],
        tension_cards: list[TensionCard | dict],
        layer: int,
        extracted_info: dict[str, Any],
    ) -> str:
        dialogue = []
        for message in history[-12:]:
            if not isinstance(message, dict):
                continue
            role = "用户" if message.get("role") == "user" else "引导者"
            dialogue.append(f"{role}: {message.get('content', '')}")

        cards = []
        for card in tension_cards[:6]:
            payload = card.to_dict() if isinstance(card, TensionCard) else card
            if not isinstance(payload, dict):
                continue
            cards.append(
                {
                    "id": payload.get("id"),
                    "kind": payload.get("kind"),
                    "status": payload.get("status"),
                    "raw_quote": payload.get("raw_quote"),
                    "last_focus_round": payload.get("last_focus_round"),
                }
            )

        allowed = " / ".join(sorted(LAYER_ALLOWED[layer]))
        context = json.dumps(extracted_info, ensure_ascii=False, default=str)
        return f"""你是意图规划者。读对话、张力卡状态和当前层级，只决定下一轮引导意图与用户原话焦点，不写问题。

只输出 JSON，字段为 user_turn_kind、intent、focus_quote、focus_card_id、avoid_quotes、rationale。
- user_turn_kind: narrate_fact|own_a_struggle|answer_with_evidence|clarify_request|off_topic|emotional_spike
- focus_quote: 最近六条用户消息中的 4-30 字原文子串，不得改写
- avoid_quotes: 已经追问过的片段，最多 6 个
- rationale: 不超过 30 字

意图含义：
- probe_fact：问事实细节
- probe_threshold：问判断标准或边界
- probe_cost：问选择的代价
- probe_fear：问最怕的具体结果
- probe_value_conflict：问两个诉求的拉扯
- probe_identity_gap：问自我表述与行动之间的落差
- probe_unspoken：问尚未说出的部分
- probe_meaning：问这件事对用户意味着什么、触到了什么
- probe_self_image：问"我应当是什么样的人"与实际之间的落差
- probe_belief：问背后的判断、前提或预设
- containment：承接强烈情绪，不推进
- clarify_back：澄清最近的具体说法
- hand_off：停止追问并移交

当前层级：L{layer}；允许意图：{allowed}
卡型提示：bipolar 可关注代价，undecided 可关注边界或担忧，tangled 可关注诉求拉扯。

最近对话：
{chr(10).join(dialogue) or "(空)"}

张力卡：
{json.dumps(cards, ensure_ascii=False)}

既有抽取信息：
{context}
"""
