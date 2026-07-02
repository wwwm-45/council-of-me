"""Pure self-repetition detection helpers for debate turns."""

from __future__ import annotations

import re
from collections.abc import Sequence
from difflib import SequenceMatcher


OVERLAP_THRESHOLD = 0.55
SPAN_THRESHOLD = 8
MIN_LEN = 8

_CJK_OR_WORD_RE = re.compile(r"[\u4e00-\u9fff]|[a-z0-9]+")


def normalize_text(text: str | None) -> str:
    """Normalize text for repetition checks without importing audit logic."""
    lowered = (text or "").strip().lower()
    if not lowered:
        return ""
    return "".join(_CJK_OR_WORD_RE.findall(lowered))


def pair_overlap(left: str | None, right: str | None) -> float:
    """Return Jaccard overlap of normalized character bigrams."""
    normalized_left = normalize_text(left)
    normalized_right = normalize_text(right)
    if not normalized_left or not normalized_right:
        return 0.0

    left_bigrams = _bigrams(normalized_left)
    right_bigrams = _bigrams(normalized_right)
    union = left_bigrams | right_bigrams
    if not union:
        return 0.0
    return len(left_bigrams & right_bigrams) / len(union)


def longest_shared_span(left: str | None, right: str | None) -> int:
    """Return the longest shared normalized character span."""
    normalized_left = normalize_text(left)
    normalized_right = normalize_text(right)
    if not normalized_left or not normalized_right:
        return 0
    return SequenceMatcher(a=normalized_left, b=normalized_right).find_longest_match().size


def max_overlap_against(candidate: str, prior: Sequence[str]) -> float:
    """Return the strongest pair overlap between candidate and prior texts."""
    return max((pair_overlap(candidate, previous) for previous in prior), default=0.0)


def most_similar(candidate: str, prior: Sequence[str]) -> tuple[str, float]:
    """Return the most similar prior text and its overlap."""
    best_text = ""
    best_overlap = 0.0

    for previous in prior:
        overlap = pair_overlap(candidate, previous)
        if overlap > best_overlap:
            best_text = previous
            best_overlap = overlap

    if best_overlap <= 0.0:
        return "", 0.0
    return best_text, best_overlap


def is_self_repetition(
    candidate: str,
    prior: Sequence[str],
    *,
    overlap_threshold: float = OVERLAP_THRESHOLD,
    span_threshold: int = SPAN_THRESHOLD,
    min_len: int = MIN_LEN,
) -> bool:
    """Detect whether candidate repeats an earlier debate contribution."""
    normalized_candidate = normalize_text(candidate)
    if len(normalized_candidate) < min_len:
        return False

    for previous in prior:
        normalized_previous = normalize_text(previous)
        if not normalized_previous:
            continue
        if (
            pair_overlap(normalized_candidate, normalized_previous) >= overlap_threshold
            or longest_shared_span(normalized_candidate, normalized_previous) >= span_threshold
        ):
            return True
    return False


def _bigrams(text: str) -> set[str]:
    return {text[index:index + 2] for index in range(max(len(text) - 1, 1))}
