"""Phase 4: Debate - next-round, debug-skip, SSE streaming."""
from uuid import UUID
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.api.routes.sessions import _session_repo
from app.services.debate_engine import (
    cleanup_session,
    run_next_round,
    get_or_create_orchestrator,
    get_artifacts,
    seed_debate_state,
)
from app.services.debate.stream_bridge import StreamBridge, SSEEvent
from app.services.debate.round_state import DebatePhase
from app.services.file_store import (
    append_debate_round,
    load_workspace_debate_statements,
    delete_session_export,
    save_debate_artifacts,
    save_debate_statements,
    save_session_meta,
)

router = APIRouter(prefix="/sessions", tags=["debate"])


@router.post("/{session_id}/debate/debug-skip")
async def debate_debug_skip(session_id: UUID):
    """
    Debug helper: load workspace fixture debate statements and jump to synthesis.
    """
    repo = _session_repo
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.status != "debating":
        raise HTTPException(
            status_code=409,
            detail={"expected_status": "debating", "current_status": row.status},
        )

    source_session_id = "workspace:debate_statements.json"
    statements = load_workspace_debate_statements()
    if not statements:
        raise HTTPException(status_code=404, detail="Workspace debate fixture not found or empty")

    cleanup_session(str(session_id))
    seed_debate_state(str(session_id), statements)

    save_debate_statements(session_id, statements)
    delete_session_export(session_id, "synthesis.json")
    delete_session_export(session_id, "debate_artifacts.json")

    await repo.update_status(session_id, "synthesizing")
    save_session_meta(session_id, {
        "status": "synthesizing",
        "debug_source_session_id": source_session_id,
    })

    return {
        "ok": True,
        "status": "synthesizing",
        "source_session_id": source_session_id,
        "statement_count": len(statements),
        "has_synthesis": False,
    }


@router.post("/{session_id}/debate/next-round")
async def debate_next_round(session_id: UUID):
    """Run next round (2–4). Expects status=debating and Round 1 already run."""
    repo = _session_repo
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.status != "debating":
        raise HTTPException(status_code=409, detail={"expected_status": "debating", "current_status": row.status})
    cards = row.identity_cards_snapshot or []
    profile = row.conflict_profile_snapshot or {}
    max_rounds = row.max_rounds or 4
    if not cards:
        raise HTTPException(status_code=400, detail="Identity cards missing.")
    result = await run_next_round(str(session_id), cards, profile, max_rounds=max_rounds)
    append_debate_round(session_id, result.get("statements", []))
    artifacts = get_artifacts(str(session_id))
    if artifacts:
        save_debate_artifacts(session_id, artifacts)
    return {
        "ok": True,
        "statements": result.get("statements", []),
        "current_round": result.get("current_round", 0),
        "done": result.get("done", False),
    }


async def _get_debating_row(session_id: UUID):
    """Validate session exists and is in debating status."""
    repo = _session_repo
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.status != "debating":
        raise HTTPException(
            status_code=409,
            detail={"expected_status": "debating", "current_status": row.status},
        )
    if not (row.identity_cards_snapshot or []):
        raise HTTPException(status_code=400, detail="Identity cards missing.")
    return row


@router.post("/{session_id}/debate/stream-round")
async def debate_stream_round(session_id: UUID):
    """Stream a single round via SSE (Server-Sent Events)."""
    row = await _get_debating_row(session_id)
    cards = row.identity_cards_snapshot or []
    profile = row.conflict_profile_snapshot or {}

    orchestrator = get_or_create_orchestrator(str(session_id), cards, profile)
    bridge = StreamBridge(orchestrator)
    r4_phases = (
        DebatePhase.R4_REFLECTION,
        DebatePhase.R4_MAPPING,
        DebatePhase.R4_FINAL,
    )

    async def event_generator():
        try:
            if orchestrator.current_phase == DebatePhase.ROUND1_OPENING:
                async for event in bridge.stream_round1():
                    yield event
            elif orchestrator.current_phase in r4_phases:
                async for event in bridge.stream_r4():
                    yield event
            elif not orchestrator.is_done:
                async for event in bridge.stream_round_n():
                    yield event
            else:
                yield SSEEvent("debate_complete", {
                    "total_rounds": orchestrator.current_round,
                }).encode()
                return
            # After streaming a round, notify frontend if debate is now complete
            if orchestrator.is_done:
                yield SSEEvent("debate_complete", {
                    "total_rounds": orchestrator.current_round,
                }).encode()
        except Exception as e:
            yield SSEEvent("error", {"message": str(e)}).encode()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

