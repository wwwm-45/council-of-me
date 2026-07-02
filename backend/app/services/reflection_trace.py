from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass
from typing import Any

from app.services.file_store import save_reflection_trace
from app.services.reflection import NodeExploration, ReflectionSessionState


def _to_dict(obj: object) -> dict:
    if isinstance(obj, dict):
        return obj
    if is_dataclass(obj):
        return asdict(obj)
    return {}


def _turn_layer(turn: dict) -> int:
    try:
        layer = int(turn.get("layer", 0))
    except (TypeError, ValueError):
        layer = 0
    if layer < 0:
        layer = 0
    return layer


def _dialogue_to_dicts(dialogue: list[object]) -> list[dict]:
    result: list[dict] = []
    for turn in dialogue:
        turn_dict = _to_dict(turn)
        if turn_dict:
            result.append(turn_dict)
    return result


def _dialogue_max_layer(dialogue: list[dict]) -> int:
    if not dialogue:
        return 0
    return max((_turn_layer(turn) for turn in dialogue), default=0)


def _select_primary_branch(exploration: NodeExploration) -> dict:
    candidates: dict[str, dict] = {}

    def _register_candidate(path: str, max_layer: int, dialogue: list[dict]) -> None:
        if not path:
            return
        existing = candidates.get(path)
        if existing is None:
            candidates[path] = {"path": path, "max_layer": max_layer, "dialogue": dialogue}
            return
        if max_layer > existing["max_layer"]:
            candidates[path] = {"path": path, "max_layer": max_layer, "dialogue": dialogue}
            return
        if max_layer == existing["max_layer"] and len(dialogue) > len(existing["dialogue"]):
            candidates[path] = {"path": path, "max_layer": max_layer, "dialogue": dialogue}

    active_path = exploration.selected_path or "emotional"
    active_dialogue = _dialogue_to_dicts(exploration.dialogue)
    active_max = max(int(exploration.max_layer or 0), _dialogue_max_layer(active_dialogue))
    _register_candidate(active_path, active_max, active_dialogue)

    branch_dialogue = exploration.branch_dialogue or {}
    branch_layers = exploration.branch_layers or {}
    for path, raw_dialogue in branch_dialogue.items():
        if not isinstance(raw_dialogue, list):
            continue
        dialogue_dicts = _dialogue_to_dicts(raw_dialogue)
        layer_meta = branch_layers.get(path, {}) if isinstance(branch_layers.get(path, {}), dict) else {}
        try:
            branch_max = int(layer_meta.get("max_layer", 0))
        except (TypeError, ValueError):
            branch_max = 0
        branch_max = max(branch_max, _dialogue_max_layer(dialogue_dicts))
        _register_candidate(path, branch_max, dialogue_dicts)

    if not candidates:
        return {"path": active_path, "max_layer": active_max, "dialogue": active_dialogue}

    selected_path = exploration.selected_path or ""
    return max(
        candidates.values(),
        key=lambda item: (item["max_layer"], item["path"] == selected_path, len(item["dialogue"])),
    )


def _extract_default_insight(dialogue: list[dict]) -> str:
    user_turns: list[dict] = []
    for turn in dialogue:
        if turn.get("role") == "user":
            user_turns.append(turn)

    if not user_turns:
        return ""

    deepest_turn = max(user_turns, key=lambda t: (_turn_layer(t), t.get("created_at", ""), t.get("timestamp", "")))
    content = str(deepest_turn.get("content", "")).strip()
    if not content:
        return ""

    # Prefer the most explicit "I am ..." clause when present.
    clause_match = re.search(r"(?:,\s*)(I am [^.?!]+)(?:[.?!]|$)", content)
    if clause_match:
        return clause_match.group(1).strip() + "."

    sentence_match = re.search(r"([^.!?]{8,}[.!?])", content)
    if sentence_match:
        return sentence_match.group(1).strip()

    trimmed = content[:120].strip()
    if trimmed and trimmed[-1] not in ".!?":
        trimmed += "."
    return trimmed


def _compute_r_level(path: str, max_layer: int) -> str:
    level = 1
    if path in {"assumption", "protective"}:
        level = 2
    if max_layer >= 3 or path == "protective":
        level = 3
    if max_layer >= 4 or path == "action":
        level = 4
    return f"R{level}"


def _footprint_sentence(first: dict, deepest: dict, anchor_insight: str) -> str:
    first_label = first.get("node_label") or first.get("node_id") or "the first explored node"
    deepest_label = deepest.get("node_label") or deepest.get("node_id") or "the deepest explored node"
    if anchor_insight:
        if first_label == deepest_label:
            return f"In {first_label}, you uncovered this: {anchor_insight}"
        return f"From {first_label} to {deepest_label}, one thread became clear: {anchor_insight}"
    if first_label == deepest_label:
        return f"You stayed with {first_label} and deepened the reflection."
    return f"You moved from {first_label} toward {deepest_label} while deepening reflection."


def build_reflection_trace(state: ReflectionSessionState) -> dict:
    explored: list[NodeExploration] = []
    for exploration in state.explorations:
        has_content = bool(
            exploration.dialogue
            or exploration.feelings
            or exploration.selected_path
            or exploration.branch_dialogue
            or (exploration.explicit_insight or "").strip()
        )
        if has_content:
            explored.append(exploration)

    insights: list[dict] = []
    dominant_feelings: list[str] = []
    for exploration in explored:
        for feeling in exploration.feelings:
            if feeling not in dominant_feelings:
                dominant_feelings.append(feeling)

        primary_branch = _select_primary_branch(exploration)
        selected_path = primary_branch["path"] or "emotional"
        selected_max_layer = int(primary_branch["max_layer"] or 0)
        explicit_insight = (exploration.explicit_insight or "").strip()
        inferred_insight = _extract_default_insight(primary_branch["dialogue"])
        insight = explicit_insight or inferred_insight

        insights.append(
            {
                "exploration_id": exploration.exploration_id or exploration.node_id,
                "node_id": exploration.node_id,
                "node_type": exploration.node_type,
                "node_label": exploration.node_label,
                "path": selected_path,
                "max_layer": selected_max_layer,
                "r_level": _compute_r_level(selected_path, selected_max_layer),
                "insight": insight,
                "source": "explicit" if explicit_insight else "derived",
                "feelings": list(exploration.feelings),
            }
        )

    if insights:
        deepest = max(insights, key=lambda item: item.get("max_layer", 0))
        first = insights[0]
    else:
        first = {"node_label": "", "node_id": ""}
        deepest = {"node_label": "", "node_id": "", "path": "emotional", "max_layer": 0}

    insight_texts = [item["insight"] for item in insights if item.get("insight")]
    deepest_insight = str(deepest.get("insight", "")).strip()
    footprint = _footprint_sentence(first, deepest, deepest_insight)
    gentle_commitment = (
        f"Carry this forward gently: {insight_texts[0]}"
        if insight_texts
        else "Carry one gentle observation into your next step."
    )

    trace = {
        "session_id": state.session_id,
        "phase": "trace",
        "exploration_order": [item.get("exploration_id") for item in insights],
        "nodes_viewed": list(state.nodes_viewed),
        "insights": insights,
        "footprint_sentence": footprint,
        "closure_seed": {
            "explored_nodes_count": len(insights),
            "deepest_path": deepest.get("path", "emotional"),
            "dominant_feelings": dominant_feelings,
            "insights": insight_texts,
            "gentle_commitment": gentle_commitment,
        },
    }
    return trace


def build_and_persist_reflection_trace(state: ReflectionSessionState) -> dict:
    trace = build_reflection_trace(state)
    save_reflection_trace(state.session_id, trace)
    return trace


def extract_closure_seed(trace: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(trace, dict):
        return {}

    raw_seed = trace.get("closure_seed")
    if not isinstance(raw_seed, dict):
        return {}

    try:
        explored_nodes_count = int(raw_seed.get("explored_nodes_count", 0) or 0)
    except (TypeError, ValueError):
        explored_nodes_count = 0
    if explored_nodes_count < 0:
        explored_nodes_count = 0

    deepest_path = str(raw_seed.get("deepest_path", "")).strip()

    dominant_feelings_raw = raw_seed.get("dominant_feelings") or []
    dominant_feelings = [
        str(item).strip()
        for item in dominant_feelings_raw
        if str(item).strip()
    ]

    insights_raw = raw_seed.get("insights") or []
    insights = [
        str(item).strip()
        for item in insights_raw
        if str(item).strip()
    ]

    gentle_commitment = str(raw_seed.get("gentle_commitment", "")).strip()

    return {
        "explored_nodes_count": explored_nodes_count,
        "deepest_path": deepest_path,
        "dominant_feelings": dominant_feelings,
        "insights": insights,
        "gentle_commitment": gentle_commitment,
    }


def build_legacy_reflection_summary(trace: dict) -> str:
    closure_seed = extract_closure_seed(trace)
    explored_nodes_count = int(closure_seed.get("explored_nodes_count", 0) or 0)
    deepest_path = str(closure_seed.get("deepest_path", "")).strip()
    dominant_feelings = closure_seed.get("dominant_feelings") or []
    insight_texts = closure_seed.get("insights") or []

    summary_parts = [f"Reflection explored {explored_nodes_count} node(s)."]
    if dominant_feelings:
        summary_parts.append(f"Dominant feelings: {', '.join(str(item) for item in dominant_feelings)}.")
    if deepest_path:
        summary_parts.append(f"Deepest path: {deepest_path}.")
    if insight_texts:
        summary_parts.append(f"Key insight: {insight_texts[0]}")
    footprint = str(trace.get("footprint_sentence", "")).strip()
    if footprint:
        summary_parts.append(footprint)
    return " ".join(part.strip() for part in summary_parts if part and part.strip())
