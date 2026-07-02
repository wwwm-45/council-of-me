"""Phase 1 elicitation and profile-confirm routes."""

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse

from app.api.routes.sessions import _session_repo
from app.api.schemas.elicitation import (
    DepthInfo,
    ElicitationBody,
    ElicitationEditBody,
    ElicitationFinishBody,
    ElicitationResponse,
    ProfileConfirmBody,
)
from app.models.elicitation import DepthEvaluation, ElicitationOutcome, default_extracted_info
from app.repositories.session_repo import SessionRepository
from app.services.conflict_profile import ConflictProfileGenerator
from app.services.elicitation import ElicitationService
from app.services.elicitation_control import build_process_intent_notice, filter_process_turns, is_process_intent
from app.services.file_store import load_session_meta, save_conflict_profile, save_elicitation, save_elicitation_outcome, save_session_meta
from app.services.outcome_extractor import OutcomeExtractor
from app.services.portrait_quality import PortraitQualityGate, PortraitQualityResult
from app.services.safety import CRISIS_RESOURCES, SafetyLevel, SafetyMonitor

router = APIRouter(prefix="/sessions", tags=["elicitation"])
_safety_monitor = SafetyMonitor()
_elicitation_service = ElicitationService()
_outcome_extractor = OutcomeExtractor()
_portrait_quality_gate = PortraitQualityGate()


def _get_repo() -> SessionRepository:
    return _session_repo


def _session_display_name(session_id: UUID) -> str:
    return str(load_session_meta(session_id).get("display_name") or "").strip()[:40]


def _coerce_tension_cards(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _coerce_focus_card_id(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _load_elicitation_state(
    elicitation_history: Any,
) -> tuple[
    list,
    dict[str, Any],
    int,
    list[DepthEvaluation],
    int,
    int,
    int,
    bool,
    int,
    list[dict[str, Any]],
    str | None,
    int,
    bool,
    int,  # consecutive_intervene_count
    list[dict[str, Any]],
]:
    if not elicitation_history or not isinstance(elicitation_history, dict):
        return [], default_extracted_info(), 0, [], 0, 1, 0, False, 0, [], None, 0, False, 0, []

    history = elicitation_history.get("conversation_history") or []
    extracted_info = elicitation_history.get("extracted_info") or default_extracted_info()
    round_count = int(elicitation_history.get("round_count") or 0)
    depth_evaluations = [
        DepthEvaluation.from_dict(item)
        for item in (elicitation_history.get("depth_evaluations") or [])
        if isinstance(item, dict)
    ]
    closing_revert_count = int(elicitation_history.get("closing_revert_count") or 0)
    current_layer = int(elicitation_history.get("current_layer") or 1)
    layer_round_count = int(elicitation_history.get("layer_round_count") or 0)
    is_containment = bool(elicitation_history.get("is_containment", False))
    containment_round_count = int(elicitation_history.get("containment_round_count") or 0)
    tension_cards = _coerce_tension_cards(elicitation_history.get("tension_cards"))
    focus_card_id = _coerce_focus_card_id(elicitation_history.get("focus_card_id"))
    l1_own_count = int(elicitation_history.get("l1_own_count") or 0)
    tension_probed_seen = bool(elicitation_history.get("tension_probed_seen", False))
    consecutive_intervene_count = int(elicitation_history.get("consecutive_intervene_count") or 0)
    focus_trace = elicitation_history.get("focus_trace")
    if not isinstance(focus_trace, list):
        focus_trace = []
    focus_trace = [item for item in focus_trace if isinstance(item, dict)]
    return (
        history,
        extracted_info,
        round_count,
        depth_evaluations,
        closing_revert_count,
        current_layer,
        layer_round_count,
        is_containment,
        containment_round_count,
        tension_cards,
        focus_card_id,
        l1_own_count,
        tension_probed_seen,
        consecutive_intervene_count,
        focus_trace,
    )


def _build_elicitation_state(
    *,
    history: list,
    extracted_info: dict[str, Any],
    round_count: int,
    depth_evaluations: list[DepthEvaluation],
    closing_revert_count: int,
    current_layer: int,
    layer_round_count: int,
    is_containment: bool,
    containment_round_count: int,
    tension_cards: list[dict[str, Any]],
    focus_card_id: str | None,
    l1_own_count: int = 0,
    tension_probed_seen: bool = False,
    consecutive_intervene_count: int = 0,
    focus_trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "conversation_history": history,
        "extracted_info": extracted_info,
        "round_count": round_count,
        "depth_evaluations": [evaluation.to_dict() for evaluation in depth_evaluations],
        "closing_revert_count": closing_revert_count,
        "current_layer": current_layer,
        "layer_round_count": layer_round_count,
        "is_containment": is_containment,
        "containment_round_count": containment_round_count,
        "tension_cards": tension_cards,
        "focus_card_id": focus_card_id,
        "l1_own_count": l1_own_count,
        "tension_probed_seen": tension_probed_seen,
        "consecutive_intervene_count": consecutive_intervene_count,
        "focus_trace": focus_trace or [],
    }


def _depth_payload(evaluations: list[DepthEvaluation]) -> DepthInfo | None:
    last_evaluation = evaluations[-1] if evaluations else None
    if last_evaluation is None:
        return None

    active_layer = last_evaluation.depth_layer
    return DepthInfo(
        depth_score=last_evaluation.depth_score,
        depth_layer=active_layer,
        current_layer=active_layer,
        recommended_action=last_evaluation.recommended_action,
        strategy_hint=last_evaluation.strategy_hint,
        emotional_state=last_evaluation.emotional_state,
        graduation_ready=last_evaluation.graduation_ready,
    )


def _latest_user_turn_index(history: list[dict[str, Any]]) -> int | None:
    for index in range(len(history) - 1, -1, -1):
        item = history[index]
        if isinstance(item, dict) and item.get("role") == "user":
            return index
    return None


def _quality_payload(quality: PortraitQualityResult | dict[str, Any] | None) -> dict[str, Any] | None:
    if quality is None:
        return None
    if isinstance(quality, dict):
        return quality
    return quality.to_dict()


def _quality_status(quality: PortraitQualityResult | dict[str, Any] | None) -> str | None:
    payload = _quality_payload(quality)
    if not payload:
        return None
    return str(payload.get("status") or "")


def _unpack_elicitation_result(
    result: tuple[Any, ...],
    *,
    fallback_tension_cards: list[dict[str, Any]],
    fallback_focus_card_id: str | None,
) -> tuple[
    str,
    bool,
    dict[str, Any],
    list,
    list[DepthEvaluation],
    ElicitationOutcome | None,
    dict[str, Any],
    list[dict[str, Any]],
    str | None,
]:
    if len(result) == 7:
        response, should_continue, new_info, new_history, new_evaluations, outcome, next_state = result
        return (
            response,
            should_continue,
            new_info,
            new_history,
            new_evaluations,
            outcome,
            next_state,
            fallback_tension_cards,
            fallback_focus_card_id,
        )

    if len(result) != 8:
        raise ValueError(f"Unexpected elicitation result length: {len(result)}")

    response, should_continue, new_info, new_history, new_evaluations, outcome, next_state, metadata = result
    new_tension_cards = fallback_tension_cards
    new_focus_card_id = fallback_focus_card_id
    if isinstance(metadata, dict):
        if "tension_cards" in metadata:
            new_tension_cards = _coerce_tension_cards(metadata.get("tension_cards"))
        if "focus_card_id" in metadata:
            new_focus_card_id = _coerce_focus_card_id(metadata.get("focus_card_id"))

    return (
        response,
        should_continue,
        new_info,
        new_history,
        new_evaluations,
        outcome,
        next_state,
        new_tension_cards,
        new_focus_card_id,
    )


async def _extract_outcome(
    extractor: Any,
    history: list,
    depth_evaluations: list[DepthEvaluation],
    tension_cards: list[dict[str, Any]],
) -> ElicitationOutcome:
    try:
        return await extractor.extract(history, depth_evaluations, tension_cards=tension_cards)
    except TypeError as exc:
        if "tension_cards" not in str(exc):
            raise
        return await extractor.extract(history, depth_evaluations)


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _depth_evaluations_from_payload(value: Any) -> list[DepthEvaluation]:
    if not isinstance(value, list):
        return []
    return [DepthEvaluation.from_dict(item) for item in value if isinstance(item, dict)]


def _int_or_default(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _first_present(mapping: dict[str, Any], key: str, fallback: Any) -> Any:
    return mapping[key] if key in mapping else fallback


def _clamp_layer(value: Any) -> int:
    return min(max(_int_or_default(value, 1), 1), 3)


def _tension_probed_before_round(tension_cards: list[dict[str, Any]], round_count: int) -> bool:
    probed_statuses = {"probed", "layered", "saturated"}
    for card in tension_cards:
        if str(card.get("status") or "").strip() not in probed_statuses:
            continue
        source_round = _int_or_default(card.get("source_round"), 0)
        if source_round > round_count:
            continue
        last_evidence_round = card.get("last_evidence_round")
        if last_evidence_round is not None and _int_or_default(last_evidence_round, 0) > round_count:
            continue
        return True
    return False


def _filter_tension_cards_before_round(
    tension_cards: list[dict[str, Any]],
    round_count: int,
) -> list[dict[str, Any]]:
    safe_cards: list[dict[str, Any]] = []
    for card in tension_cards:
        last_evidence_round = card.get("last_evidence_round")
        if last_evidence_round is not None:
            if _int_or_default(last_evidence_round, round_count + 1) <= round_count:
                safe_cards.append(card)
            continue

        source_round = card.get("source_round")
        if source_round is not None:
            if _int_or_default(source_round, round_count + 1) <= round_count:
                safe_cards.append(card)
            continue

        safe_cards.append(card)
    return safe_cards


def _rollback_edit_generation_state(
    *,
    prior_round_count: int,
    prior_depth_evaluations: list[DepthEvaluation],
    current_layer: int,
    layer_round_count: int,
    is_containment: bool,
    containment_round_count: int,
    tension_cards: list[dict[str, Any]],
    l1_own_count: int,
) -> dict[str, Any]:
    previous_evaluation = prior_depth_evaluations[-1] if prior_depth_evaluations else None
    rollback_layer = _clamp_layer(previous_evaluation.depth_layer if previous_evaluation is not None else 1)
    rollback_layer_round_count = (
        max(layer_round_count - 1, 0)
        if _clamp_layer(current_layer) == rollback_layer
        else 0
    )
    rollback_is_containment = bool(
        previous_evaluation is not None
        and previous_evaluation.strategy_hint == "containment"
        and is_containment
    )
    rollback_containment_round_count = (
        max(containment_round_count - 1, 0)
        if rollback_is_containment
        else 0
    )
    return {
        "current_layer": rollback_layer,
        "layer_round_count": rollback_layer_round_count,
        "is_containment": rollback_is_containment,
        "containment_round_count": rollback_containment_round_count,
        "l1_own_count": 0,
        "tension_probed_seen": _tension_probed_before_round(tension_cards, prior_round_count),
        "consecutive_intervene_count": 0,
    }


async def _persist_finished_elicitation(
    *,
    repo: SessionRepository,
    session_id: UUID,
    state: dict[str, Any],
    outcome: ElicitationOutcome,
    conflict_profile_draft: dict[str, Any],
    quality: PortraitQualityResult | dict[str, Any] | None = None,
    quality_forced: bool | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    outcome_dict = outcome.to_dict()
    quality_payload = _quality_payload(quality)
    display_name = _session_display_name(session_id)
    if display_name:
        conflict_profile_draft = {
            **conflict_profile_draft,
            "user_display_name": display_name,
        }

    if quality_payload is not None:
        state["portrait_quality"] = quality_payload
    if quality_forced is not None:
        state["quality_forced"] = quality_forced
    state.pop("pending_finish_outcome", None)
    state.pop("pending_conflict_profile_draft", None)
    state.pop("pending_finish_quality", None)

    await repo.update_profile(
        session_id,
        elicitation_history=state,
        conflict_profile_snapshot=conflict_profile_draft,
    )
    await repo.update_status(session_id, "portrait_pending")
    save_elicitation(session_id, state)
    save_elicitation_outcome(session_id, outcome_dict)
    save_conflict_profile(session_id, conflict_profile_draft)
    save_session_meta(session_id, {"status": "portrait_pending"})
    return outcome_dict, conflict_profile_draft


async def _persist_stream_turn_end(
    *,
    repo: SessionRepository,
    session_id: UUID,
    row: Any,
    data: dict[str, Any],
    closing_revert_count: int,
) -> dict[str, Any]:
    next_state = data.get("next_state") if isinstance(data.get("next_state"), dict) else {}
    prior_state = row.elicitation_history if isinstance(getattr(row, "elicitation_history", None), dict) else {}
    l1_own_source = _first_present(
        next_state,
        "l1_own_count",
        _first_present(data, "l1_own_count", prior_state.get("l1_own_count", 0)),
    )
    tension_probed_source = _first_present(
        next_state,
        "tension_probed_seen",
        _first_present(data, "tension_probed_seen", prior_state.get("tension_probed_seen", False)),
    )
    depth_evaluations = _depth_evaluations_from_payload(data.get("depth_evaluations"))
    consecutive_intervene_source = _first_present(
        next_state,
        "consecutive_intervene_count",
        _first_present(data, "consecutive_intervene_count", prior_state.get("consecutive_intervene_count", 0)),
    )
    focus_trace_source = _first_present(
        next_state,
        "focus_trace",
        _first_present(data, "focus_trace", prior_state.get("focus_trace", [])),
    )
    if not isinstance(focus_trace_source, list):
        focus_trace_source = []
    state = _build_elicitation_state(
        history=data.get("conversation_history") if isinstance(data.get("conversation_history"), list) else [],
        extracted_info=data.get("extracted_info") if isinstance(data.get("extracted_info"), dict) else default_extracted_info(),
        round_count=int(data.get("round") or 0),
        depth_evaluations=depth_evaluations,
        closing_revert_count=closing_revert_count,
        current_layer=int(next_state.get("current_layer") or 1),
        layer_round_count=int(next_state.get("layer_round_count") or 0),
        is_containment=bool(next_state.get("is_containment", False)),
        containment_round_count=int(next_state.get("containment_round_count") or 0),
        tension_cards=_coerce_tension_cards(data.get("tension_cards")),
        focus_card_id=_coerce_focus_card_id(data.get("focus_card_id")),
        l1_own_count=_int_or_default(l1_own_source, 0),
        tension_probed_seen=bool(tension_probed_source),
        consecutive_intervene_count=_int_or_default(consecutive_intervene_source, 0),
        focus_trace=[item for item in focus_trace_source if isinstance(item, dict)],
    )
    state["last_turn_correction_applied"] = bool(data.get("correction_applied", False))
    state["last_turn_correction_count"] = _int_or_default(data.get("correction_count"), 0)
    await repo.update_profile(session_id, elicitation_history=state)
    save_elicitation(session_id, state)

    if row.crisis_returned_at is not None:
        await repo.update_profile(session_id, rounds_since_crisis_return=(row.rounds_since_crisis_return or 0) + 1)

    if data.get("should_continue") is False and isinstance(data.get("elicitation_outcome"), dict):
        outcome = ElicitationOutcome.from_dict(data["elicitation_outcome"])
        conflict_profile_draft = ConflictProfileGenerator().generate_from_outcome(
            outcome,
            tension_cards=_coerce_tension_cards(data.get("tension_cards")),
        )
        quality = _portrait_quality_gate.evaluate(outcome, conflict_profile_draft)
        persist_quality = quality if _quality_status(quality) == "warn" else None
        outcome_dict, conflict_profile_draft = await _persist_finished_elicitation(
            repo=repo,
            session_id=session_id,
            state=state,
            outcome=outcome,
            conflict_profile_draft=conflict_profile_draft,
            quality=persist_quality,
        )
        data = {
            **data,
            "elicitation_outcome": outcome_dict,
            "conflict_profile_draft": conflict_profile_draft,
            "portrait_quality": state.get("portrait_quality"),
        }

    return data


@router.post("/{session_id}/elicitation", response_model=ElicitationResponse)
async def post_elicitation(session_id: UUID, body: ElicitationBody):
    repo = _get_repo()
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.status != "eliciting":
        raise HTTPException(status_code=409, detail={"expected_status": "eliciting", "current_status": row.status})
    user_display_name = _session_display_name(session_id)

    use_lower = bool(row.crisis_returned_at) and (row.rounds_since_crisis_return or 0) < 5
    safety_result = _safety_monitor.check_input(body.message, context=[], use_lower_threshold=use_lower)
    if safety_result.level == SafetyLevel.CRITICAL:
        raise HTTPException(
            status_code=449,
            detail={
                "safety": "crisis",
                "resources": CRISIS_RESOURCES,
                "require_confirm_to_continue": True,
            },
        )
    safety_warning = "如需支持可查看页面底部资源链接。" if safety_result.level == SafetyLevel.WARNING else None

    (
        history,
        extracted_info,
        round_count,
        depth_evaluations,
        closing_revert_count,
        current_layer,
        layer_round_count,
        is_containment,
        containment_round_count,
        tension_cards,
        focus_card_id,
        l1_own_count,
        tension_probed_seen,
        consecutive_intervene_count,
        focus_trace,
    ) = _load_elicitation_state(row.elicitation_history)

    if is_process_intent(body.message):
        state = _build_elicitation_state(
            history=history,
            extracted_info=extracted_info,
            round_count=round_count,
            depth_evaluations=depth_evaluations,
            closing_revert_count=closing_revert_count,
            current_layer=current_layer,
            layer_round_count=layer_round_count,
            is_containment=is_containment,
            containment_round_count=containment_round_count,
            tension_cards=tension_cards,
            focus_card_id=focus_card_id,
            l1_own_count=l1_own_count,
            tension_probed_seen=tension_probed_seen,
            consecutive_intervene_count=consecutive_intervene_count,
            focus_trace=focus_trace,
        )
        await repo.update_profile(session_id, elicitation_history=state)
        save_elicitation(session_id, state)
        return ElicitationResponse(
            response=build_process_intent_notice(body.message),
            should_continue=True,
            round=round_count,
            depth=_depth_payload(depth_evaluations),
            conflict_profile_draft=None,
            elicitation_outcome=None,
            safety_warning=safety_warning,
            portrait_quality=None,
            requires_quality_confirmation=False,
            tension_cards=tension_cards,
            focus_card_id=focus_card_id,
        )

    (
        response,
        should_continue,
        new_info,
        new_history,
        new_evaluations,
        outcome,
        next_state,
        new_tension_cards,
        new_focus_card_id,
    ) = _unpack_elicitation_result(
        await _elicitation_service.generate_response(
            body.message,
            history,
            extracted_info,
            round_count,
            depth_evaluations,
            closing_revert_count,
            current_layer,
            layer_round_count,
            is_containment,
            containment_round_count,
            tension_cards=tension_cards,
            focus_card_id=focus_card_id,
            l1_own_count=l1_own_count,
            tension_probed_seen=tension_probed_seen,
            user_display_name=user_display_name,
            consecutive_intervene_count=consecutive_intervene_count,
            focus_trace=focus_trace,
        ),
        fallback_tension_cards=tension_cards,
        fallback_focus_card_id=focus_card_id,
    )

    new_round = round_count + 1
    state = _build_elicitation_state(
        history=new_history,
        extracted_info=new_info,
        round_count=new_round,
        depth_evaluations=new_evaluations,
        closing_revert_count=closing_revert_count,
        current_layer=next_state["current_layer"],
        layer_round_count=next_state["layer_round_count"],
        is_containment=next_state["is_containment"],
        containment_round_count=next_state["containment_round_count"],
        tension_cards=new_tension_cards,
        focus_card_id=new_focus_card_id,
        l1_own_count=int(next_state.get("l1_own_count", l1_own_count)),
        tension_probed_seen=bool(next_state.get("tension_probed_seen", tension_probed_seen)),
        consecutive_intervene_count=int(next_state.get("consecutive_intervene_count", consecutive_intervene_count)),
        focus_trace=next_state.get("focus_trace") if isinstance(next_state.get("focus_trace"), list) else focus_trace,
    )
    await repo.update_profile(session_id, elicitation_history=state)
    save_elicitation(session_id, state)

    if row.crisis_returned_at is not None:
        await repo.update_profile(session_id, rounds_since_crisis_return=(row.rounds_since_crisis_return or 0) + 1)

    conflict_profile_draft = None
    outcome_dict = None
    if not should_continue and outcome is not None:
        conflict_profile_draft = ConflictProfileGenerator().generate_from_outcome(
            outcome,
            tension_cards=new_tension_cards,
        )
        quality = _portrait_quality_gate.evaluate(outcome, conflict_profile_draft)
        persist_quality = quality if _quality_status(quality) == "warn" else None
        outcome_dict, conflict_profile_draft = await _persist_finished_elicitation(
            repo=repo,
            session_id=session_id,
            state=state,
            outcome=outcome,
            conflict_profile_draft=conflict_profile_draft,
            quality=persist_quality,
        )

    return ElicitationResponse(
        response=response,
        should_continue=should_continue,
        round=new_round,
        depth=_depth_payload(new_evaluations),
        conflict_profile_draft=conflict_profile_draft,
        elicitation_outcome=outcome_dict,
        safety_warning=safety_warning,
        portrait_quality=state.get("portrait_quality"),
        requires_quality_confirmation=False,
        tension_cards=new_tension_cards,
        focus_card_id=new_focus_card_id,
    )


@router.post("/{session_id}/elicitation/stream")
async def stream_elicitation(session_id: UUID, body: ElicitationBody):
    repo = _get_repo()
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.status != "eliciting":
        raise HTTPException(status_code=409, detail={"expected_status": "eliciting", "current_status": row.status})
    user_display_name = _session_display_name(session_id)

    use_lower = bool(row.crisis_returned_at) and (row.rounds_since_crisis_return or 0) < 5
    safety_result = _safety_monitor.check_input(body.message, context=[], use_lower_threshold=use_lower)
    if safety_result.level == SafetyLevel.CRITICAL:
        raise HTTPException(
            status_code=449,
            detail={
                "safety": "crisis",
                "resources": CRISIS_RESOURCES,
                "require_confirm_to_continue": True,
            },
        )
    safety_warning = "如需支持可查看页面底部资源链接。" if safety_result.level == SafetyLevel.WARNING else None

    (
        history,
        extracted_info,
        round_count,
        depth_evaluations,
        closing_revert_count,
        current_layer,
        layer_round_count,
        is_containment,
        containment_round_count,
        tension_cards,
        focus_card_id,
        l1_own_count,
        tension_probed_seen,
        consecutive_intervene_count,
        focus_trace,
    ) = _load_elicitation_state(row.elicitation_history)

    async def event_generator():
        try:
            if is_process_intent(body.message):
                state = _build_elicitation_state(
                    history=history,
                    extracted_info=extracted_info,
                    round_count=round_count,
                    depth_evaluations=depth_evaluations,
                    closing_revert_count=closing_revert_count,
                    current_layer=current_layer,
                    layer_round_count=layer_round_count,
                    is_containment=is_containment,
                    containment_round_count=containment_round_count,
                    tension_cards=tension_cards,
                    focus_card_id=focus_card_id,
                    l1_own_count=l1_own_count,
                    tension_probed_seen=tension_probed_seen,
                    consecutive_intervene_count=consecutive_intervene_count,
                    focus_trace=focus_trace,
                )
                await repo.update_profile(session_id, elicitation_history=state)
                save_elicitation(session_id, state)
                response_text = build_process_intent_notice(body.message)
                yield _sse_event("turn_start", {
                    "round": round_count,
                    "current_layer": current_layer,
                    "focus_tension": None,
                })
                yield _sse_event("assistant_token", {"content": response_text})
                yield _sse_event("turn_end", {
                    "response": response_text,
                    "raw_response": response_text,
                    "correction_applied": False,
                    "should_continue": True,
                    "round": round_count,
                    "depth": _depth_payload(depth_evaluations).model_dump() if _depth_payload(depth_evaluations) else None,
                    "tension_cards": tension_cards,
                    "focus_card_id": focus_card_id,
                    "safety_warning": safety_warning,
                })
                return

            async for event in _elicitation_service.generate_response_stream(
                body.message,
                history,
                extracted_info,
                round_count,
                depth_evaluations,
                closing_revert_count,
                current_layer,
                layer_round_count,
                is_containment,
                containment_round_count,
                tension_cards=tension_cards,
                focus_card_id=focus_card_id,
                l1_own_count=l1_own_count,
                tension_probed_seen=tension_probed_seen,
                user_display_name=user_display_name,
                consecutive_intervene_count=consecutive_intervene_count,
                focus_trace=focus_trace,
            ):
                event_type = str(event.get("type") or "message")
                data = event.get("data") if isinstance(event.get("data"), dict) else {}
                if event_type == "turn_end":
                    data = {**data, "safety_warning": safety_warning}
                    data = await _persist_stream_turn_end(
                        repo=repo,
                        session_id=session_id,
                        row=row,
                        data=data,
                        closing_revert_count=closing_revert_count,
                    )
                yield _sse_event(event_type, data)
        except Exception as exc:
            yield _sse_event("error", {"message": str(exc)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.put("/{session_id}/elicitation/last-user-message", response_model=ElicitationResponse)
async def update_last_elicitation_message(session_id: UUID, body: ElicitationEditBody):
    repo = _get_repo()
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.status != "eliciting":
        raise HTTPException(status_code=409, detail={"expected_status": "eliciting", "current_status": row.status})
    user_display_name = _session_display_name(session_id)

    (
        history,
        extracted_info,
        round_count,
        depth_evaluations,
        closing_revert_count,
        current_layer,
        layer_round_count,
        is_containment,
        containment_round_count,
        tension_cards,
        focus_card_id,
        l1_own_count,
        tension_probed_seen,
        consecutive_intervene_count,
        focus_trace,
    ) = _load_elicitation_state(row.elicitation_history)

    latest_user_index = _latest_user_turn_index(history)
    if latest_user_index is None:
        raise HTTPException(status_code=400, detail="No user message to edit")

    base_history = history[:latest_user_index]
    prior_round_count = max(round_count - 1, 0)
    prior_depth_evaluations = depth_evaluations[:prior_round_count]
    rollback_tension_cards = _filter_tension_cards_before_round(tension_cards, prior_round_count)
    rollback_focus_card_id = focus_card_id if any(
        card.get("id") == focus_card_id for card in rollback_tension_cards
    ) else None
    rollback_focus_trace = [
        item
        for item in focus_trace
        if isinstance(item, dict) and _int_or_default(item.get("round"), 0) <= prior_round_count
    ]
    rollback_state = _rollback_edit_generation_state(
        prior_round_count=prior_round_count,
        prior_depth_evaluations=prior_depth_evaluations,
        current_layer=current_layer,
        layer_round_count=layer_round_count,
        is_containment=is_containment,
        containment_round_count=containment_round_count,
        tension_cards=rollback_tension_cards,
        l1_own_count=l1_own_count,
    )

    (
        response,
        should_continue,
        new_info,
        new_history,
        new_evaluations,
        outcome,
        next_state,
        new_tension_cards,
        new_focus_card_id,
    ) = _unpack_elicitation_result(
        await _elicitation_service.generate_response(
            body.message,
            base_history,
            extracted_info,
            prior_round_count,
            prior_depth_evaluations,
            closing_revert_count,
            rollback_state["current_layer"],
            rollback_state["layer_round_count"],
            rollback_state["is_containment"],
            rollback_state["containment_round_count"],
            tension_cards=rollback_tension_cards,
            focus_card_id=rollback_focus_card_id,
            l1_own_count=rollback_state["l1_own_count"],
            tension_probed_seen=rollback_state["tension_probed_seen"],
            user_display_name=user_display_name,
            consecutive_intervene_count=0,
            focus_trace=rollback_focus_trace,
        ),
        fallback_tension_cards=rollback_tension_cards,
        fallback_focus_card_id=rollback_focus_card_id,
    )

    new_round = prior_round_count + 1
    state = _build_elicitation_state(
        history=new_history,
        extracted_info=new_info,
        round_count=new_round,
        depth_evaluations=new_evaluations,
        closing_revert_count=closing_revert_count,
        current_layer=next_state["current_layer"],
        layer_round_count=next_state["layer_round_count"],
        is_containment=next_state["is_containment"],
        containment_round_count=next_state["containment_round_count"],
        tension_cards=new_tension_cards,
        focus_card_id=new_focus_card_id,
        l1_own_count=int(next_state.get("l1_own_count", rollback_state["l1_own_count"])),
        tension_probed_seen=bool(next_state.get("tension_probed_seen", rollback_state["tension_probed_seen"])),
        consecutive_intervene_count=int(next_state.get("consecutive_intervene_count", 0)),
        focus_trace=next_state.get("focus_trace")
        if isinstance(next_state.get("focus_trace"), list)
        else rollback_focus_trace,
    )
    state.pop("pending_finish_outcome", None)
    state.pop("pending_conflict_profile_draft", None)
    state.pop("pending_finish_quality", None)

    await repo.update_profile(session_id, elicitation_history=state)
    save_elicitation(session_id, state)

    conflict_profile_draft = None
    outcome_dict = None
    if not should_continue and outcome is not None:
        conflict_profile_draft = ConflictProfileGenerator().generate_from_outcome(
            outcome,
            tension_cards=new_tension_cards,
        )
        quality = _portrait_quality_gate.evaluate(outcome, conflict_profile_draft)
        persist_quality = quality if _quality_status(quality) == "warn" else None
        outcome_dict, conflict_profile_draft = await _persist_finished_elicitation(
            repo=repo,
            session_id=session_id,
            state=state,
            outcome=outcome,
            conflict_profile_draft=conflict_profile_draft,
            quality=persist_quality,
        )

    return ElicitationResponse(
        response=response,
        should_continue=should_continue,
        round=new_round,
        depth=_depth_payload(new_evaluations),
        conflict_profile_draft=conflict_profile_draft,
        elicitation_outcome=outcome_dict,
        safety_warning=None,
        portrait_quality=state.get("portrait_quality"),
        requires_quality_confirmation=False,
        tension_cards=new_tension_cards,
        focus_card_id=new_focus_card_id,
    )


@router.post("/{session_id}/elicitation/finish", response_model=ElicitationResponse)
async def finish_elicitation(session_id: UUID, body: ElicitationFinishBody | None = None):
    repo = _get_repo()
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.status != "eliciting":
        raise HTTPException(status_code=409, detail={"expected_status": "eliciting", "current_status": row.status})

    finish_body = body or ElicitationFinishBody()
    (
        history,
        extracted_info,
        round_count,
        depth_evaluations,
        closing_revert_count,
        current_layer,
        layer_round_count,
        is_containment,
        containment_round_count,
        tension_cards,
        focus_card_id,
        l1_own_count,
        tension_probed_seen,
        consecutive_intervene_count,
        focus_trace,
    ) = _load_elicitation_state(row.elicitation_history)
    state = _build_elicitation_state(
        history=history,
        extracted_info=extracted_info,
        round_count=round_count,
        depth_evaluations=depth_evaluations,
        closing_revert_count=closing_revert_count,
        current_layer=current_layer,
        layer_round_count=layer_round_count,
        is_containment=is_containment,
        containment_round_count=containment_round_count,
        tension_cards=tension_cards,
        focus_card_id=focus_card_id,
        l1_own_count=l1_own_count,
        tension_probed_seen=tension_probed_seen,
        consecutive_intervene_count=consecutive_intervene_count,
        focus_trace=focus_trace,
    )

    pending_finish = None
    if finish_body.force and isinstance(row.elicitation_history, dict):
        pending_outcome = row.elicitation_history.get("pending_finish_outcome")
        pending_profile = row.elicitation_history.get("pending_conflict_profile_draft")
        pending_quality = row.elicitation_history.get("pending_finish_quality")
        if (
            isinstance(pending_outcome, dict)
            and isinstance(pending_profile, dict)
            and isinstance(pending_quality, dict)
            and pending_quality.get("status") == "warn"
        ):
            pending_finish = (
                ElicitationOutcome.from_dict(pending_outcome),
                pending_profile,
                pending_quality,
            )

    if pending_finish is not None:
        outcome, conflict_profile_draft, quality = pending_finish
    else:
        outcome = await _extract_outcome(
            _outcome_extractor,
            filter_process_turns(history),
            depth_evaluations,
            tension_cards,
        )
        conflict_profile_draft = ConflictProfileGenerator().generate_from_outcome(
            outcome,
            tension_cards=tension_cards,
        )
        quality = _portrait_quality_gate.evaluate(outcome, conflict_profile_draft)

    quality_payload = _quality_payload(quality)

    if _quality_status(quality) == "warn" and not finish_body.force:
        state["portrait_quality"] = quality_payload
        state["pending_finish_outcome"] = outcome.to_dict()
        state["pending_conflict_profile_draft"] = conflict_profile_draft
        state["pending_finish_quality"] = quality_payload
        await repo.update_profile(session_id, elicitation_history=state)
        save_elicitation(session_id, state)
        issues = quality_payload.get("issues") if isinstance(quality_payload, dict) else []
        missing = (
            "; ".join(str(issue.get("suggestion")) for issue in issues if isinstance(issue, dict) and issue.get("suggestion"))
            or "Add more concrete portrait evidence before continuing."
        )
        return ElicitationResponse(
            response=f"The portrait needs more evidence before continuing: {missing}",
            should_continue=True,
            round=round_count,
            depth=_depth_payload(depth_evaluations),
            conflict_profile_draft=None,
            elicitation_outcome=None,
            portrait_quality=quality_payload,
            requires_quality_confirmation=True,
            tension_cards=tension_cards,
            focus_card_id=focus_card_id,
        )

    outcome_dict, conflict_profile_draft = await _persist_finished_elicitation(
        repo=repo,
        session_id=session_id,
        state=state,
        outcome=outcome,
        conflict_profile_draft=conflict_profile_draft,
        quality=quality,
        quality_forced=True if _quality_status(quality) == "warn" and finish_body.force else None,
    )
    return ElicitationResponse(
        response="Elicitation finished. The portrait draft is ready for review.",
        should_continue=False,
        round=round_count,
        depth=_depth_payload(depth_evaluations),
        conflict_profile_draft=conflict_profile_draft,
        elicitation_outcome=outcome_dict,
        portrait_quality=quality_payload,
        requires_quality_confirmation=False,
        tension_cards=tension_cards,
        focus_card_id=focus_card_id,
    )


@router.post("/{session_id}/elicitation/resume")
async def resume_elicitation(session_id: UUID):
    repo = _get_repo()
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.status != "profile_pending":
        raise HTTPException(status_code=409, detail={"expected_status": "profile_pending", "current_status": row.status})

    await repo.update_status(session_id, "eliciting")
    await repo.update_profile(session_id, conflict_profile_snapshot=None)

    (
        history,
        extracted_info,
        round_count,
        depth_evaluations,
        closing_revert_count,
        current_layer,
        layer_round_count,
        is_containment,
        containment_round_count,
        tension_cards,
        focus_card_id,
        l1_own_count,
        tension_probed_seen,
        consecutive_intervene_count,
        focus_trace,
    ) = _load_elicitation_state(row.elicitation_history)
    updated_state = _build_elicitation_state(
        history=history,
        extracted_info=extracted_info,
        round_count=round_count,
        depth_evaluations=depth_evaluations,
        closing_revert_count=closing_revert_count + 1,
        current_layer=current_layer,
        layer_round_count=layer_round_count,
        is_containment=is_containment,
        containment_round_count=containment_round_count,
        tension_cards=tension_cards,
        focus_card_id=focus_card_id,
        l1_own_count=l1_own_count,
        tension_probed_seen=tension_probed_seen,
        consecutive_intervene_count=consecutive_intervene_count,
        focus_trace=focus_trace,
    )
    await repo.update_profile(session_id, elicitation_history=updated_state)
    save_elicitation(session_id, updated_state)
    save_conflict_profile(session_id, {})
    save_session_meta(session_id, {"status": "eliciting"})
    return {"ok": True, "status": "eliciting"}


@router.put("/{session_id}/profile")
async def put_profile(session_id: UUID, profile: dict[str, Any] = Body(...)):
    repo = _get_repo()
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.status != "profile_pending":
        raise HTTPException(status_code=409, detail={"expected_status": "profile_pending", "current_status": row.status})
    await repo.update_profile(session_id, conflict_profile_snapshot=profile)
    save_conflict_profile(session_id, profile)
    return {"ok": True}


@router.post("/{session_id}/profile/confirm")
async def profile_confirm(session_id: UUID, body: ProfileConfirmBody | None = None):
    repo = _get_repo()
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.status != "profile_pending":
        raise HTTPException(status_code=409, detail={"expected_status": "profile_pending", "current_status": row.status})
    await repo.update_status(session_id, "portrait_pending")
    save_session_meta(session_id, {"status": "portrait_pending"})
    return {"ok": True, "status": "portrait_pending"}
