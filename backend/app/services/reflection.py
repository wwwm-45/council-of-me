"""
Phase 6: Immersive reflection state machine and structured reflection storage.
Based on Fleck & Fitzpatrick (2010) R0-R4 + Baumer (2015) three dimensions.
"""
import json
import logging
import os
import re
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services import file_store

# session_id -> [{level, responses: [...]}, ...]; used by route and archive
_reflections_store: dict[str, list[dict]] = {}
_reflection_state_store: dict[str, "ReflectionSessionState"] = {}
logger = logging.getLogger(__name__)

FEELING_IDS = ("resonance", "unease", "surprise", "seen", "push_back", "wordless")
PATH_IDS = ("emotional", "assumption", "protective", "action")


@dataclass
class ReflectionEchoCard:
    card_id: str = ""
    title: str = ""
    body: str = ""
    path_id: str = ""
    created_at: str = ""


@dataclass
class DialogueTurn:
    turn_id: str = ""
    role: str = ""
    content: str = ""
    timestamp: str = ""
    created_at: str = ""
    layer: int = 0
    path: str = ""
    client_turn_id: str = ""
    echo_cards: list[ReflectionEchoCard] = field(default_factory=list)


@dataclass
class NodeExploration:
    node_id: str
    node_type: str
    node_label: str
    exploration_id: str = ""
    viewed_at: str = ""
    started_at: str = ""
    feelings: list[str] = field(default_factory=list)
    selected_path: str = ""
    current_layer: int = 0
    max_layer: int = 0
    explicit_insight: str = ""
    status: str = ""
    branch_dialogue: dict[str, list[DialogueTurn]] = field(default_factory=dict)
    branch_layers: dict[str, dict[str, int]] = field(default_factory=dict)
    dialogue: list[DialogueTurn] = field(default_factory=list)


@dataclass
class ReflectionSessionState:
    session_id: str
    phase: str = "explore"
    started_at: str = ""
    nodes_viewed: list[str] = field(default_factory=list)
    explorations: list[NodeExploration] = field(default_factory=list)
    current_exploration_id: str = ""
    current_node_id: str = ""
    dialogue: list[DialogueTurn] = field(default_factory=list)
    updated_at: str = ""


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _reflection_state_path(session_id: str) -> Path:
    return Path(file_store.config.SESSION_EXPORT_DIR).expanduser() / session_id / "reflection_state.json"


def _load_reflection_state_payload(session_id: str) -> dict[str, Any] | None:
    state_path = _reflection_state_path(session_id)
    if not state_path.exists():
        return None

    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
        raise ValueError(
            f"Invalid reflection state payload type: {type(payload).__name__}; expected object"
        )
    except Exception as exc:
        quarantine = state_path.with_name(
            f"reflection_state.corrupt-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}.json"
        )
        logger.warning(
            "Failed to load reflection state for session %s from %s; quarantining to %s",
            session_id,
            state_path,
            quarantine,
            exc_info=exc,
        )
        try:
            os.replace(state_path, quarantine)
        except Exception:
            logger.exception(
                "Failed to quarantine corrupt reflection state for session %s at %s",
                session_id,
                state_path,
            )
        return None


def _state_from_dict(payload: dict[str, Any]) -> ReflectionSessionState:
    nodes_viewed_raw = payload.get("nodes_viewed") or []
    nodes_viewed: list[str] = []
    for node_id in nodes_viewed_raw:
        if isinstance(node_id, str) and node_id and node_id not in nodes_viewed:
            nodes_viewed.append(node_id)

    def _echo_card_from_dict(item: dict[str, Any]) -> ReflectionEchoCard:
        return ReflectionEchoCard(
            card_id=str(item.get("card_id", "")),
            title=str(item.get("title", "")),
            body=str(item.get("body", "")),
            path_id=str(item.get("path_id", "")),
            created_at=str(item.get("created_at", "")),
        )

    def _dialogue_turn_from_dict(item: dict[str, Any]) -> DialogueTurn:
        raw_path = str(item.get("path", ""))
        selected_path = raw_path if raw_path in PATH_IDS else ""
        raw_layer = item.get("layer", 0)
        try:
            layer = int(raw_layer)
        except (TypeError, ValueError):
            layer = 0
        if layer < 0:
            layer = 0

        timestamp = str(item.get("timestamp", "")) or str(item.get("created_at", ""))
        echo_cards_raw = item.get("echo_cards") or []
        echo_cards = [
            _echo_card_from_dict(card)
            for card in echo_cards_raw
            if isinstance(card, dict)
        ]
        return DialogueTurn(
            turn_id=str(item.get("turn_id", "")),
            role=str(item.get("role", "")),
            content=str(item.get("content", "")),
            timestamp=timestamp,
            created_at=str(item.get("created_at", "")) or timestamp,
            layer=layer,
            path=selected_path,
            client_turn_id=str(item.get("client_turn_id", "")),
            echo_cards=echo_cards,
        )

    explorations_raw = payload.get("explorations") or []
    explorations: list[NodeExploration] = []
    for item in explorations_raw:
        if not isinstance(item, dict):
            continue
        dialogue_raw = item.get("dialogue") or []
        dialogue = [
            _dialogue_turn_from_dict(turn)
            for turn in dialogue_raw
            if isinstance(turn, dict)
        ]
        branch_dialogue_raw = item.get("branch_dialogue") or {}
        branch_dialogue: dict[str, list[DialogueTurn]] = {}
        if isinstance(branch_dialogue_raw, dict):
            for raw_path, branch_turns_raw in branch_dialogue_raw.items():
                if raw_path not in PATH_IDS or not isinstance(branch_turns_raw, list):
                    continue
                branch_dialogue[raw_path] = [
                    _dialogue_turn_from_dict(turn)
                    for turn in branch_turns_raw
                    if isinstance(turn, dict)
                ]
        branch_layers_raw = item.get("branch_layers") or {}
        branch_layers: dict[str, dict[str, int]] = {}
        if isinstance(branch_layers_raw, dict):
            for raw_path, raw_meta in branch_layers_raw.items():
                if raw_path not in PATH_IDS or not isinstance(raw_meta, dict):
                    continue
                try:
                    current_layer = int(raw_meta.get("current_layer", 0))
                except (TypeError, ValueError):
                    current_layer = 0
                try:
                    max_layer_branch = int(raw_meta.get("max_layer", 0))
                except (TypeError, ValueError):
                    max_layer_branch = 0
                if current_layer < 0:
                    current_layer = 0
                if max_layer_branch < current_layer:
                    max_layer_branch = current_layer
                branch_layers[raw_path] = {
                    "current_layer": current_layer,
                    "max_layer": max_layer_branch,
                }

        feelings = [
            feeling for feeling in (item.get("feelings") or [])
            if isinstance(feeling, str) and feeling in FEELING_IDS
        ]

        raw_selected_path = str(item.get("selected_path", ""))
        selected_path = raw_selected_path if raw_selected_path in PATH_IDS else ""
        raw_current_layer = item.get("current_layer", 0)
        raw_max_layer = item.get("max_layer", 0)
        try:
            current_layer = int(raw_current_layer)
        except (TypeError, ValueError):
            current_layer = 0
        try:
            max_layer = int(raw_max_layer)
        except (TypeError, ValueError):
            max_layer = 0
        if current_layer < 0:
            current_layer = 0
        if max_layer < current_layer:
            max_layer = current_layer

        explorations.append(
            NodeExploration(
                node_id=str(item.get("node_id", "")),
                node_type=str(item.get("node_type", "")),
                node_label=str(item.get("node_label", "")),
                exploration_id=str(item.get("exploration_id", "")),
                viewed_at=str(item.get("viewed_at", "")),
                started_at=str(item.get("started_at", "")) or str(item.get("viewed_at", "")),
                feelings=feelings,
                selected_path=selected_path,
                current_layer=current_layer,
                max_layer=max_layer,
                explicit_insight=str(item.get("explicit_insight", "")),
                status=str(item.get("status", "")),
                branch_dialogue=branch_dialogue,
                branch_layers=branch_layers,
                dialogue=dialogue,
            )
        )

    dialogue_root_raw = payload.get("dialogue") or []
    dialogue_root = [
        _dialogue_turn_from_dict(turn)
        for turn in dialogue_root_raw
        if isinstance(turn, dict)
    ]

    return ReflectionSessionState(
        session_id=str(payload.get("session_id", "")),
        phase=str(payload.get("phase", "explore")) or "explore",
        started_at=str(payload.get("started_at", "")),
        nodes_viewed=nodes_viewed,
        explorations=explorations,
        current_exploration_id=str(payload.get("current_exploration_id", "")),
        current_node_id=str(payload.get("current_node_id", "")),
        dialogue=dialogue_root,
        updated_at=str(payload.get("updated_at", "")),
    )


def persist_reflection_state(session_id: str, state: ReflectionSessionState) -> ReflectionSessionState:
    state.session_id = session_id
    state.updated_at = _utcnow()
    file_store.save_reflection_state(session_id, asdict(state))
    _reflection_state_store[session_id] = state
    return state


def ensure_reflection_state(session_id: str) -> ReflectionSessionState:
    if session_id in _reflection_state_store:
        return _reflection_state_store[session_id]

    payload = _load_reflection_state_payload(session_id)
    if isinstance(payload, dict) and payload:
        state = _state_from_dict(payload)
        if not state.session_id:
            state.session_id = session_id
        if not state.started_at:
            state.started_at = _utcnow()
    else:
        state = ReflectionSessionState(session_id=session_id, started_at=_utcnow())
    _reflection_state_store[session_id] = state
    return state


def ensure_reflection_state_persisted(session_id: str) -> ReflectionSessionState:
    state = deepcopy(ensure_reflection_state(session_id))
    if not state.started_at:
        state.started_at = _utcnow()
    if not state.phase:
        state.phase = "explore"
    return persist_reflection_state(session_id, state)


def _ensure_exploration(
    state: ReflectionSessionState,
    node_id: str,
    node_type: str,
    node_label: str,
) -> NodeExploration:
    for exploration in state.explorations:
        if exploration.node_id == node_id:
            if not exploration.node_type:
                exploration.node_type = node_type
            if not exploration.node_label:
                exploration.node_label = node_label
            if not exploration.viewed_at:
                exploration.viewed_at = _utcnow()
            if not exploration.started_at:
                exploration.started_at = exploration.viewed_at or _utcnow()
            return exploration

    exploration = NodeExploration(
        node_id=node_id,
        node_type=node_type,
        node_label=node_label,
        viewed_at=_utcnow(),
        started_at=_utcnow(),
    )
    state.explorations.append(exploration)
    return exploration


def mark_node_viewed(
    session_id: str,
    node_id: str,
    node_type: str,
    node_label: str,
) -> ReflectionSessionState:
    state = deepcopy(ensure_reflection_state(session_id))
    if node_id not in state.nodes_viewed:
        state.nodes_viewed.append(node_id)
    _ensure_exploration(state, node_id, node_type, node_label)
    if state.phase not in {"dialogue", "trace"}:
        state.phase = "explore"
    state.current_node_id = node_id
    return persist_reflection_state(session_id, state)


def mark_node_feelings(
    session_id: str,
    node_id: str,
    node_type: str,
    node_label: str,
    feelings: list[str],
) -> ReflectionSessionState:
    state = deepcopy(ensure_reflection_state(session_id))
    if node_id not in state.nodes_viewed:
        state.nodes_viewed.append(node_id)
    exploration = _ensure_exploration(state, node_id, node_type, node_label)
    exploration.feelings = [feeling for feeling in feelings if feeling in FEELING_IDS]
    return persist_reflection_state(session_id, state)


def set_node_insight(
    session_id: str,
    node_id: str,
    insight: str,
    *,
    node_type: str = "",
    node_label: str = "",
) -> ReflectionSessionState:
    state = deepcopy(ensure_reflection_state(session_id))
    if node_id and node_id not in state.nodes_viewed:
        state.nodes_viewed.append(node_id)
    exploration = _ensure_exploration(state, node_id, node_type, node_label)
    cleaned = (insight or "").strip()
    exploration.explicit_insight = cleaned
    if cleaned:
        exploration.status = "ready_for_trace"
    return persist_reflection_state(session_id, state)


def set_reflection_phase(session_id: str, phase: str) -> ReflectionSessionState:
    state = deepcopy(ensure_reflection_state(session_id))
    state.phase = (phase or "").strip() or state.phase
    return persist_reflection_state(session_id, state)


def serialize_reflection_state(state: ReflectionSessionState) -> dict[str, Any]:
    return asdict(state)


def _slugify_token(value: str, *, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return slug or fallback


def build_reflection_node_catalog(synthesis: dict[str, Any]) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _register(item: dict[str, Any]) -> None:
        node_id = str(item.get("node_id", "")).strip()
        if not node_id or node_id in seen_ids:
            return
        seen_ids.add(node_id)
        catalog.append(item)

    for index, voice in enumerate(synthesis.get("voice_positions") or []):
        if not isinstance(voice, dict):
            continue
        raw_agent_id = str(voice.get("agent_id", "")).strip()
        label = str(voice.get("agent_name", "")).strip() or f"Voice {index + 1}"
        node_id = f"voice-{_slugify_token(raw_agent_id or label, fallback=f'v{index + 1}')}"
        _register(
            {
                "node_id": node_id,
                "node_type": "voice",
                "node_label": label,
                "stance": str(voice.get("core_stance", "")).strip(),
            }
        )

    for index, tension in enumerate(synthesis.get("core_tensions") or []):
        if not isinstance(tension, dict):
            continue
        raw_tension_id = str(tension.get("tension_id", "")).strip()
        label = str(tension.get("name", "")).strip() or f"Tension {index + 1}"
        node_id = f"tension-{_slugify_token(raw_tension_id or label, fallback=f't{index + 1}')}"
        _register(
            {
                "node_id": node_id,
                "node_type": "tension",
                "node_label": label,
                "intensity": tension.get("intensity", 0.0),
            }
        )

    return catalog


def get_reflections(session_id: str) -> list[dict]:
    """Return list of reflection entries for Phase 7 archive."""
    cached = _reflections_store.get(session_id)
    if cached is not None:
        return list(cached)

    persisted = file_store.load_reflections(session_id)
    if isinstance(persisted, list) and persisted:
        _reflections_store[session_id] = list(persisted)
        return list(persisted)
    return []


def upsert_reflection_summary_bridge(session_id: str, content: str, level: str = "R4") -> None:
    """Persist a single legacy content entry so archive/closure code can read plain text."""
    record = {
        "level": level,
        "content": (content or "").strip(),
        "source": "trace_summary",
    }
    entries = list(_reflections_store.get(session_id) or file_store.load_reflections(session_id) or [])
    for idx, existing in enumerate(entries):
        if existing.get("source") == "trace_summary":
            entries[idx] = record
            _reflections_store[session_id] = entries
            file_store.save_reflections(session_id, entries)
            return
    entries.append(record)
    _reflections_store[session_id] = entries
    file_store.save_reflections(session_id, entries)
