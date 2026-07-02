"""Phase 5: Synthesis - GET after debate + SSE streaming endpoint."""
import json
from uuid import UUID
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.api.routes.sessions import _session_repo
from app.services.debate_engine import (
    generate_synthesis_for_session_async,
    get_orchestrator,
)
from app.services.file_store import save_debate_artifacts, save_synthesis

router = APIRouter(prefix="/sessions", tags=["synthesis"])


async def _get_valid_session(session_id: UUID):
    """Shared validation for synthesis endpoints."""
    repo = _session_repo
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.status not in ("debating", "synthesizing", "reflecting", "closing"):
        raise HTTPException(
            status_code=409,
            detail={
                "expected_status": "debating|synthesizing|reflecting|closing",
                "current_status": row.status,
            },
        )
    return row


@router.get("/{session_id}/synthesis")
async def get_synthesis(session_id: UUID):
    """Return LLM-enhanced synthesis from current debate. Falls back to heuristic."""
    await _get_valid_session(session_id)
    synthesis = await generate_synthesis_for_session_async(str(session_id))
    return synthesis


@router.post("/{session_id}/synthesis/stream")
async def stream_synthesis(session_id: UUID):
    """Stream synthesis generation progress via SSE.

    Events: synthesis_stage_start, synthesis_stage_end, synthesis_complete,
    synthesis_cached, error.
    """
    await _get_valid_session(session_id)
    orchestrator = get_orchestrator(str(session_id))
    if not orchestrator:
        raise HTTPException(status_code=404, detail="No active debate session")

    async def event_generator():
        try:
            async for event in orchestrator.generate_synthesis_streaming():
                evt_name = event.get("event", "unknown")
                evt_data = event.get("data", {})
                if evt_name in ("synthesis_complete", "synthesis_cached"):
                    save_synthesis(session_id, evt_data)
                    artifacts = orchestrator.get_artifacts()
                    if artifacts:
                        save_debate_artifacts(session_id, artifacts)
                yield f"event: {evt_name}\ndata: {json.dumps(evt_data, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'message': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
