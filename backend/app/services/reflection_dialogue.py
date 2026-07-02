from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from app.services.llm import generate_chat
from app.services.reflection import (
    DialogueTurn,
    NodeExploration,
    PATH_IDS,
    ReflectionSessionState,
    ensure_reflection_state,
    persist_reflection_state,
)

_MIN_LAYER = 1
_MAX_LAYER = 4
_start_locks: dict[str, asyncio.Lock] = {}
_respond_locks: dict[str, asyncio.Lock] = {}


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _get_respond_lock(session_id: str, exploration_id: str) -> asyncio.Lock:
    return _respond_locks.setdefault(f"{session_id}:{exploration_id}", asyncio.Lock())


def _get_start_lock(session_id: str, node_id: str) -> asyncio.Lock:
    return _start_locks.setdefault(f"{session_id}:{node_id}", asyncio.Lock())


def _coerce_layer(value: object, *, default: int = 0) -> int:
    try:
        layer = int(value)
    except (TypeError, ValueError):
        layer = default
    if layer < 0:
        layer = 0
    if layer > _MAX_LAYER:
        layer = _MAX_LAYER
    return layer


def _dialogue_max_layer(dialogue: list[DialogueTurn]) -> int:
    if not dialogue:
        return 0
    return max((_coerce_layer(turn.layer, default=0) for turn in dialogue), default=0)


def recommend_path(feelings: list[str], node_type: str, deepest_layer_seen: int) -> str:
    feeling_set = {feeling for feeling in feelings if isinstance(feeling, str)}
    if "push_back" in feeling_set or "surprise" in feeling_set:
        return "assumption"
    if node_type == "tension" or len(feeling_set) >= 2:
        return "protective"
    if feeling_set.intersection({"unease", "wordless", "seen"}):
        return "emotional"
    if "resonance" in feeling_set or deepest_layer_seen >= 3:
        return "action"
    return "emotional"


def _new_turn(
    *,
    role: str,
    content: str,
    layer: int,
    path: str,
    client_turn_id: str = "",
) -> DialogueTurn:
    created_at = _utcnow()
    return DialogueTurn(
        turn_id=f"turn-{uuid4().hex[:12]}",
        role=role,
        content=content.strip(),
        timestamp=created_at,
        created_at=created_at,
        layer=max(_MIN_LAYER, min(_MAX_LAYER, int(layer))),
        path=path if path in PATH_IDS else "",
        client_turn_id=client_turn_id,
    )


def _serialize_turn(turn: DialogueTurn) -> dict:
    return {
        "turn_id": turn.turn_id,
        "role": turn.role,
        "content": turn.content,
        "layer": turn.layer,
        "path": turn.path,
        "created_at": turn.created_at or turn.timestamp,
        "client_turn_id": turn.client_turn_id,
    }


def _to_turn(item: DialogueTurn | dict) -> DialogueTurn:
    if isinstance(item, DialogueTurn):
        return item
    if not isinstance(item, dict):
        return DialogueTurn()
    raw_layer = item.get("layer", _MIN_LAYER)
    try:
        layer = int(raw_layer)
    except (TypeError, ValueError):
        layer = _MIN_LAYER
    if layer < _MIN_LAYER:
        layer = _MIN_LAYER
    if layer > _MAX_LAYER:
        layer = _MAX_LAYER
    created_at = str(item.get("created_at", "")) or str(item.get("timestamp", "")) or _utcnow()
    return DialogueTurn(
        turn_id=str(item.get("turn_id", "")) or f"turn-{uuid4().hex[:12]}",
        role=str(item.get("role", "")),
        content=str(item.get("content", "")),
        timestamp=str(item.get("timestamp", "")) or created_at,
        created_at=created_at,
        layer=layer,
        path=str(item.get("path", "")) if str(item.get("path", "")) in PATH_IDS else "",
        client_turn_id=str(item.get("client_turn_id", "")),
    )


def _normalize_exploration_dialogue(exploration: NodeExploration) -> None:
    exploration.dialogue = [_to_turn(item) for item in exploration.dialogue]
    normalized_branches: dict[str, list[DialogueTurn]] = {}
    for path, branch in (exploration.branch_dialogue or {}).items():
        if path not in PATH_IDS or not isinstance(branch, list):
            continue
        normalized_branches[path] = [_to_turn(item) for item in branch]
    exploration.branch_dialogue = normalized_branches
    normalized_branch_layers: dict[str, dict[str, int]] = {}
    for path, meta in (exploration.branch_layers or {}).items():
        if path not in PATH_IDS or not isinstance(meta, dict):
            continue
        current_layer = _coerce_layer(meta.get("current_layer", 0), default=0)
        max_layer = _coerce_layer(meta.get("max_layer", 0), default=0)
        if max_layer < current_layer:
            max_layer = current_layer
        normalized_branch_layers[path] = {"current_layer": current_layer, "max_layer": max_layer}
    exploration.branch_layers = normalized_branch_layers


def _save_current_branch_state(exploration: NodeExploration) -> None:
    path = exploration.selected_path
    if path not in PATH_IDS:
        return
    branch_dialogue = list(exploration.dialogue)
    dialogue_max = _dialogue_max_layer(branch_dialogue)
    current_layer = _coerce_layer(exploration.current_layer, default=_MIN_LAYER)
    if current_layer < _MIN_LAYER:
        current_layer = _MIN_LAYER
    max_layer = max(
        _coerce_layer(exploration.max_layer, default=current_layer),
        current_layer,
        dialogue_max,
    )
    exploration.branch_dialogue[path] = branch_dialogue
    exploration.branch_layers[path] = {
        "current_layer": current_layer,
        "max_layer": max_layer,
    }


def _restore_branch_state(exploration: NodeExploration, path: str) -> None:
    branch_dialogue = list(exploration.branch_dialogue.get(path, []))
    if not branch_dialogue:
        exploration.dialogue = []
        exploration.current_layer = _MIN_LAYER
        exploration.max_layer = _MIN_LAYER
        return

    exploration.dialogue = branch_dialogue
    branch_meta = exploration.branch_layers.get(path, {})
    dialogue_max = _dialogue_max_layer(branch_dialogue)
    current_layer = _coerce_layer(branch_meta.get("current_layer", dialogue_max or _MIN_LAYER), default=_MIN_LAYER)
    if current_layer < _MIN_LAYER:
        current_layer = _MIN_LAYER
    max_layer = _coerce_layer(branch_meta.get("max_layer", dialogue_max or current_layer), default=current_layer)
    if max_layer < current_layer:
        max_layer = current_layer
    if max_layer < dialogue_max:
        max_layer = dialogue_max
    exploration.current_layer = current_layer
    exploration.max_layer = max_layer


def _find_exploration_by_node(state: ReflectionSessionState, node_id: str) -> NodeExploration:
    for exploration in state.explorations:
        if exploration.node_id == node_id:
            return exploration
    exploration = NodeExploration(node_id=node_id, node_type="", node_label="", viewed_at=_utcnow(), started_at=_utcnow())
    state.explorations.append(exploration)
    if node_id and node_id not in state.nodes_viewed:
        state.nodes_viewed.append(node_id)
    return exploration


def _find_exploration_by_id(state: ReflectionSessionState, exploration_id: str) -> NodeExploration | None:
    for exploration in state.explorations:
        if exploration.exploration_id == exploration_id:
            return exploration
    return None


def _select_path(exploration: NodeExploration, requested_path: str) -> str:
    if requested_path in PATH_IDS:
        return requested_path
    return recommend_path(exploration.feelings, exploration.node_type, exploration.max_layer or 0)


def _ensure_exploration_id(exploration: NodeExploration) -> None:
    if not exploration.exploration_id:
        exploration.exploration_id = f"exp-{uuid4().hex[:12]}"


def _recommended_next_actions(path: str, current_layer: int) -> list[str]:
    if current_layer >= _MAX_LAYER:
        return ["capture_insight", "prepare_trace"]
    if path == "assumption":
        return ["name_hidden_premise", "check_origin_story"]
    if path == "protective":
        return ["identify_protective_intent", "thank_the_protector"]
    if path == "action":
        return ["define_small_commitment", "time_box_next_step"]
    return ["stay_with_feeling", "describe_body_signal"]


def _build_opening_prompt(path: str, node_label: str) -> str:
    label = node_label or "this part"
    if path == "assumption":
        return f"Let's explore {label}. What assumption feels least negotiable right now?"
    if path == "protective":
        return f"Inside {label}, what is this reaction trying to protect for you?"
    if path == "action":
        return f"If {label} were 5% clearer, what is one concrete next step you would take?"
    return f"When you stay with {label}, what feeling is most alive right now?"


def _next_layer(current_layer: int, content: str) -> int:
    if current_layer >= _MAX_LAYER:
        return _MAX_LAYER
    word_count = len((content or "").strip().split())
    if word_count >= 6:
        return current_layer + 1
    return current_layer


def _build_follow_up_system_prompt(path: str, layer: int) -> str:
    return (
        "You are guiding a reflection dialogue. "
        f"Path={path}. "
        f"Layer={layer}. "
        "Reply with one compassionate, concrete question only."
    )


def _fallback_follow_up(path: str, layer: int) -> str:
    if layer >= _MAX_LAYER:
        return "What sentence captures the deepest truth you are touching right now?"
    if path == "assumption":
        return "Whose standard taught you that premise, and does it still fit your life now?"
    if path == "protective":
        return "If this protector trusted you a little more, what would it allow you to feel?"
    if path == "action":
        return "What is the smallest next action you can commit to in the next 24 hours?"
    return "If this feeling had a voice, what would it want you to hear?"


async def start_dialogue(state: ReflectionSessionState, node_id: str, path: str) -> tuple[ReflectionSessionState, dict]:
    lock = _get_start_lock(state.session_id, node_id)
    async with lock:
        latest_state = ensure_reflection_state(state.session_id)
        detached_state = deepcopy(latest_state)
        exploration = _find_exploration_by_node(detached_state, node_id)
        _normalize_exploration_dialogue(exploration)
        _ensure_exploration_id(exploration)

        selected_path = _select_path(exploration, path)
        if exploration.selected_path and exploration.selected_path != selected_path:
            _save_current_branch_state(exploration)
        if exploration.selected_path != selected_path:
            _restore_branch_state(exploration, selected_path)
        exploration.selected_path = selected_path
        if exploration.current_layer < _MIN_LAYER:
            exploration.current_layer = _MIN_LAYER
        if exploration.max_layer < exploration.current_layer:
            exploration.max_layer = exploration.current_layer
        exploration.status = "active"

        if not exploration.dialogue:
            assistant_turn = _new_turn(
                role="assistant",
                content=_build_opening_prompt(selected_path, exploration.node_label),
                layer=exploration.current_layer,
                path=selected_path,
            )
            exploration.dialogue.append(assistant_turn)
        else:
            last_turn = exploration.dialogue[-1]
            if last_turn.role == "assistant":
                assistant_turn = last_turn
            else:
                assistant_turn = _new_turn(
                    role="assistant",
                    content=_build_opening_prompt(selected_path, exploration.node_label),
                    layer=exploration.current_layer,
                    path=selected_path,
                )
                exploration.dialogue.append(assistant_turn)

        detached_state.phase = "dialogue"
        detached_state.current_exploration_id = exploration.exploration_id
        detached_state.current_node_id = exploration.node_id
        _save_current_branch_state(exploration)
        detached_state = persist_reflection_state(detached_state.session_id, detached_state)

        payload = {
            "exploration_id": exploration.exploration_id,
            "assistant_turn": _serialize_turn(assistant_turn),
            "current_layer": exploration.current_layer,
            "selected_path": selected_path,
            "exploration_status": "in_progress" if exploration.current_layer < _MAX_LAYER else "ready_for_trace",
            "recommended_next_actions": _recommended_next_actions(selected_path, exploration.current_layer),
        }
        return detached_state, payload


async def respond_dialogue(
    state: ReflectionSessionState,
    exploration_id: str,
    content: str,
    client_turn_id: str,
) -> tuple[ReflectionSessionState, dict]:
    lock = _get_respond_lock(state.session_id, exploration_id)
    async with lock:
        latest_state = ensure_reflection_state(state.session_id)
        detached_state = deepcopy(latest_state)
        exploration = _find_exploration_by_id(detached_state, exploration_id)
        if exploration is None:
            raise ValueError(f"Exploration not found: {exploration_id}")

        _normalize_exploration_dialogue(exploration)
        _ensure_exploration_id(exploration)
        selected_path = _select_path(exploration, exploration.selected_path)
        exploration.selected_path = selected_path
        if exploration.current_layer < _MIN_LAYER:
            exploration.current_layer = _MIN_LAYER

        duplicate_index = -1
        for idx, turn in enumerate(exploration.dialogue):
            if turn.role == "user" and turn.client_turn_id and turn.client_turn_id == client_turn_id:
                duplicate_index = idx
                break

        if duplicate_index >= 0:
            assistant_turn = None
            if duplicate_index + 1 < len(exploration.dialogue):
                candidate = exploration.dialogue[duplicate_index + 1]
                if candidate.role == "assistant":
                    assistant_turn = candidate
            if assistant_turn is None:
                fallback_layer = min(_MAX_LAYER, max(_MIN_LAYER, exploration.current_layer))
                assistant_turn = _new_turn(
                    role="assistant",
                    content=_fallback_follow_up(selected_path, fallback_layer),
                    layer=fallback_layer,
                    path=selected_path,
                )
                exploration.dialogue.append(assistant_turn)

            _save_current_branch_state(exploration)
            detached_state.phase = "dialogue"
            detached_state.current_exploration_id = exploration.exploration_id
            detached_state.current_node_id = exploration.node_id
            detached_state = persist_reflection_state(detached_state.session_id, detached_state)

            payload = {
                "exploration_id": exploration.exploration_id,
                "assistant_turn": _serialize_turn(assistant_turn),
                "current_layer": exploration.current_layer,
                "layer_advanced": False,
                "selected_path": selected_path,
                "exploration_status": "in_progress" if exploration.current_layer < _MAX_LAYER else "ready_for_trace",
                "recommended_next_actions": _recommended_next_actions(selected_path, exploration.current_layer),
            }
            return detached_state, payload

        active_layer = min(_MAX_LAYER, max(_MIN_LAYER, exploration.current_layer))
        user_turn = _new_turn(
            role="user",
            content=content,
            layer=active_layer,
            path=selected_path,
            client_turn_id=client_turn_id,
        )
        exploration.dialogue.append(user_turn)
        exploration.max_layer = max(exploration.max_layer, user_turn.layer)

        next_layer = _next_layer(active_layer, content)
        layer_advanced = next_layer > active_layer
        follow_up_messages = [
            {"role": turn.role, "content": turn.content}
            for turn in exploration.dialogue[-8:]
            if turn.role in {"user", "assistant"} and turn.content
        ]
        try:
            follow_up = await generate_chat(
                messages=follow_up_messages,
                system=_build_follow_up_system_prompt(selected_path, next_layer),
                temperature=0.7,
                max_tokens=220,
            )
        except Exception:
            follow_up = ""
        if not isinstance(follow_up, str) or not follow_up.strip():
            follow_up = _fallback_follow_up(selected_path, next_layer)

        assistant_turn = _new_turn(
            role="assistant",
            content=follow_up,
            layer=next_layer,
            path=selected_path,
        )
        exploration.dialogue.append(assistant_turn)
        exploration.current_layer = next_layer
        exploration.max_layer = max(exploration.max_layer, next_layer)
        exploration.status = "in_progress" if exploration.current_layer < _MAX_LAYER else "ready_for_trace"

        detached_state.phase = "dialogue"
        detached_state.current_exploration_id = exploration.exploration_id
        detached_state.current_node_id = exploration.node_id
        _save_current_branch_state(exploration)
        detached_state = persist_reflection_state(detached_state.session_id, detached_state)

        payload = {
            "exploration_id": exploration.exploration_id,
            "assistant_turn": _serialize_turn(assistant_turn),
            "current_layer": exploration.current_layer,
            "layer_advanced": layer_advanced,
            "selected_path": selected_path,
            "exploration_status": exploration.status,
            "recommended_next_actions": _recommended_next_actions(selected_path, exploration.current_layer),
        }
        return detached_state, payload
