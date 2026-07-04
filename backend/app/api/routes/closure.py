"""Phase 8: Closure - close and archive the session."""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.api.routes.sessions import _session_repo
from app.services.archive import ArchiveController
from app.services.debate_engine import cleanup_session, generate_synthesis_for_session, get_debate_state
from app.services.file_store import (
    save_closure_summary,
    save_session_meta,
)
from app.services.intervention import get_annotations, get_interventions
from app.services.reflection import get_reflections

router = APIRouter(prefix="/sessions", tags=["closure"])


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

    return {
        "ok": True,
        "status": "closed",
        "archive": result.message if result.success else result.error,
    }
