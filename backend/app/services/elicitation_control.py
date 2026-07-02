"""Helpers for separating app-control turns from elicitation content."""

from __future__ import annotations

import re
from typing import Any


_CHINESE_PROCESS_PATTERNS = [
    re.compile(r"(对话|问题|提问).{0,8}(结束|够了|可以了)"),
    re.compile(r"(结束|停止).{0,8}(对话|问题|提问)"),
    re.compile(r"(直接)?进入(下|下一)(轮|步|阶段)"),
    re.compile(r"开始(辩论|讨论)吧?"),
]

_ENGLISH_PROCESS_PATTERNS = [
    re.compile(r"\bno more questions\b"),
    re.compile(r"\bmove (on )?to the next (step|stage|round)\b"),
    re.compile(r"\bgo (on )?to the next (step|stage|round)\b"),
    re.compile(r"\bstart (the )?(debate|discussion)\b"),
]

_CHINESE_SUBSTANTIVE_CUES = (
    "如果",
    "担心",
    "害怕",
    "很怕",
    "关系",
    "工作",
    "辞职",
    "父母",
    "失望",
    "犹豫",
    "是不是",
    "是否",
)

_ENGLISH_SUBSTANTIVE_CUES = (
    "afraid",
    "fear",
    "scared",
    "relationship",
    "job",
    "work",
    "parent",
    "hurt",
    "wondering",
    "whether",
)

_MAX_PROCESS_COMMAND_CHARS = 48
_MAX_PROCESS_COMMAND_WORDS = 10


def _english_token_variants(token: str) -> set[str]:
    variants = {token}
    if token.endswith("ies") and len(token) > 3:
        variants.add(f"{token[:-3]}y")
    if token.endswith("es") and len(token) > 2:
        variants.add(token[:-2])
    if token.endswith("s") and len(token) > 1:
        variants.add(token[:-1])
    if token.endswith("ed") and len(token) > 2:
        variants.add(token[:-2])
    if token.endswith("ing") and len(token) > 3:
        variants.add(token[:-3])
    return variants


def _has_substantive_cue(normalized: str) -> bool:
    english_tokens = {
        variant
        for token in re.findall(r"[a-z]+", normalized)
        for variant in _english_token_variants(token)
    }
    return any(cue in normalized for cue in _CHINESE_SUBSTANTIVE_CUES) or any(
        cue in english_tokens for cue in _ENGLISH_SUBSTANTIVE_CUES
    )


def _is_short_control_turn(normalized: str) -> bool:
    return (
        len(normalized) <= _MAX_PROCESS_COMMAND_CHARS
        and len(normalized.split()) <= _MAX_PROCESS_COMMAND_WORDS
    )


def is_process_intent(text: str) -> bool:
    """Return whether text is a process-only command to advance the app."""

    if not isinstance(text, str):
        return False

    normalized = re.sub(r"\s+", " ", text.strip().lower())
    if not normalized:
        return False

    has_process_phrase = any(pattern.search(normalized) for pattern in _CHINESE_PROCESS_PATTERNS) or any(
        pattern.search(normalized) for pattern in _ENGLISH_PROCESS_PATTERNS
    )
    if not has_process_phrase:
        return False

    return _is_short_control_turn(normalized) and not _has_substantive_cue(normalized)


def filter_process_turns(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove process-only user turns while preserving all other turns."""

    return [
        turn
        for turn in history
        if not (turn.get("role") == "user" and is_process_intent(turn.get("content", "")))
    ]


def build_process_intent_notice(text: str) -> str:
    """Build the Chinese notice shown when a process-control turn is detected."""

    return (
        "我听到你想推进流程。如果想提前结束当前对话，请使用“提前结束对话”。"
        "应用会先检查画像质量是否足够支撑后续环节，再决定是否进入下一轮。"
    )
