"""Portrait routes for the unified post-elicitation experience."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException

from app.api.routes import sessions as sessions_routes
from app.models.elicitation import (
    DepthEvaluation,
    DilemmaLayer,
    ElicitationOutcome,
    EmotionEntry,
    InnerVoice,
    Stakeholder,
    Tension,
    TensionCard,
    ValueConflict,
)
from app.services.conflict_profile import ConflictProfileGenerator
from app.services.file_store import (
    load_elicitation,
    load_elicitation_outcome,
    load_portrait,
    load_session_meta,
    save_conflict_profile,
    save_elicitation_outcome,
    save_identity_cards,
    save_portrait,
    save_session_meta,
)
from app.services.framing import apply_framing
from app.services.identity_card import BASE_CARDS, build_identity_cards
from app.services.outcome_extractor import OutcomeExtractor, core_tensions_from_cards
from app.services.portrait_language_refiner import PortraitLanguageRefiner
from app.services.portrait_composer import Portrait, PortraitComposer
from app.services.portrait_display import PortraitDisplayComposer
from app.services.psyche.builder import PsycheBundleBuilder, bundle_or_legacy

router = APIRouter(prefix="/sessions", tags=["portrait"])

_portrait_composer = PortraitComposer()
_outcome_extractor = OutcomeExtractor()
_portrait_language_refiner = PortraitLanguageRefiner()
_portrait_display_composer = PortraitDisplayComposer()
_profile_generator = ConflictProfileGenerator()
_psyche_builder = PsycheBundleBuilder()
_ALLOWED_STATUSES = {"portrait_pending", "profile_pending", "complexity_pending", "identity_pending"}
_LEVEL_CONFIG: dict[str, tuple[int, int]] = {
    "L1": (2, 3),
    "L2": (4, 4),
    "L3": (5, 5),
}


def _get_repo():
    return sessions_routes._session_repo


def _ensure_portrait_status(status: str) -> None:
    if status not in _ALLOWED_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={"expected_statuses": sorted(_ALLOWED_STATUSES), "current_status": status},
        )


def _attach_quality_metadata(payload: dict[str, Any], row: Any) -> dict[str, Any]:
    history = row.elicitation_history if isinstance(row.elicitation_history, dict) else {}
    quality = history.get("portrait_quality")
    if quality is not None:
        payload["portrait_quality"] = quality
        payload["quality_forced"] = bool(history.get("quality_forced", False))
    return payload


def _attach_display_payload(
    payload: dict[str, Any],
    outcome: ElicitationOutcome,
    portrait: Portrait,
    row: Any,
) -> dict[str, Any]:
    history = row.elicitation_history if isinstance(row.elicitation_history, dict) else {}
    payload["display"] = _portrait_display_composer.compose(
        outcome,
        portrait,
        quality=history.get("portrait_quality"),
    )
    return payload


def _outcome_from_profile(profile: dict[str, Any]) -> ElicitationOutcome:
    profile = profile or {}
    tensions: list[Tension] = []
    for item in profile.get("core_tensions") or []:
        if isinstance(item, dict):
            tensions.append(Tension.from_dict(item))
        else:
            text = str(item).strip()
            if text:
                tensions.append(Tension(pole_a="", pole_b="", user_evidence=text))

    emotion_map = []
    emotional_tone = profile.get("emotional_tone") or {}
    intensity_map = emotional_tone.get("emotions", emotional_tone) if isinstance(emotional_tone, dict) else {}
    for emotion in profile.get("emotions") or []:
        text = str(emotion).strip()
        if text:
            emotion_map.append(
                EmotionEntry(
                    emotion=text,
                    context="",
                    intensity=float(intensity_map.get(text, 0.5)) if isinstance(intensity_map, dict) else 0.5,
                )
            )

    stakeholders = []
    for item in profile.get("stakeholders") or []:
        if isinstance(item, dict):
            stakeholders.append(Stakeholder.from_dict(item))
        else:
            text = str(item).strip()
            if text:
                stakeholders.append(Stakeholder(name=text, role_in_dilemma="", user_feeling=""))

    value_conflicts = []
    for item in profile.get("value_conflicts") or []:
        if isinstance(item, dict):
            value_conflicts.append(ValueConflict.from_dict(item))

    dilemma_layers = []
    for item in profile.get("dilemmas") or []:
        text = str(item).strip()
        if text:
            dilemma_layers.append(DilemmaLayer(description=text, depth="surface", user_language=text))

    return ElicitationOutcome(
        core_dilemma=str(profile.get("core_dilemma") or ""),
        dilemma_layers=dilemma_layers,
        inner_voices=[
            InnerVoice.from_dict(item)
            for item in (profile.get("inner_voices") or [])
            if isinstance(item, dict)
        ],
        core_tensions=tensions,
        emotion_map=emotion_map,
        value_conflicts=value_conflicts,
        stakeholders=stakeholders,
        conversation_depth=float(profile.get("conversation_depth") or 0.0),
        max_depth_reached=float(profile.get("max_depth_reached") or 0.0),
        depth_trajectory=[float(item) for item in (profile.get("depth_trajectory") or [])],
        closing_readiness=float(profile.get("closing_readiness") or 0.0),
    )


def _semantic_version() -> str:
    return PortraitLanguageRefiner.SEMANTIC_VERSION


def _should_skip_semantic_refresh(meta: dict[str, Any]) -> bool:
    attempted = meta.get("portrait_semantic_attempted_version")
    applied = meta.get("portrait_semantic_version")
    return attempted == _semantic_version() and applied != _semantic_version()


def _semantic_refresh_already_applied(meta: dict[str, Any]) -> bool:
    return meta.get("portrait_semantic_version") == _semantic_version()


async def _maybe_refresh_outcome(session_id: UUID, outcome: ElicitationOutcome) -> tuple[ElicitationOutcome, bool]:
    if not _portrait_language_refiner.needs_refinement(outcome):
        return outcome, False

    meta = load_session_meta(session_id)
    if _semantic_refresh_already_applied(meta):
        return outcome, False
    if _should_skip_semantic_refresh(meta):
        return outcome, False

    elicitation_payload = load_elicitation(session_id)
    conversation_history = elicitation_payload.get("conversation_history") or []
    depth_evaluations = [
        DepthEvaluation.from_dict(item)
        for item in (elicitation_payload.get("depth_evaluations") or [])
        if isinstance(item, dict)
    ]
    refinement = await _portrait_language_refiner.refine(
        outcome,
        conversation_history=conversation_history,
        depth_evaluations=depth_evaluations,
    )

    save_session_meta(
        session_id,
        {
            "portrait_semantic_attempted_version": _semantic_version(),
        },
    )

    if not refinement.improved:
        return outcome, False

    save_session_meta(
        session_id,
        {
            "portrait_semantic_version": _semantic_version(),
        },
    )
    save_elicitation_outcome(session_id, refinement.outcome.to_dict())
    return refinement.outcome, True


def _portrait_needs_refresh(payload: dict[str, Any]) -> bool:
    if not payload:
        return False

    outcome = ElicitationOutcome.from_dict(
        {
            "core_dilemma": payload.get("core_dilemma"),
            "dilemma_layers": payload.get("dilemma_layers") or [],
            "inner_voices": payload.get("inner_voices") or [],
            "emotion_map": payload.get("emotion_map") or [],
        }
    )
    return _portrait_language_refiner.needs_refinement(outcome)


async def _load_outcome(session_id: UUID, row: Any) -> tuple[ElicitationOutcome, bool]:
    persisted = load_elicitation_outcome(session_id)
    if persisted:
        outcome, refreshed = await _maybe_refresh_outcome(session_id, ElicitationOutcome.from_dict(persisted))
        return _backfill_core_tensions(outcome, row), refreshed
    if row.conflict_profile_snapshot:
        outcome, refreshed = await _maybe_refresh_outcome(session_id, _outcome_from_profile(dict(row.conflict_profile_snapshot)))
        return _backfill_core_tensions(outcome, row), refreshed
    raise HTTPException(status_code=400, detail="No elicitation outcome available for portrait generation")


async def _load_or_compose_portrait(
    session_id: UUID,
    row: Any,
    outcome: ElicitationOutcome,
    *,
    force_recompose: bool = False,
) -> Portrait:
    cached = load_portrait(session_id)
    if cached and not force_recompose and not _portrait_needs_refresh(cached):
        return Portrait.from_dict(cached)
    portrait = await _portrait_composer.compose(outcome, framing_preference=row.framing_preference)
    save_portrait(session_id, portrait.to_dict())
    return portrait


def _override_profile_config(profile: dict[str, Any], level: str) -> dict[str, Any]:
    agent_count, max_rounds = _LEVEL_CONFIG.get(level, _LEVEL_CONFIG["L2"])
    profile["debate_level"] = level
    profile["agent_count"] = agent_count
    profile["max_rounds"] = max_rounds
    return profile


def _serialize_assignments(portrait: Portrait) -> list[dict[str, Any]]:
    return [item.to_dict() for item in portrait.agent_assignments]


def _tension_cards_from_row(row: Any) -> list[dict[str, Any]]:
    history = row.elicitation_history if isinstance(row.elicitation_history, dict) else {}
    cards = history.get("tension_cards")
    if not isinstance(cards, list):
        return []
    return [item for item in cards if isinstance(item, dict)]


def _backfill_core_tensions(outcome: ElicitationOutcome, row: Any) -> ElicitationOutcome:
    if outcome.core_tensions:
        return outcome
    cards = [TensionCard.from_dict(item) for item in _tension_cards_from_row(row)]
    derived = core_tensions_from_cards(cards)
    if derived:
        outcome.core_tensions = derived
    return outcome


def _portrait_imprint_input(portrait: Portrait, row: Any) -> dict[str, Any]:
    kept_voices = [
        str(voice.get("name") or "")
        for voice in portrait.inner_voices
        if isinstance(voice, dict) and str(voice.get("name") or "").strip()
    ]
    return {
        "agent_assignments": _serialize_assignments(portrait),
        "kept_voice_names": kept_voices,
        "renamed_voices": {},
    }


def _merge_bundle_imprint(profile: dict[str, Any], portrait: Portrait, row: Any) -> None:
    bundle = bundle_or_legacy(profile)
    merged = _psyche_builder.merge_portrait(bundle, _portrait_imprint_input(portrait, row))
    profile["psyche_bundle"] = merged.to_dict()


def _build_assigned_identity_cards(
    profile: dict[str, Any],
    framing_preference: str | None,
    assignments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not assignments:
        return build_identity_cards(profile, framing_preference)

    voices = profile.get("inner_voices") or []
    voice_lookup = {
        voice.get("name"): voice
        for voice in voices
        if isinstance(voice, dict) and voice.get("name")
    }
    dilemma = profile.get("core_dilemma") or "Inner conflict"
    cards = []
    for assignment in assignments:
        role = assignment.get("agent_role") or "Empathic Listener"
        base = dict(BASE_CARDS.get(role, BASE_CARDS["Empathic Listener"]))
        base["role"] = role
        base["display_name"] = apply_framing(framing_preference or "neutral", role)
        base["agent_id"] = role.replace(" ", "_").lower()

        voice = voice_lookup.get(assignment.get("voice_name") or "")
        if voice:
            concern = voice.get("core_concern") or voice.get("name") or dilemma
            base["anchored_voice"] = voice
            base["specific_concern"] = concern
            base["system_prompt"] = (
                base["system_prompt"]
                + f"\n\n本次辩论中你特别代表用户内心的声音：{voice.get('name', '')}，关切：{concern}。用户的困境是：{dilemma}。"
            )
        else:
            base["specific_concern"] = dilemma

        addon = assignment.get("system_prompt_addon")
        if addon:
            base["system_prompt"] = base["system_prompt"] + f"\n\n{addon}"

        cards.append(base)
    return cards


@router.get("/{session_id}/portrait")
async def get_portrait(session_id: UUID):
    repo = _get_repo()
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    _ensure_portrait_status(row.status)

    outcome, refreshed = await _load_outcome(session_id, row)
    portrait = await _load_or_compose_portrait(session_id, row, outcome, force_recompose=refreshed)
    payload = _attach_quality_metadata(portrait.to_dict(), row)
    return _attach_display_payload(payload, outcome, portrait, row)


@router.put("/{session_id}/portrait")
async def put_portrait(session_id: UUID, body: dict[str, Any] = Body(...)):
    repo = _get_repo()
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    _ensure_portrait_status(row.status)

    outcome, refreshed = await _load_outcome(session_id, row)
    portrait = await _load_or_compose_portrait(session_id, row, outcome, force_recompose=refreshed)

    if "core_dilemma" in body and isinstance(body.get("core_dilemma"), str):
        outcome.core_dilemma = body["core_dilemma"].strip() or outcome.core_dilemma
    if "inner_voices" in body and isinstance(body.get("inner_voices"), list):
        outcome.inner_voices = [
            InnerVoice.from_dict(item)
            for item in body["inner_voices"]
            if isinstance(item, dict)
        ]

    level_override = body.get("debate_level")
    portrait = await _portrait_composer.recompose_council(
        outcome,
        portrait,
        level_override=level_override if isinstance(level_override, str) else None,
        framing_preference=row.framing_preference,
    )

    updated_profile = _override_profile_config(
        _profile_generator.generate_from_outcome(
            outcome,
            tension_cards=_tension_cards_from_row(row),
        ),
        portrait.complexity.level,
    )
    _merge_bundle_imprint(updated_profile, portrait, row)
    await repo.update_profile(
        session_id,
        conflict_profile_snapshot=updated_profile,
        debate_level=portrait.complexity.level,
        agent_count=portrait.complexity.agent_count,
        max_rounds=portrait.complexity.max_rounds,
    )
    save_elicitation_outcome(session_id, outcome.to_dict())
    save_conflict_profile(session_id, updated_profile)
    save_portrait(session_id, portrait.to_dict())
    payload = _attach_quality_metadata(portrait.to_dict(), row)
    return _attach_display_payload(payload, outcome, portrait, row)


@router.post("/{session_id}/portrait/confirm")
async def confirm_portrait(session_id: UUID, body: dict[str, Any] | None = Body(default=None)):
    repo = _get_repo()
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    _ensure_portrait_status(row.status)

    outcome, refreshed = await _load_outcome(session_id, row)
    portrait = await _load_or_compose_portrait(session_id, row, outcome, force_recompose=refreshed)

    level_override = body.get("debate_level") if isinstance(body, dict) else None
    if isinstance(level_override, str) and level_override != portrait.complexity.level:
        portrait = await _portrait_composer.recompose_council(
            outcome,
            portrait,
            level_override=level_override,
            framing_preference=row.framing_preference,
        )

    profile = _override_profile_config(
        _profile_generator.generate_from_outcome(
            outcome,
            tension_cards=_tension_cards_from_row(row),
        ),
        portrait.complexity.level,
    )
    _merge_bundle_imprint(profile, portrait, row)
    assignments = _serialize_assignments(portrait)
    identity_cards = _build_assigned_identity_cards(profile, row.framing_preference, assignments)

    await repo.update_profile(
        session_id,
        conflict_profile_snapshot=profile,
        identity_cards_snapshot=identity_cards,
        debate_level=portrait.complexity.level,
        agent_count=portrait.complexity.agent_count,
        max_rounds=portrait.complexity.max_rounds,
    )
    await repo.update_status(session_id, "debating")

    save_elicitation_outcome(session_id, outcome.to_dict())
    save_conflict_profile(session_id, profile)
    save_identity_cards(session_id, identity_cards)
    save_portrait(session_id, portrait.to_dict())
    save_session_meta(
        session_id,
        {
            "status": "debating",
            "debate_level": portrait.complexity.level,
            "agent_count": portrait.complexity.agent_count,
            "max_rounds": portrait.complexity.max_rounds,
        },
    )
    return {"ok": True, "status": "debating"}
