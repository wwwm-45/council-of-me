"""Phase 2: Complexity assessment and confirm."""
from uuid import UUID
from fastapi import APIRouter, HTTPException, Body

from app.api.routes.sessions import _session_repo
from app.services.complexity import calculate_complexity_score, calculate_complexity_dimensions, assign_debate_level
from app.services.file_store import save_session_meta

router = APIRouter(prefix="/sessions", tags=["complexity"])


@router.get("/{session_id}/complexity")
async def get_complexity(session_id: UUID):
    """Return complexity score, dimensions, suggested level. Expects status=complexity_pending."""
    repo = _session_repo
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.status != "complexity_pending":
        raise HTTPException(status_code=409, detail={"expected_status": "complexity_pending", "current_status": row.status})
    profile = row.conflict_profile_snapshot or {}
    score = calculate_complexity_score(profile)
    dimensions = calculate_complexity_dimensions(profile)
    level, agent_count, max_rounds = assign_debate_level(score)
    return {
        "score": round(score, 1),
        "dimensions": dimensions,
        "profile_summary": {
            "core_dilemma": profile.get("core_dilemma", ""),
            "inner_voices": profile.get("inner_voices", []),
            "pain_points": profile.get("pain_points", []),
            "core_tensions": profile.get("core_tensions", []),
        },
        "suggested_level": level,
        "agent_count": agent_count,
        "max_rounds": max_rounds,
    }


@router.post("/{session_id}/complexity/confirm")
async def complexity_confirm(session_id: UUID, body: dict | None = Body(default=None)):
    """Confirm debate config; optionally override. body: debate_level?, agent_count?, max_rounds?."""
    repo = _session_repo
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.status != "complexity_pending":
        raise HTTPException(status_code=409, detail={"expected_status": "complexity_pending", "current_status": row.status})
    body = body or {}
    profile = row.conflict_profile_snapshot or {}
    score = calculate_complexity_score(profile)
    computed_level, computed_agents, computed_rounds = assign_debate_level(score)
    level = body.get("debate_level") or computed_level
    agent_count = body.get("agent_count") or computed_agents
    max_rounds = body.get("max_rounds") or computed_rounds
    await repo.update_profile(session_id, debate_level=level, agent_count=agent_count, max_rounds=max_rounds)
    await repo.update_status(session_id, "identity_pending")
    save_session_meta(session_id, {
        "status": "identity_pending",
        "debate_level": level,
        "agent_count": agent_count,
        "max_rounds": max_rounds,
    })
    return {"ok": True, "status": "identity_pending", "debate_level": level, "agent_count": agent_count, "max_rounds": max_rounds}
