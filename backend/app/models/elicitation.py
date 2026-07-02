"""Phase 1 elicitation data models."""

from dataclasses import dataclass, field
import string
from typing import Any, Optional


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _pick_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y", "on"}:
            return True
        if text in {"false", "0", "no", "n", "off", ""}:
            return False
        return default
    return bool(value)


def _normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        candidates = value
    else:
        candidates = [value]

    normalized: list[str] = []
    for item in candidates:
        text = _pick_text(item)
        if text:
            normalized.append(text)
    return normalized


def _normalize_relationships(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []

    normalized: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "target": _pick_text(item.get("target")),
                "dynamic": _pick_text(item.get("dynamic")),
                "description": _pick_text(item.get("description")),
            }
        )
    return normalized


def normalize_comparable_text(value: Any) -> str:
    text = _pick_text(value).lower()
    if not text:
        return ""

    punctuation = set(string.punctuation) | {
        "，",
        "。",
        "！",
        "？",
        "：",
        "；",
        "、",
        "（",
        "）",
        "【",
        "】",
        "《",
        "》",
        "“",
        "”",
        "‘",
        "’",
        "…",
        "—",
        "·",
        " ",
        "\n",
        "\r",
        "\t",
    }
    return "".join(ch for ch in text if ch not in punctuation)


def texts_meaningfully_different(left: Any, right: Any) -> bool:
    left_text = normalize_comparable_text(left)
    right_text = normalize_comparable_text(right)
    return bool(left_text and right_text and left_text != right_text)


def dilemma_layer_semantic_key(depth: Any, description: Any, user_language: Any) -> tuple[str, str, str]:
    return (
        normalize_comparable_text(_extract_depth_label(depth)),
        normalize_comparable_text(description),
        normalize_comparable_text(user_language),
    )


def voice_name_is_label(name: str) -> bool:
    stripped = _pick_text(name)
    if not stripped:
        return False

    if stripped.endswith(("者", "型", "派")):
        return True
    return any(token in stripped for token in ("分析者", "追求者", "规避者", "行动者", "保护者"))


def intensities_are_flat(values: list[float], *, default: float = 0.5) -> bool:
    return bool(values) and all(abs(value - default) <= 1e-9 for value in values)


def _extract_metric(value: Any, *keys: str, default: float = 0.0) -> float:
    if isinstance(value, dict):
        for key in keys:
            if key in value:
                return _coerce_float(value.get(key), default)
        return default
    return _coerce_float(value, default)


def _coerce_unit_interval(value: Any, default: float = 0.0) -> float:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.endswith("%"):
            number = _coerce_float(stripped[:-1], default)
            return min(max(number / 100.0, 0.0), 1.0)

    number = _coerce_float(value, default)
    if number > 1.0 and number <= 100.0:
        number = number / 100.0
    return min(max(number, 0.0), 1.0)


def _extract_intensity(payload: dict[str, Any], default: float = 0.5) -> float:
    for key in (
        "intensity",
        "strength",
        "salience",
        "weight",
        "intensity_score",
        "emotional_intensity",
    ):
        if key in payload:
            return _coerce_unit_interval(payload.get(key), default)
    return default


def _extract_depth_label(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip().lower()
        if text:
            return text

    layer = _coerce_int(value, 1)
    return {
        1: "surface",
        2: "emotional",
        3: "existential",
    }.get(layer, "surface")


@dataclass
class ExtractedInfo:
    """Backward-compatible lightweight per-round extraction payload."""

    core_dilemma: Optional[str] = None
    inner_voices: list[dict[str, Any]] = field(default_factory=list)
    values: list[str] = field(default_factory=list)
    tactics: list[str] = field(default_factory=list)
    emotions: list[str] = field(default_factory=list)
    stakeholders: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    deltas: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "core_dilemma": self.core_dilemma,
            "inner_voices": self.inner_voices,
            "values": self.values,
            "tactics": self.tactics,
            "emotions": self.emotions,
            "stakeholders": self.stakeholders,
            "constraints": self.constraints,
            "deltas": self.deltas,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExtractedInfo":
        return cls(
            core_dilemma=payload.get("core_dilemma"),
            inner_voices=list(payload.get("inner_voices") or []),
            values=list(payload.get("values") or []),
            tactics=list(payload.get("tactics") or []),
            emotions=list(payload.get("emotions") or []),
            stakeholders=list(payload.get("stakeholders") or []),
            constraints=list(payload.get("constraints") or []),
            deltas=[item for item in (payload.get("deltas") or []) if isinstance(item, dict)],
        )


@dataclass
class SaturationSignals:
    depth_saturated: bool = False
    theme_saturated: bool = False
    emotion_settled: bool = False
    spontaneous_integration: bool = False

    def count_true(self) -> int:
        return sum(
            [
                self.depth_saturated,
                self.theme_saturated,
                self.emotion_settled,
                self.spontaneous_integration,
            ]
        )

    def to_dict(self) -> dict[str, bool]:
        return {
            "depth_saturated": self.depth_saturated,
            "theme_saturated": self.theme_saturated,
            "emotion_settled": self.emotion_settled,
            "spontaneous_integration": self.spontaneous_integration,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SaturationSignals":
        return cls(
            depth_saturated=bool(payload.get("depth_saturated", False)),
            theme_saturated=bool(payload.get("theme_saturated", False)),
            emotion_settled=bool(payload.get("emotion_settled", False)),
            spontaneous_integration=bool(payload.get("spontaneous_integration", False)),
        )


@dataclass
class DepthEvaluation:
    depth_score: float
    depth_layer: int
    saturation_signals: SaturationSignals
    readiness_score: float
    recommended_action: str
    strategy_hint: str
    reasoning: str = ""
    emotional_state: str = "calm"
    graduation_ready: bool = False
    graduation_evidence: str = ""
    tension_visible: bool = False
    tension_owned: bool = False
    unattended_card_ids: list[str] = field(default_factory=list)
    coverage_probe: str = ""
    layer_up_gap: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "depth_score": self.depth_score,
            "depth_layer": self.depth_layer,
            "saturation_signals": self.saturation_signals.to_dict(),
            "readiness_score": self.readiness_score,
            "recommended_action": self.recommended_action,
            "strategy_hint": self.strategy_hint,
            "reasoning": self.reasoning,
            "emotional_state": self.emotional_state,
            "graduation_ready": self.graduation_ready,
            "graduation_evidence": self.graduation_evidence,
            "tension_visible": self.tension_visible,
            "tension_owned": self.tension_owned,
            "unattended_card_ids": self.unattended_card_ids,
            "coverage_probe": self.coverage_probe,
            "layer_up_gap": self.layer_up_gap,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DepthEvaluation":
        return cls(
            depth_score=float(payload.get("depth_score", 0.0)),
            depth_layer=int(payload.get("depth_layer", 1)),
            saturation_signals=SaturationSignals.from_dict(payload.get("saturation_signals") or {}),
            readiness_score=float(payload.get("readiness_score", 0.0)),
            recommended_action=str(payload.get("recommended_action", "continue")),
            strategy_hint=str(payload.get("strategy_hint", "grounding")),
            reasoning=str(payload.get("reasoning", "")),
            emotional_state=_pick_text(payload.get("emotional_state"), "calm"),
            graduation_ready=_coerce_bool(payload.get("graduation_ready"), False),
            graduation_evidence=_pick_text(payload.get("graduation_evidence")),
            tension_visible=_coerce_bool(payload.get("tension_visible"), False),
            tension_owned=_coerce_bool(payload.get("tension_owned"), False),
            unattended_card_ids=_normalize_string_list(payload.get("unattended_card_ids")),
            coverage_probe=_pick_text(payload.get("coverage_probe")),
            layer_up_gap=_pick_text(payload.get("layer_up_gap")),
        )


@dataclass
class CardLayer:
    description: str
    user_language: str
    round_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "user_language": self.user_language,
            "round_index": self.round_index,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CardLayer":
        return cls(
            description=_pick_text(payload.get("description"), payload.get("summary")),
            user_language=_pick_text(
                payload.get("user_language"),
                payload.get("quote"),
                payload.get("raw_quote"),
                payload.get("description"),
            ),
            round_index=_coerce_int(payload.get("round_index") or payload.get("round"), 0),
        )


@dataclass
class TensionCard:
    id: str
    raw_quote: str
    pole_a: Optional[str] = None
    pole_b: Optional[str] = None
    layers: list[CardLayer] = field(default_factory=list)
    status: str = "surfaced"
    source_round: int = 1
    last_evidence_round: Optional[int] = None
    last_focus_round: Optional[int] = None
    intensity_hint: float = 0.5
    kind: str = "undecided"
    candidates: list[str] = field(default_factory=list)
    threads: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "raw_quote": self.raw_quote,
            "pole_a": self.pole_a,
            "pole_b": self.pole_b,
            "kind": self.kind,
            "candidates": self.candidates,
            "threads": self.threads,
            "layers": [layer.to_dict() for layer in self.layers],
            "status": self.status,
            "source_round": self.source_round,
            "last_evidence_round": self.last_evidence_round,
            "last_focus_round": self.last_focus_round,
            "intensity_hint": self.intensity_hint,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TensionCard":
        if isinstance(payload, TensionCard):
            payload = payload.to_dict()

        raw_layers = payload.get("layers") or []
        layers = [
            item if isinstance(item, CardLayer) else CardLayer.from_dict(item)
            for item in raw_layers
            if isinstance(item, (dict, CardLayer))
        ]
        pole_a = _pick_text(payload.get("pole_a")) or None
        pole_b = _pick_text(payload.get("pole_b")) or None
        candidates = _normalize_string_list(payload.get("candidates"))
        threads = _normalize_string_list(payload.get("threads"))
        kind = _pick_text(payload.get("kind")).lower()
        if kind not in {"bipolar", "undecided", "tangled"}:
            if pole_a and pole_b:
                kind = "bipolar"
            elif candidates:
                kind = "undecided"
            elif len(threads) >= 2:
                kind = "tangled"
            else:
                kind = "undecided"

        return cls(
            id=_pick_text(payload.get("id"), payload.get("card_id")),
            raw_quote=_pick_text(payload.get("raw_quote"), payload.get("quote")),
            pole_a=pole_a,
            pole_b=pole_b,
            layers=layers,
            status=_pick_text(payload.get("status"), "surfaced"),
            source_round=_coerce_int(payload.get("source_round"), 1),
            last_evidence_round=(
                _coerce_int(payload.get("last_evidence_round"), 0)
                if payload.get("last_evidence_round") is not None
                else None
            ),
            last_focus_round=(
                _coerce_int(payload.get("last_focus_round"), 0)
                if payload.get("last_focus_round") is not None
                else None
            ),
            intensity_hint=_coerce_unit_interval(payload.get("intensity_hint"), 0.5),
            kind=kind,
            candidates=candidates,
            threads=threads,
        )


@dataclass
class DilemmaLayer:
    description: str
    depth: str
    user_language: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "depth": self.depth,
            "user_language": self.user_language,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DilemmaLayer":
        description = _pick_text(
            payload.get("description"),
            payload.get("summary"),
            payload.get("analytic_summary"),
            payload.get("layer_summary"),
        )
        depth = _extract_depth_label(payload.get("depth") or payload.get("layer"))
        user_language = _pick_text(
            payload.get("user_language"),
            payload.get("quote"),
            payload.get("original_language"),
            payload.get("user_words"),
            payload.get("theme"),
        )
        if not description:
            description = _pick_text(payload.get("theme"), user_language)
        if not user_language:
            user_language = _pick_text(payload.get("theme"), description)
        return cls(
            description=description,
            depth=depth,
            user_language=user_language,
        )


@dataclass
class InnerVoice:
    name: str
    core_concern: str
    protective_intent: str
    intensity: float
    fear: str = ""
    language_style: str = ""
    typical_phrases: list[str] = field(default_factory=list)
    relationship_to_others: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "core_concern": self.core_concern,
            "protective_intent": self.protective_intent,
            "intensity": self.intensity,
            "fear": self.fear,
            "language_style": self.language_style,
            "typical_phrases": self.typical_phrases,
            "relationship_to_others": self.relationship_to_others,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "InnerVoice":
        core_concern = _pick_text(
            payload.get("core_concern"),
            payload.get("message"),
            payload.get("concern"),
        )
        return cls(
            name=_pick_text(payload.get("name"), payload.get("voice"), "Unnamed voice"),
            core_concern=core_concern,
            protective_intent=_pick_text(payload.get("protective_intent"), payload.get("intent")),
            intensity=_extract_intensity(payload, 0.5),
            fear=_pick_text(payload.get("fear")),
            language_style=_pick_text(payload.get("language_style")),
            typical_phrases=_normalize_string_list(payload.get("typical_phrases")),
            relationship_to_others=_normalize_relationships(payload.get("relationship_to_others")),
        )


@dataclass
class Tension:
    pole_a: str
    pole_b: str
    user_evidence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pole_a": self.pole_a,
            "pole_b": self.pole_b,
            "user_evidence": self.user_evidence,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Tension":
        return cls(
            pole_a=_pick_text(payload.get("pole_a")),
            pole_b=_pick_text(payload.get("pole_b")),
            user_evidence=_pick_text(payload.get("user_evidence"), payload.get("description")),
        )


@dataclass
class EmotionEntry:
    emotion: str
    context: str
    intensity: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "emotion": self.emotion,
            "context": self.context,
            "intensity": self.intensity,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any], default_context: str = "") -> "EmotionEntry":
        return cls(
            emotion=_pick_text(payload.get("emotion"), payload.get("name")),
            context=_pick_text(payload.get("context"), payload.get("source"), payload.get("trigger"), default_context),
            intensity=_extract_intensity(payload, 0.5),
        )


@dataclass
class ValueConflict:
    value_a: str
    value_b: str
    context: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "value_a": self.value_a,
            "value_b": self.value_b,
            "context": self.context,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ValueConflict":
        value_a = _pick_text(payload.get("value_a"))
        value_b = _pick_text(payload.get("value_b"))
        return cls(
            value_a=value_a,
            value_b=value_b,
            context=_pick_text(
                payload.get("context"),
                payload.get("tension_description"),
                f"{value_a} vs {value_b}".strip(),
            ),
        )


@dataclass
class Stakeholder:
    name: str
    role_in_dilemma: str
    user_feeling: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role_in_dilemma": self.role_in_dilemma,
            "user_feeling": self.user_feeling,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Stakeholder":
        return cls(
            name=_pick_text(payload.get("name")),
            role_in_dilemma=_pick_text(payload.get("role_in_dilemma"), payload.get("role")),
            user_feeling=_pick_text(payload.get("user_feeling"), payload.get("feeling")),
        )


@dataclass
class ElicitationOutcome:
    core_dilemma: str
    dilemma_layers: list[DilemmaLayer] = field(default_factory=list)
    inner_voices: list[InnerVoice] = field(default_factory=list)
    core_tensions: list[Tension] = field(default_factory=list)
    emotion_map: list[EmotionEntry] = field(default_factory=list)
    value_conflicts: list[ValueConflict] = field(default_factory=list)
    stakeholders: list[Stakeholder] = field(default_factory=list)
    conversation_depth: float = 0.0
    max_depth_reached: float = 0.0
    total_rounds: int = 0
    depth_trajectory: list[float] = field(default_factory=list)
    key_expressions: list[str] = field(default_factory=list)
    closing_readiness: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "core_dilemma": self.core_dilemma,
            "dilemma_layers": [item.to_dict() for item in self.dilemma_layers],
            "inner_voices": [item.to_dict() for item in self.inner_voices],
            "core_tensions": [item.to_dict() for item in self.core_tensions],
            "emotion_map": [item.to_dict() for item in self.emotion_map],
            "value_conflicts": [item.to_dict() for item in self.value_conflicts],
            "stakeholders": [item.to_dict() for item in self.stakeholders],
            "conversation_depth": self.conversation_depth,
            "max_depth_reached": self.max_depth_reached,
            "total_rounds": self.total_rounds,
            "depth_trajectory": self.depth_trajectory,
            "key_expressions": self.key_expressions,
            "closing_readiness": self.closing_readiness,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ElicitationOutcome":
        core_dilemma = payload.get("core_dilemma", "")
        if isinstance(core_dilemma, dict):
            core_dilemma = _pick_text(
                core_dilemma.get("summary"),
                core_dilemma.get("inferred_problem"),
                core_dilemma.get("description"),
            )

        emotion_map = payload.get("emotion_map") or []
        normalized_emotions: list[EmotionEntry] = []
        if isinstance(emotion_map, dict):
            for key in ("explicitly_stated", "implicitly_suggested"):
                for item in emotion_map.get(key) or []:
                    if isinstance(item, dict):
                        normalized_emotions.append(EmotionEntry.from_dict(item))
        else:
            normalized_emotions = [
                EmotionEntry.from_dict(item)
                for item in emotion_map
                if isinstance(item, dict)
            ]

        depth_trajectory = []
        for item in (payload.get("depth_trajectory") or []):
            if isinstance(item, dict):
                depth_trajectory.append(_coerce_float(item.get("depth")))
            else:
                depth_trajectory.append(_coerce_float(item))

        key_expressions = []
        for item in (payload.get("key_expressions") or []):
            if isinstance(item, dict):
                text = _pick_text(item.get("text"))
            else:
                text = _pick_text(item)
            if text:
                key_expressions.append(text)

        return cls(
            core_dilemma=_pick_text(core_dilemma),
            dilemma_layers=[
                DilemmaLayer.from_dict(item)
                for item in (payload.get("dilemma_layers") or [])
                if isinstance(item, dict)
            ],
            inner_voices=[
                InnerVoice.from_dict(item)
                for item in (payload.get("inner_voices") or [])
                if isinstance(item, dict)
            ],
            core_tensions=[
                Tension.from_dict(item)
                for item in (payload.get("core_tensions") or [])
                if isinstance(item, dict)
            ],
            emotion_map=normalized_emotions,
            value_conflicts=[
                ValueConflict.from_dict(item)
                for item in (payload.get("value_conflicts") or [])
                if isinstance(item, dict)
            ],
            stakeholders=[
                Stakeholder.from_dict(item)
                for item in (payload.get("stakeholders") or [])
                if isinstance(item, dict)
            ],
            conversation_depth=_extract_metric(payload.get("conversation_depth"), "final_depth", "depth", "score"),
            max_depth_reached=_extract_metric(payload.get("max_depth_reached"), "depth", "score"),
            total_rounds=_coerce_int(payload.get("total_rounds"), 0),
            depth_trajectory=depth_trajectory,
            key_expressions=key_expressions,
            closing_readiness=_extract_metric(payload.get("closing_readiness"), "score", "readiness", "final_readiness"),
        )


def best_outcome_context_text(outcome: ElicitationOutcome) -> str:
    for layer in outcome.dilemma_layers:
        text = _pick_text(layer.user_language)
        if text:
            return text

    return _pick_text(outcome.core_dilemma)


def default_extracted_info() -> dict[str, Any]:
    return ExtractedInfo().to_dict()
