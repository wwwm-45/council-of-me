"""User debate interventions: pause, resume, user turns, adjust, resonance."""
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException

from app.api.routes.sessions import _session_repo
from app.services.debate_engine import (
    get_orchestrator,
    handle_adjust_intensity as engine_adjust,
    handle_pause as engine_pause,
    handle_resume as engine_resume,
    record_resonance as engine_resonance,
    submit_followup_response,
)
from app.services.file_store import append_intervention
from app.services.intervention import record_intervention
from app.services.safety import CRISIS_RESOURCES, SafetyLevel, SafetyMonitor

router = APIRouter(prefix="/sessions", tags=["interventions"])
_safety_monitor = SafetyMonitor()
_DEPRECATED_USER_TURN_DETAIL = (
    "Joining the debate as yourself has been removed; user turns are no longer accepted."
)


async def _get_row(session_id: UUID):
    repo = _session_repo
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.status != "debating":
        raise HTTPException(
            status_code=409,
            detail={"expected_status": "debating", "current_status": row.status},
        )
    return row


@router.post("/{session_id}/debate/pause")
async def debate_pause(session_id: UUID, body: dict = Body(default={})):
    """Pause debate. Optional body: round_number."""
    await _get_row(session_id)
    engine_pause(str(session_id))
    record_intervention(str(session_id), "pause", round_number=body.get("round_number"))
    append_intervention(session_id, {
        "intervention_type": "pause",
        "type": "pause",
        "round_number": body.get("round_number"),
    })
    return {"ok": True, "message": "paused"}


@router.post("/{session_id}/debate/resume")
async def debate_resume(session_id: UUID):
    """Resume debate."""
    await _get_row(session_id)
    engine_resume(str(session_id))
    record_intervention(str(session_id), "resume")
    append_intervention(session_id, {
        "intervention_type": "resume",
        "type": "resume",
    })
    return {"ok": True, "message": "resumed"}


@router.post("/{session_id}/debate/inject")
async def debate_inject(session_id: UUID, body: dict = Body(...)):
    """Removed. User debate turns are no longer supported."""
    raise HTTPException(status_code=410, detail=_DEPRECATED_USER_TURN_DETAIL)


@router.post("/{session_id}/debate/user-turn")
async def debate_user_turn(session_id: UUID, body: dict = Body(...)):
    """Removed. Formal user turns during the debate are no longer supported."""
    raise HTTPException(status_code=410, detail=_DEPRECATED_USER_TURN_DETAIL)


@router.post("/{session_id}/debate/adjust")
async def debate_adjust(session_id: UUID, body: dict = Body(...)):
    """Adjust agent intensity. body: agent_id, intensity (0-1)."""
    await _get_row(session_id)
    agent_id = body.get("agent_id") or ""
    intensity = body.get("intensity", 0.5)
    applied = engine_adjust(str(session_id), agent_id, intensity)
    record_intervention(
        str(session_id),
        "adjust",
        target_agent_id=agent_id,
        intensity=intensity,
        round_number=body.get("round_number"),
    )
    append_intervention(session_id, {
        "intervention_type": "adjust",
        "type": "adjust",
        "target_agent_id": agent_id,
        "intensity": intensity,
        "round_number": body.get("round_number"),
    })
    return {"ok": True, "applied": applied}


@router.post("/{session_id}/debate/annotate")
async def debate_annotate(session_id: UUID, body: dict = Body(...)):
    """Removed. User debate turns are no longer supported."""
    raise HTTPException(status_code=410, detail=_DEPRECATED_USER_TURN_DETAIL)


@router.post("/{session_id}/debate/resonance")
async def debate_resonance(session_id: UUID, body: dict = Body(...)):
    """Record which voice resonated. body: agent_id, reason."""
    await _get_row(session_id)
    agent_id = body.get("agent_id") or ""
    reason = body.get("reason") or ""
    applied = engine_resonance(str(session_id), agent_id, reason)
    record_intervention(
        str(session_id),
        "resonance",
        target_agent_id=agent_id,
        content=reason,
        round_number=body.get("round_number"),
    )
    append_intervention(session_id, {
        "intervention_type": "resonance",
        "type": "resonance",
        "target_agent_id": agent_id,
        "content": reason,
        "round_number": body.get("round_number"),
    })
    return {"ok": True, "applied": applied}


@router.post("/{session_id}/debate/early-termination-decision")
async def debate_early_termination_decision(session_id: UUID, body: dict = Body(...)):
    """Resolve a pending early-termination offer with continue or close."""
    await _get_row(session_id)
    decision = body.get("decision")
    if decision not in {"continue", "close"}:
        raise HTTPException(status_code=400, detail="Invalid early-termination decision")

    orchestrator = get_orchestrator(str(session_id))
    if orchestrator is None:
        raise HTTPException(status_code=404, detail="No live orchestrator for session")

    try:
        orchestrator.submit_early_termination_decision(decision)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    record_intervention(
        str(session_id),
        "early_termination_decision",
        content=decision,
        round_number=body.get("round_number"),
    )
    append_intervention(session_id, {
        "intervention_type": "early_termination_decision",
        "type": "early_termination_decision",
        "decision": decision,
        "round_number": body.get("round_number"),
    })
    return {"ok": True, "decision": decision}


@router.post("/{session_id}/debate/followup-response")
async def debate_followup_response(session_id: UUID, body: dict = Body(...)):
    """Resolve a pending follow-up gate with the user's answers (or an explicit skip)."""
    row = await _get_row(session_id)
    followup_id = body.get("followup_id")
    if not followup_id:
        raise HTTPException(status_code=400, detail="followup_id is required.")

    use_lower = bool(row.crisis_returned_at) and (row.rounds_since_crisis_return or 0) < 5
    cleaned: list[dict] = []
    for item in body.get("responses") or []:
        answer = (item.get("answer") or "").strip()
        if not answer:
            continue
        safety = _safety_monitor.check_input(answer, context=[], use_lower_threshold=use_lower)
        if safety.level == SafetyLevel.CRITICAL:
            raise HTTPException(
                status_code=449,
                detail={
                    "safety": "crisis",
                    "resources": CRISIS_RESOURCES,
                    "require_confirm_to_continue": True,
                },
            )
        cleaned.append({"question_id": item.get("question_id"), "answer": answer})

    try:
        result = submit_followup_response(str(session_id), followup_id, cleaned)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    append_intervention(session_id, {
        "intervention_type": "followup_response",
        "type": "followup_response",
        "followup_id": followup_id,
        "answered": len(cleaned),
    })
    return {
        "ok": True,
        "status": "recorded" if cleaned else "skipped",
        "accepted": result.get("accepted", len(cleaned)),
    }


@router.post("/{session_id}/debate/misalignment")
async def debate_misalignment(session_id: UUID, body: dict = Body(...)):
    """Mark a statement as misaligned. body: statement_id, agent_id, reason."""
    await _get_row(session_id)
    record_intervention(
        str(session_id),
        "misalignment",
        target_agent_id=body.get("agent_id"),
        content=body.get("reason") or "",
        round_number=body.get("round_number"),
    )
    append_intervention(session_id, {
        "intervention_type": "misalignment",
        "type": "misalignment",
        "statement_id": body.get("statement_id"),
        "agent_id": body.get("agent_id"),
        "reason": body.get("reason"),
        "round_number": body.get("round_number"),
    })
    return {"ok": True}
