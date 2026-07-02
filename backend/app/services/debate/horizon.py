"""3-C deterministic decision-horizon mapper (no LLM)."""
from __future__ import annotations

import re
from typing import Any

from app.services.debate.artifacts import Tension, TensionMap

_CJK = re.compile(r"[\u4e00-\u9fff]")
_ALNUM = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_OVERLAP_THRESHOLD = 0.3


def _statement_text(statement: Any) -> str:
    if statement is None:
        return ""
    if isinstance(statement, dict):
        value = statement.get("content", "")
    else:
        value = getattr(statement, "content", "")
    return str(value or "")


def _feature_set(text: str) -> set[str]:
    lowered = (text or "").lower()
    features = set(_CJK.findall(lowered))
    features.update(match.group(0) for match in _ALNUM.finditer(lowered))
    return features


def _tension_by_id(tensions: list[Tension], tension_id: Any) -> Tension | None:
    try:
        target_id = int(tension_id)
    except (TypeError, ValueError):
        return None

    for tension in tensions:
        try:
            if int(tension.id) == target_id:
                return tension
        except (TypeError, ValueError):
            continue
    return None


def classify_statement_horizon(
    statement: Any,
    tensions: list[Tension] | None,
    dominant_tension_id: Any,
) -> str:
    if not tensions:
        return "unscoped"

    related_tension_id = None
    if isinstance(statement, dict):
        related_tension_id = statement.get("related_tension_id")
    else:
        related_tension_id = getattr(statement, "related_tension_id", None)

    related_tension = _tension_by_id(tensions, related_tension_id)
    if related_tension is not None:
        return related_tension.horizon

    statement_features = _feature_set(_statement_text(statement))

    best_tension: Tension | None = None
    best_ratio = 0.0
    for tension in tensions:
        desc_features = _feature_set(tension.description)
        if not desc_features:
            continue
        overlap = statement_features & desc_features
        ratio = len(overlap) / len(desc_features)
        if ratio > best_ratio:
            best_ratio = ratio
            best_tension = tension

    if best_tension is not None and best_ratio >= _OVERLAP_THRESHOLD:
        return best_tension.horizon

    dominant_tension = _tension_by_id(tensions, dominant_tension_id)
    if dominant_tension is not None:
        return dominant_tension.horizon

    return "unscoped"


def tag_statements_with_horizon(
    statements: list[Any],
    tension_map: TensionMap | None,
) -> list[Any]:
    tensions = tension_map.tensions if tension_map is not None else []
    dominant_tension_id = tension_map.dominant_tension_id if tension_map is not None else None
    horizon = "unscoped"

    for statement in statements:
        horizon = classify_statement_horizon(statement, tensions, dominant_tension_id)
        if isinstance(statement, dict):
            statement["horizon"] = horizon
        else:
            setattr(statement, "horizon", horizon)
    return statements
