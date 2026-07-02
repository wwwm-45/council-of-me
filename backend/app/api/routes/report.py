"""Downloadable HTML report for a session (ending-screen download)."""
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.api.routes.sessions import _session_repo
from app.services.debate_engine import generate_synthesis_for_session_async
from app.services.file_store import load_report_html, load_synthesis, save_report_html
from app.services.report.builder import build_report_context
from app.services.report.summarizer import summarize
from app.services.report.template import render_report_html

router = APIRouter(prefix="/sessions", tags=["report"])


def _html_response(session_id: UUID, html: str) -> Response:
    short = str(session_id)[:8]
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="council-report-{short}.html"'},
    )


@router.get("/{session_id}/report")
async def get_report(session_id: UUID, refresh: bool = Query(False)):
    """Generate (or serve cached) a downloadable HTML report for the session."""
    row = await _session_repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    if not refresh:
        cached = load_report_html(session_id)
        if cached:
            return _html_response(session_id, cached)

    synthesis = load_synthesis(session_id)
    if not synthesis:
        synthesis = await generate_synthesis_for_session_async(str(session_id))

    ctx = build_report_context(synthesis=synthesis, session_row=row)
    summaries = await summarize(ctx)
    html = render_report_html(ctx, summaries)
    save_report_html(session_id, html)
    return _html_response(session_id, html)
