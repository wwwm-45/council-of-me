"""Phase 8: Closure - emotion check, summary, close."""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException

from app.api.routes.sessions import _session_repo
from app.services.archive import ArchiveController
from app.services.closure import assess_emotional_risk, generate_closing_message
from app.services.debate_engine import cleanup_session, generate_synthesis_for_session, get_debate_state
from app.services.file_store import (
    load_closure_emotion,
    load_reflection_trace,
    save_closure_emotion,
    save_closure_summary,
    save_session_meta,
)
from app.services.intervention import get_annotations, get_interventions
from app.services.reflection import get_reflections
from app.services.reflection_trace import extract_closure_seed
from app.services.safety import CRISIS_RESOURCES

router = APIRouter(prefix="/sessions", tags=["closure"])

# Interim storage for emotion assessment (per-session), read by closure/summary.
_emotion_results: dict[str, dict] = {}


@router.post("/{session_id}/closure/emotion")
async def closure_emotion(session_id: UUID, body: dict = Body(...)):
    """Submit emotion check. body: emotions (list), intensity (1-10). Returns risk level."""
    row = await _session_repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.status not in ("closing", "reflecting", "synthesizing"):
        raise HTTPException(
            status_code=409,
            detail={"expected_status": "closing|reflecting|synthesizing", "current_status": row.status},
        )

    # Reflection phase removed: entering the closure flow transitions the
    # session straight from synthesizing (or legacy reflecting) to closing.
    if row.status != "closing":
        await _session_repo.update_status(session_id, "closing")
        save_session_meta(session_id, {"status": "closing"})

    emotions = body.get("emotions") or []
    intensity = int(body.get("intensity") or 5)
    risk = assess_emotional_risk(emotions, intensity)

    payload = {"risk_level": risk, "emotions": emotions, "intensity": intensity}
    _emotion_results[str(session_id)] = payload
    save_closure_emotion(session_id, payload)
    return payload


@router.get("/{session_id}/closure/summary")
async def closure_summary(session_id: UUID):
    """Get synthesis narrative + closing message based on submitted emotion risk."""
    row = await _session_repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    dilemma = (row.conflict_profile_snapshot or {}).get("core_dilemma") or "inner conflict"

    emotion_data = _emotion_results.get(str(session_id), {})
    if not emotion_data:
        emotion_data = load_closure_emotion(session_id)
        if emotion_data:
            _emotion_results[str(session_id)] = emotion_data
    risk = emotion_data.get("risk_level", "LOW")

    reflection_trace = load_reflection_trace(session_id)
    closure_seed = extract_closure_seed(reflection_trace)

    synthesis = generate_synthesis_for_session(str(session_id))
    narrative = synthesis.get("narrative", f"You just explored your inner voices around '{dilemma}'.")

    closing = generate_closing_message(risk, dilemma, closure_seed=closure_seed)
    return {
        "narrative": narrative,
        "closing_message": closing,
        "risk_level": risk,
        "synthesis_type": synthesis.get("synthesis_type", "NONE"),
        "resources": CRISIS_RESOURCES if risk != "LOW" else None,
        "reflection_trace": reflection_trace or {},
    }


@router.post("/{session_id}/close")
async def close_session(session_id: UUID):
    """Set status=closed. Triggers Phase 7 archive when DB is configured.

    The synthesis landscape now ends the journey directly (no emotion check or
    review step), so a session may still be `synthesizing` when it reaches here;
    `reflecting`/`closing` remain valid from the deep-reflection path.
    """
    row = await _session_repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.status not in ("closing", "reflecting", "synthesizing"):
        raise HTTPException(
            status_code=409,
            detail={"expected_status": "closing|reflecting|synthesizing", "current_status": row.status},
        )

    archive = ArchiveController(
        _session_repo,
        get_debate_state_fn=get_debate_state,
        get_reflections_fn=get_reflections,
        get_interventions_fn=get_interventions,
        get_annotations_fn=get_annotations,
        get_synthesis_fn=generate_synthesis_for_session,
    )
    result = await archive.archive_session(session_id)
    if not result.success:
        raise HTTPException(status_code=500, detail={"archive_error": result.error or "archive_failed"})

    now = datetime.now(timezone.utc)
    await _session_repo.update_status(session_id, "closed")
    await _session_repo.update_profile(session_id, completed_at=now)

    save_session_meta(session_id, {"status": "closed", "completed_at": str(now)})
    save_closure_summary(
        session_id,
        {
            "core_dilemma": (row.conflict_profile_snapshot or {}).get("core_dilemma", ""),
            "completed_at": str(now),
            "debate_level": row.debate_level,
            "total_rounds": row.total_rounds,
            "agent_count": row.agent_count,
        },
    )

    cleanup_session(str(session_id))
    _emotion_results.pop(str(session_id), None)

    return {
        "ok": True,
        "status": "closed",
        "archive": result.message if result.success else result.error,
    }
