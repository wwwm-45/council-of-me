"""
Phase 6: Reflection prompts R1-R4 and structured reflection storage.
Prompts use enhanced synthesis data (tensions, protective intents, frameworks).
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

# ──────── Seven canonical cognitive frames ────────────────────────────────

ALL_FRAMES = [
    {"key": "cost_benefit", "label": "成本收益框架", "description": "从长远利弊、投入产出的角度衡量"},
    {"key": "emotional_safety", "label": "情感安全框架", "description": "关注情感需求、心理安全感"},
    {"key": "duty", "label": "责任义务框架", "description": "从对他人的责任和承诺出发"},
    {"key": "autonomy", "label": "自主性框架", "description": "关注个人自由、自我决定权"},
    {"key": "meaning", "label": "意义追求框架", "description": "追问人生意义、价值实现"},
    {"key": "relational", "label": "关系框架", "description": "从人际关系、社会连接的角度思考"},
    {"key": "creative", "label": "创造性框架", "description": "寻找第三选项、打破二元对立"},
]

_FRAME_MAP = {f["key"]: f for f in ALL_FRAMES}

# Identity card primary_lens keywords → canonical frame key
_LENS_TO_FRAME: dict[str, str] = {
    "情感安全": "emotional_safety",
    "心理需求": "emotional_safety",
    "成本收益": "cost_benefit",
    "风险评估": "cost_benefit",
    "权力关系": "duty",
    "隐含假设": "duty",
    "社会建构": "duty",
    "可能性空间": "creative",
    "第三选项": "creative",
    "系统视角": "meaning",
    "整体图景": "meaning",
}

# Opposite frame mapping for alternative suggestion
_OPPOSITES: dict[str, str] = {
    "cost_benefit": "emotional_safety",
    "emotional_safety": "cost_benefit",
    "duty": "autonomy",
    "autonomy": "duty",
    "meaning": "creative",
    "creative": "meaning",
    "relational": "autonomy",
}

# Re-framing prompts per frame
_FRAME_PROMPTS: dict[str, str] = {
    "cost_benefit": "如果你不计算任何利弊，只凭内心的声音做选择，答案会是什么？",
    "emotional_safety": "如果你能保证情感安全不受威胁，你最想做什么？",
    "duty": "如果你暂时放下所有责任和义务，只为自己而活一天，你会做什么不同的选择？",
    "autonomy": "如果你的决定会深刻影响你最在意的人，你还会做同样的选择吗？",
    "meaning": "这个选择在你人生的更大图景中代表什么？十年后回看，哪个选择更有意义？",
    "relational": "这个困境中的每个选择会如何影响你最重要的关系？",
    "creative": "如果你不必在已知的选项中选择，你能发明一个全新的解决方案吗？",
}

# ──────── Storage ─────────────────────────────────────────────────────────


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


def append_reflection(session_id: str, level: str, responses: list[dict]) -> None:
    """Append structured reflection entry."""
    if session_id not in _reflections_store:
        _reflections_store[session_id] = []
    _reflections_store[session_id].append({"level": level, "responses": responses})


def append_reflection_legacy(session_id: str, level: str, content: str) -> None:
    """Old format: wrap as single text response for backward compatibility."""
    append_reflection(session_id, level, [
        {"question_id": f"{level.lower()}_q1", "input_type": "text", "value": content},
    ])


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


# ──────── R4 Framework Detection ─────────────────────────────────────────


def _identify_dominant_frames(
    identity_cards: list[dict[str, Any]] | None,
    synthesis: dict[str, Any],
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    """Identify cognitive frameworks used by agents, enriched with user relevance."""
    user_frame_keys: set[str] = set()

    # Signal from profile value_conflicts
    for vc in (profile.get("value_conflicts") or []):
        for val_str in [str(vc.get("value_a", "")), str(vc.get("value_b", ""))]:
            for keyword, fk in _LENS_TO_FRAME.items():
                if keyword in val_str:
                    user_frame_keys.add(fk)

    # Signal from synthesis tensions
    for tension in (synthesis.get("core_tensions") or []):
        vc = tension.get("value_conflict") or {}
        for val_str in [str(vc.get("value_a", "")), str(vc.get("value_b", ""))]:
            for keyword, fk in _LENS_TO_FRAME.items():
                if keyword in val_str:
                    user_frame_keys.add(fk)

    detected: list[dict[str, Any]] = []
    if not identity_cards:
        return detected

    voice_positions = synthesis.get("voice_positions") or []
    vp_map = {vp.get("agent_id", ""): vp for vp in voice_positions}

    for card in identity_cards:
        cf = card.get("cognitive_framework") or {}
        lens = cf.get("primary_lens", "")
        if not lens:
            continue

        matched_key: str | None = None
        for keyword, fk in _LENS_TO_FRAME.items():
            if keyword in lens:
                matched_key = fk
                break

        if not matched_key:
            continue

        frame_info = _FRAME_MAP.get(matched_key)
        if not frame_info:
            continue

        agent_id = card.get("agent_id", "")
        vp = vp_map.get(agent_id, {})
        stance = vp.get("core_stance", "")[:80]

        detected.append({
            "agent_id": agent_id,
            "agent_name": card.get("display_name", card.get("role", "")),
            "framework_label": frame_info["label"],
            "framework_key": matched_key,
            "primary_lens": lens,
            "usage_evidence": stance or lens,
            "is_user_frame": matched_key in user_frame_keys,
        })

    # Sort: user-relevant frames first
    detected.sort(key=lambda d: (not d["is_user_frame"],))
    return detected


def _suggest_alternative_frame(dominant_keys: set[str]) -> dict[str, str]:
    """Suggest an underused framework for R4 re-framing exercise."""
    # Try opposite of dominant frames
    for dk in dominant_keys:
        opp = _OPPOSITES.get(dk)
        if opp and opp not in dominant_keys:
            frame = _FRAME_MAP[opp]
            return {
                "name": frame["label"],
                "description": frame["description"],
                "prompt": _FRAME_PROMPTS.get(opp, "试试从一个全新的角度看这个问题"),
            }

    # Fallback: first frame not in dominant set
    for frame in ALL_FRAMES:
        if frame["key"] not in dominant_keys:
            return {
                "name": frame["label"],
                "description": frame["description"],
                "prompt": _FRAME_PROMPTS.get(frame["key"], "试试从一个全新的角度看这个问题"),
            }

    return {
        "name": "创造性框架",
        "description": "寻找第三选项、打破二元对立",
        "prompt": "如果你不必在已知的选项中选择，你能发明一个全新的解决方案吗？",
    }


# ──────── Prompt Generation ──────────────────────────────────────────────


def _extract_notable_statements(
    statements: list[dict[str, Any]] | None,
    synthesis: dict[str, Any],
) -> list[dict[str, str]]:
    """Extract 6-10 notable debate statements for R1 Q2 multi_select."""
    if not statements:
        return []

    # Gather statement IDs from tension evidence
    evidence_ids: set[str] = set()
    for t in (synthesis.get("core_tensions") or []):
        for pole in [t.get("pole_a", {}), t.get("pole_b", {})]:
            for ev in (pole.get("evidence_statements") or []):
                evidence_ids.add(ev.get("statement_id", ""))

    # Take last-round statements per agent + tension evidence statements
    max_round = max((s.get("round_number", 1) for s in statements), default=1)
    seen_agents: set[str] = set()
    selected: list[dict[str, str]] = []

    # Last round, one per agent
    for s in reversed(statements):
        agent = s.get("agent_name", "")
        if s.get("round_number") == max_round and agent not in seen_agents:
            seen_agents.add(agent)
            selected.append({
                "id": s.get("statement_id", f"s_{len(selected)}"),
                "label": s.get("content", "")[:80],
                "agent_name": agent,
            })

    # Add tension evidence statements not already included
    selected_ids = {s["id"] for s in selected}
    for s in statements:
        sid = s.get("statement_id", "")
        if sid in evidence_ids and sid not in selected_ids and len(selected) < 10:
            selected.append({
                "id": sid,
                "label": s.get("content", "")[:80],
                "agent_name": s.get("agent_name", ""),
            })
            selected_ids.add(sid)

    return selected[:10]


def generate_reflection_prompts(
    synthesis: dict[str, Any],
    profile: dict[str, Any],
    identity_cards: list[dict[str, Any]] | None = None,
    statements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Generate contextualized R1-R4 multi-question prompts.
    Uses enhanced synthesis data for rich contextualization.
    """
    dilemma = profile.get("core_dilemma") or "内心困境"
    voices = synthesis.get("voice_positions") or []
    synthesis_type = synthesis.get("synthesis_type", "")
    protective_intents = synthesis.get("protective_intents") or []

    voice_names = [vp.get("agent_name", "") for vp in voices if vp.get("agent_name")]
    voice_list_str = "、".join(voice_names) if voice_names else "各个声音"

    # Voice stances for R2 hint
    voice_hints = []
    for vp in voices[:5]:
        name = vp.get("agent_name", "")
        stance = vp.get("core_stance", "")[:60]
        if name and stance:
            voice_hints.append(f"{name}认为「{stance}…」")
    voice_hint_str = "；".join(voice_hints) if voice_hints else "回顾各声音的立场"

    # Convergence for R3
    if synthesis_type == "CONSENSUS_MAP":
        convergence_desc = "各声音逐渐趋于共识"
        r3_hint = "想想哪些观点改变了你的看法，或者共识中是否有你仍然存疑的部分"
    else:
        convergence_desc = "各声音保持了不同立场"
        r3_hint = "想想这些不同声音是否反映了你内心的多面性，哪些是你不愿面对的"

    # Value conflicts for R4
    value_conflicts = profile.get("value_conflicts") or []
    if value_conflicts:
        vc_examples = "、".join(
            f"{c.get('value_a', '')}与{c.get('value_b', '')}" for c in value_conflicts[:3]
        )
    else:
        vc_examples = "不同价值取向"

    # R4 framework detection
    detected_frames = _identify_dominant_frames(identity_cards, synthesis, profile)
    dominant_keys = {d["framework_key"] for d in detected_frames}
    alternative_frame = _suggest_alternative_frame(dominant_keys)
    has_framework_data = len(detected_frames) > 0

    # R1 Q2: notable statements for multi_select
    notable = _extract_notable_statements(statements, synthesis)

    # ── R1: Descriptive Reflection ──

    r1: list[dict[str, Any]] = [
        {
            "id": "r1_q1",
            "level": "R1",
            "question": f"回顾关于「{dilemma}」的这场辩论，{voice_list_str}分别表达了什么核心观点？你觉得讨论的核心议题是什么？",
            "hint": "尝试用自己的话概括每个声音的立场",
            "input_type": "text",
            "optional": False,
            "options": None,
            "context": None,
        },
    ]
    if notable:
        r1.append({
            "id": "r1_q2",
            "level": "R1",
            "question": "哪些论点让你印象最深刻？",
            "hint": "可以是让你共鸣的，也可以是让你意外的",
            "input_type": "multi_select",
            "optional": True,
            "options": notable,
            "context": None,
        })

    # ── R2: Dialogical Reflection ──

    intent_hint_parts = []
    for pi in protective_intents[:3]:
        name = pi.get("agent_name", "")
        intent = pi.get("intent", "")[:40]
        if name and intent:
            intent_hint_parts.append(f"{name}可能在保护你的{pi.get('underlying_value', '某种需要')}")
    discomfort_hint = "；".join(intent_hint_parts) if intent_hint_parts else "不舒服往往指向重要的东西"

    slider_options = [
        {"id": vp.get("agent_id", f"agent_{i}"), "label": vp.get("agent_name", ""), "agent_name": vp.get("agent_name", ""), "default_value": 50}
        for i, vp in enumerate(voices)
    ]

    r2: list[dict[str, Any]] = [
        {
            "id": "r2_q1",
            "level": "R2",
            "question": f"在{voice_list_str}中，哪个声音的观点最触动你？为什么它引起了你的共鸣？",
            "hint": voice_hint_str,
            "input_type": "text",
            "optional": False,
            "options": None,
            "context": None,
        },
        {
            "id": "r2_q2",
            "level": "R2",
            "question": "哪个声音让你最不舒服或最想反驳？这种不舒服来自哪里？",
            "hint": discomfort_hint,
            "input_type": "text",
            "optional": False,
            "options": None,
            "context": None,
        },
    ]
    if slider_options:
        r2.append({
            "id": "r2_q3",
            "level": "R2",
            "question": "如果让你给每个声音的「音量」重新调整，你会怎么调？",
            "hint": "哪个声音应该更大声？哪个应该小一点？",
            "input_type": "slider_group",
            "optional": True,
            "options": slider_options,
            "context": None,
        })

    # ── R3: Transformative Reflection ──

    r3_q3_context = {"protective_intents": protective_intents} if protective_intents else None

    r3: list[dict[str, Any]] = [
        {
            "id": "r3_q1",
            "level": "R3",
            "question": f"这场辩论中{convergence_desc}。这个过程让你对自己有什么新的认识？",
            "hint": r3_hint,
            "input_type": "text",
            "optional": False,
            "options": None,
            "context": None,
        },
        {
            "id": "r3_q2",
            "level": "R3",
            "question": "你发现自己内心的哪些声音之前被忽视了？",
            "hint": "有没有某个声音一直在，但你从未认真倾听？",
            "input_type": "text",
            "optional": True,
            "options": None,
            "context": None,
        },
        {
            "id": "r3_q3",
            "level": "R3",
            "question": "如果把这些声音都看作是在保护你，它们各自在保护什么？",
            "hint": "即使是让你不舒服的声音，它的善意是什么？",
            "input_type": "text",
            "optional": False,
            "options": None,
            "context": r3_q3_context,
        },
    ]

    # ── R4: Critical / Framework Reflection ──

    frame_labels = [d["framework_label"] for d in detected_frames[:3]]
    frame_hint = f"比如：{'、'.join(frame_labels)}" if frame_labels else f"例如在这次辩论中涉及的{vc_examples}之间的取舍"

    r4_q1_context = {"detected_frames": detected_frames} if detected_frames else None
    r4_q3_context = {"alternative_frame": alternative_frame}

    r4: list[dict[str, Any]] = [
        {
            "id": "r4_q1",
            "level": "R4",
            "question": f"在思考「{dilemma}」时，你发现自己通常用什么「框架」或角度来看问题？",
            "hint": frame_hint,
            "input_type": "text",
            "optional": False,
            "options": None,
            "context": r4_q1_context,
        },
        {
            "id": "r4_q2",
            "level": "R4",
            "question": "这个框架从哪里来？是谁教给你的？",
            "hint": "家庭？学校？社会？某个重要的人？某段经历？",
            "input_type": "text",
            "optional": True,
            "options": None,
            "context": None,
        },
        {
            "id": "r4_q3",
            "level": "R4",
            "question": f"如果用「{alternative_frame['name']}」重新看这个问题，你会看到什么不同？",
            "hint": alternative_frame["prompt"],
            "input_type": "text",
            "optional": False,
            "options": None,
            "context": r4_q3_context,
        },
        {
            "id": "r4_q4",
            "level": "R4",
            "question": f"在这个困境中，有哪些「理所当然」的假设？",
            "hint": f"比如「我必须……」「不能……」——谁说的？涉及{vc_examples}的取舍时，哪些前提是未经检验的？",
            "input_type": "text",
            "optional": True,
            "options": None,
            "context": None,
        },
    ]

    all_questions = r1 + r2 + r3 + r4
    total = len(all_questions)
    required = sum(1 for q in all_questions if not q["optional"])

    return {
        "r1": r1,
        "r2": r2,
        "r3": r3,
        "r4": r4,
        "meta": {
            "total_questions": total,
            "required_questions": required,
            "synthesis_type": synthesis_type,
            "has_framework_data": has_framework_data,
        },
    }
