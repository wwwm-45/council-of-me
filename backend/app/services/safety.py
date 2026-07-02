"""
Safety screening (COMPLETE_PROCESS_FLOW Phase 0.3).
Keyword scan + optional LLM semantic analysis; returns SAFE / WARNING / CRITICAL.
"""
import re
from typing import Callable, Optional

from app.config import (
    SAFETY_AFTER_CRISIS_THRESHOLD,
    SAFETY_CRITICAL_THRESHOLD,
    SAFETY_WARNING_THRESHOLD,
)
from app.models.safety import SafetyLevel, SafetyResult

# Direct self-harm / suicide intent patterns.
DIRECT_CRISIS_PATTERNS = [
    (r"(想|要)死", 1.0),
    (r"自杀", 1.0),
    (r"结束(自己的)?生命", 1.0),
    (r"不想活", 1.0),
    (r"割腕|自残|伤害自己", 1.0),
]

# High-distress expressions that should not, by themselves, trigger crisis mode.
DISTRESS_PATTERNS = [
    (r"崩溃|无法呼吸", 0.75),
]

CRISIS_RESOURCES = [
    {"name": "心理危机热线", "phone": "400-161-9995", "desc": "24小时"},
    {"name": "北京心理危机研究与干预中心", "phone": "010-82951332", "desc": ""},
]


def _keyword_scan(text: str) -> tuple[float, list[str]]:
    """Return (max_score, list of matched keywords)."""
    if not text or not text.strip():
        return 0.0, []
    max_score = 0.0
    matched: list[str] = []
    for pattern, score in DIRECT_CRISIS_PATTERNS + DISTRESS_PATTERNS:
        if re.search(pattern, text):
            max_score = max(max_score, score)
            matched.append(pattern)
    return max_score, matched


class SafetyMonitor:
    """
    Check user input for crisis content.
    Use check_input() before any business logic that handles user text.
    """

    def __init__(
        self,
        semantic_scorer: Optional[Callable[[str, list[str]], float]] = None,
    ) -> None:
        self._semantic_scorer = semantic_scorer

    def check_input(
        self,
        user_input: str,
        context: Optional[list[str]] = None,
        use_lower_threshold: bool = False,
    ) -> SafetyResult:
        """
        Run keyword + optional semantic check.
        use_lower_threshold: True when user has just returned from crisis page (next 5 rounds).
        """
        context = context or []
        keyword_score, matched = _keyword_scan(user_input or "")
        matched_direct = [pattern for pattern, _score in DIRECT_CRISIS_PATTERNS if re.search(pattern, user_input or "")]

        if matched_direct:
            return SafetyResult(
                level=SafetyLevel.CRITICAL,
                confidence=1.0,
                matched_keywords=matched_direct,
            )

        semantic_score = 0.0
        if self._semantic_scorer:
            semantic_score = self._semantic_scorer(user_input or "", context)

        final_score = max(keyword_score, semantic_score)
        threshold_critical = SAFETY_AFTER_CRISIS_THRESHOLD if use_lower_threshold else SAFETY_CRITICAL_THRESHOLD
        threshold_warning = SAFETY_WARNING_THRESHOLD

        # Do not escalate generic distress-only language to CRITICAL just because
        # the post-crisis threshold is temporarily lowered.
        if semantic_score >= threshold_critical or (keyword_score >= threshold_critical and matched_direct):
            return SafetyResult(
                level=SafetyLevel.CRITICAL,
                confidence=final_score,
                matched_keywords=matched,
            )
        if final_score >= threshold_warning:
            return SafetyResult(
                level=SafetyLevel.WARNING,
                confidence=final_score,
                matched_keywords=matched,
            )
        return SafetyResult(
            level=SafetyLevel.SAFE,
            confidence=1.0 - final_score,
            matched_keywords=[],
        )
