"""Phase 6: Reflection routes (immersive state-machine + legacy compatibility)."""
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, HTTPException

from app.api.routes.sessions import _session_repo
from app.services.debate_engine import get_debate_state, get_enhanced_synthesis
from app.services.file_store import (
    append_reflection as file_append_reflection,
    save_session_meta,
)
from app.services.reflection import (
    PATH_IDS,
    append_reflection,
    append_reflection_legacy,
    build_reflection_node_catalog,
    ensure_reflection_state,
    ensure_reflection_state_persisted,
    generate_reflection_prompts,
    mark_node_feelings,
    mark_node_viewed,
    serialize_reflection_state,
    set_node_insight,
    set_reflection_phase,
    upsert_reflection_summary_bridge,
)
from app.services.reflection_dialogue import respond_dialogue, start_dialogue
from app.services.reflection_trace import (
    build_and_persist_reflection_trace,
    build_legacy_reflection_summary,
)

router = APIRouter(prefix="/sessions", tags=["reflection"])


async def _validate_session(session_id: UUID, allowed_statuses: tuple[str, ...] | None = None):
    repo = _session_repo
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if allowed_statuses and row.status not in allowed_statuses:
        raise HTTPException(
            status_code=409,
            detail={"expected_status": "|".join(allowed_statuses), "current_status": row.status},
        )
    return row


def _required_text(body: dict, field: str) -> str:
    value = str(body.get(field, "")).strip()
    if not value:
        raise HTTPException(status_code=400, detail=f"{field} is required")
    return value


async def _ensure_reflecting(session_id: UUID, current_status: str) -> None:
    allowed_statuses = ("synthesizing", "reflecting", "closing")
    if current_status not in allowed_statuses:
        raise HTTPException(
            status_code=409,
            detail={"expected_status": "synthesizing|reflecting|closing", "current_status": current_status},
        )
    if current_status in ("reflecting", "closing"):
        return
    if current_status == "synthesizing":
        await _session_repo.update_status(session_id, "reflecting")
        save_session_meta(session_id, {"status": "reflecting"})


async def _resolve_reflection_synthesis(session_id: UUID, profile: dict) -> dict:
    enhanced = await get_enhanced_synthesis(str(session_id))
    if enhanced:
        return enhanced

    from app.services.synthesis import generate_synthesis

    state = get_debate_state(str(session_id))
    return generate_synthesis(state.get("statements") or [], profile)


@router.post("/{session_id}/reflection/start")
async def reflection_start(session_id: UUID):
    row = await _validate_session(session_id)
    await _ensure_reflecting(session_id, row.status)
    state = ensure_reflection_state_persisted(str(session_id))
    synthesis = await _resolve_reflection_synthesis(session_id, row.conflict_profile_snapshot or {})
    return {
        "state": serialize_reflection_state(state),
        "node_catalog": build_reflection_node_catalog(synthesis),
    }


@router.post("/{session_id}/reflection/feeling")
async def reflection_feeling(session_id: UUID, body: dict = Body(...)):
    row = await _validate_session(session_id)
    await _ensure_reflecting(session_id, row.status)

    node_id = _required_text(body, "node_id")
    node_type = str(body.get("node_type", "voice")).strip() or "voice"
    node_label = str(body.get("node_label", node_id)).strip() or node_id
    feelings_raw = body.get("feelings") or []
    if not isinstance(feelings_raw, list):
        raise HTTPException(status_code=400, detail="feelings must be a list")

    feelings = [str(item).strip() for item in feelings_raw if isinstance(item, str)]
    state = mark_node_feelings(
        str(session_id),
        node_id=node_id,
        node_type=node_type,
        node_label=node_label,
        feelings=feelings,
    )
    return {"state": serialize_reflection_state(state)}


@router.post("/{session_id}/reflection/viewed")
async def reflection_viewed(session_id: UUID, body: dict = Body(...)):
    row = await _validate_session(session_id)
    await _ensure_reflecting(session_id, row.status)

    node_id = _required_text(body, "node_id")
    node_type = str(body.get("node_type", "voice")).strip() or "voice"
    node_label = str(body.get("node_label", node_id)).strip() or node_id

    state = mark_node_viewed(
        str(session_id),
        node_id=node_id,
        node_type=node_type,
        node_label=node_label,
    )
    return {"state": serialize_reflection_state(state)}


@router.post("/{session_id}/reflection/dialogue/start")
async def reflection_dialogue_start(session_id: UUID, body: dict = Body(...)):
    row = await _validate_session(session_id)
    node_id = _required_text(body, "node_id")
    node_type = str(body.get("node_type", "voice")).strip() or "voice"
    node_label = str(body.get("node_label", node_id)).strip() or node_id
    feelings_raw = body.get("feelings")
    path = str(body.get("path", "")).strip()
    if path and path not in PATH_IDS:
        raise HTTPException(status_code=400, detail=f"path must be one of {PATH_IDS}")

    if feelings_raw is not None:
        if not isinstance(feelings_raw, list):
            raise HTTPException(status_code=400, detail="feelings must be a list when provided")
        feelings = [str(item).strip() for item in feelings_raw if isinstance(item, str)]

    await _ensure_reflecting(session_id, row.status)

    if feelings_raw is not None:
        state = mark_node_feelings(
            str(session_id),
            node_id=node_id,
            node_type=node_type,
            node_label=node_label,
            feelings=feelings,
        )
    else:
        state = mark_node_viewed(
            str(session_id),
            node_id=node_id,
            node_type=node_type,
            node_label=node_label,
        )

    _state, payload = await start_dialogue(state, node_id=node_id, path=path)
    return payload


@router.post("/{session_id}/reflection/dialogue/respond")
async def reflection_dialogue_respond(session_id: UUID, body: dict = Body(...)):
    row = await _validate_session(session_id)
    await _ensure_reflecting(session_id, row.status)

    exploration_id = _required_text(body, "exploration_id")
    content = _required_text(body, "content")
    client_turn_id = str(body.get("client_turn_id", "")).strip() or f"client-{uuid4().hex[:12]}"

    state = ensure_reflection_state(str(session_id))
    try:
        _state, payload = await respond_dialogue(
            state,
            exploration_id=exploration_id,
            content=content,
            client_turn_id=client_turn_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return payload


@router.post("/{session_id}/reflection/insight")
async def reflection_insight(session_id: UUID, body: dict = Body(...)):
    row = await _validate_session(session_id)
    await _ensure_reflecting(session_id, row.status)

    node_id = _required_text(body, "node_id")
    insight = _required_text(body, "insight")
    node_type = str(body.get("node_type", "")).strip()
    node_label = str(body.get("node_label", "")).strip()

    state = set_node_insight(
        str(session_id),
        node_id=node_id,
        insight=insight,
        node_type=node_type,
        node_label=node_label,
    )
    return {"state": serialize_reflection_state(state)}


@router.get("/{session_id}/reflection/trace")
async def reflection_trace(session_id: UUID):
    _row = await _validate_session(session_id, ("reflecting", "closing"))
    state = set_reflection_phase(str(session_id), "trace")
    trace = build_and_persist_reflection_trace(state)
    return trace


@router.get("/{session_id}/reflection/prompts")
async def get_reflection_prompts(session_id: UUID):
    row = await _validate_session(
        session_id,
        ("debating", "synthesizing", "reflecting", "closing"),
    )
    profile = row.conflict_profile_snapshot or {}
    identity_cards = row.identity_cards_snapshot or []
    synthesis = await _resolve_reflection_synthesis(session_id, profile)
    state = get_debate_state(str(session_id))
    statements = state.get("statements") or []

    prompts = generate_reflection_prompts(
        synthesis,
        profile,
        identity_cards=identity_cards,
        statements=statements,
    )
    return prompts


@router.post("/{session_id}/reflection")
async def post_reflection(session_id: UUID, body: dict = Body(...)):
    row = await _validate_session(session_id)
    await _ensure_reflecting(session_id, row.status)

    level = body.get("level", "R1")
    if "responses" in body:
        responses = body["responses"]
        append_reflection(str(session_id), level, responses)
        file_append_reflection(session_id, {"level": level, "responses": responses})
    else:
        content = body.get("content", "")
        append_reflection_legacy(str(session_id), level, content)
        file_append_reflection(session_id, {"level": level, "content": content})

    return {"ok": True}


@router.post("/{session_id}/reflection/complete")
async def reflection_complete(session_id: UUID):
    row = await _validate_session(session_id, ("reflecting", "closing"))
    reflection_completed_at = datetime.now(timezone.utc)

    state = set_reflection_phase(str(session_id), "trace")
    trace = build_and_persist_reflection_trace(state)
    legacy_summary = build_legacy_reflection_summary(trace)
    upsert_reflection_summary_bridge(str(session_id), legacy_summary, level="R4")

    if row.status != "closing":
        await _session_repo.update_status(session_id, "closing")
    save_session_meta(
        session_id,
        {
            "status": "closing",
            "reflection_completed_at": reflection_completed_at.isoformat(),
            "reflection_summary": legacy_summary,
        },
    )
    return {"ok": True, "status": "closing", "trace": trace}
