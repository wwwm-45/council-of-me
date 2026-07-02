"""Shared helpers for enforcing Chinese output on structured LLM paths."""

from __future__ import annotations

import re
from contextvars import ContextVar
from typing import Iterable

CHINESE_OUTPUT_MANDATE = (
    "所有面向用户的说明字段都必须使用中文。"
    "可以保留少量英文专有名词或术语，但不允许整段英文。"
)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_NON_WHITESPACE_RE = re.compile(r"\S")
_COUNTERS: ContextVar[dict[str, int] | None] = ContextVar(
    "language_guard_counters",
    default=None,
)


def chinese_system_prompt(base: str) -> str:
    """Append the shared Chinese-output mandate to an existing system prompt."""
    prefix = (base or "").strip()
    if CHINESE_OUTPUT_MANDATE in prefix:
        return prefix
    if not prefix:
        return CHINESE_OUTPUT_MANDATE
    return f"{prefix}\n\n{CHINESE_OUTPUT_MANDATE}"


def has_sufficient_chinese(
    value: str,
    *,
    min_chinese_ratio: float = 0.3,
) -> bool:
    """Return True when the text contains enough Chinese characters."""
    if not value or not value.strip():
        return False

    non_whitespace = _NON_WHITESPACE_RE.findall(value)
    if not non_whitespace:
        return False

    cjk_count = len(_CJK_RE.findall(value))
    return (cjk_count / len(non_whitespace)) >= min_chinese_ratio


def find_low_chinese_fields(named_values: Iterable[tuple[str, str]]) -> list[str]:
    """Return field paths whose values are non-empty and insufficiently Chinese."""
    failing: list[str] = []
    for field_name, value in named_values:
        if not value or not value.strip():
            continue
        if not has_sufficient_chinese(value):
            failing.append(field_name)
    return failing


def _get_or_create_counters() -> dict[str, int]:
    counters = _COUNTERS.get()
    if counters is None:
        counters = {"retry_count": 0, "failure_count": 0}
        _COUNTERS.set(counters)
    return counters


def record_retry() -> None:
    """Record that a guarded call retried once."""
    _get_or_create_counters()["retry_count"] += 1


def record_failure() -> None:
    """Record that a guarded call still failed after retry."""
    _get_or_create_counters()["failure_count"] += 1


def drain_counter_patch() -> dict[str, dict[str, int]]:
    """Return a session_meta patch and clear any accumulated counters."""
    counters = _COUNTERS.get()
    _COUNTERS.set(None)
    if not counters:
        return {}

    payload = {key: value for key, value in counters.items() if value}
    if not payload:
        return {}

    return {"language_guard": payload}
