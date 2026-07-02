"""Phase 3: Identity cards - GET, try, confirm."""
from uuid import UUID
from fastapi import APIRouter, HTTPException, Body

from app.api.routes.sessions import _session_repo
from app.services.identity_card import build_identity_cards, get_card_by_id
from app.services.llm import generate as llm_generate
from app.services.file_store import save_identity_cards, save_session_meta

router = APIRouter(prefix="/sessions", tags=["identity"])


@router.get("/{session_id}/identity")
async def get_identity(session_id: UUID):
    """Return identity cards for this session. Expects status=identity_pending."""
    repo = _session_repo
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.status != "identity_pending":
        raise HTTPException(status_code=409, detail={"expected_status": "identity_pending", "current_status": row.status})
    profile = dict(row.conflict_profile_snapshot or {})
    if row.debate_level:
        profile["debate_level"] = row.debate_level
    cards = build_identity_cards(profile, row.framing_preference)
    return {"identity_cards": cards}


@router.post("/{session_id}/identity/try")
async def identity_try(session_id: UUID, body: dict = Body(...)):
    """Generate one example statement for the given agent_id."""
    repo = _session_repo
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.status != "identity_pending":
        raise HTTPException(status_code=409, detail={"expected_status": "identity_pending", "current_status": row.status})
    profile = dict(row.conflict_profile_snapshot or {})
    if row.debate_level:
        profile["debate_level"] = row.debate_level
    cards = build_identity_cards(profile, row.framing_preference)
    agent_id = body.get("agent_id")
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id required")
    card = get_card_by_id(cards, agent_id)
    if not card:
        raise HTTPException(status_code=404, detail="Agent not found")
    dilemma = profile.get("core_dilemma") or "内心困境"
    prompt = f"请以{card.get('display_name', card['role'])}的身份，针对用户的困境「{dilemma}」，用一句话（50字内）表达你的立场。不要解释，只输出这一句话。"
    content = await llm_generate(prompt, system=card.get("system_prompt", ""))
    if not content or len(content) > 100:
        content = f"作为{card.get('display_name')}，我认为在「{dilemma}」中需要兼顾各方关切。"
    return {"agent_id": agent_id, "example_statement": content.strip()[:100]}


@router.post("/{session_id}/identity/confirm")
async def identity_confirm(session_id: UUID):
    """Confirm identity cards and move to debating."""
    repo = _session_repo
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.status != "identity_pending":
        raise HTTPException(status_code=409, detail={"expected_status": "identity_pending", "current_status": row.status})
    profile = dict(row.conflict_profile_snapshot or {})
    if row.debate_level:
        profile["debate_level"] = row.debate_level
    cards = build_identity_cards(profile, row.framing_preference)
    await repo.update_profile(session_id, identity_cards_snapshot=cards)
    await repo.update_status(session_id, "debating")
    save_identity_cards(session_id, cards)
    save_session_meta(session_id, {"status": "debating"})
    return {"ok": True, "status": "debating"}
