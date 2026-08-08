"""
Phase 0 & session lifecycle: create, consent, framing, safety/confirm.
Status transitions: created -> (consent, framing) -> eliciting.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4
from fastapi import APIRouter, HTTPException, status

from app import config
from app.api.schemas.sessions import (
    CreateSessionBody,
    CreateSessionResponse,
    ConsentBody,
    FramingBody,
)
from app.repositories.session_repo import SessionRepository
from app.services.debate.round_state import COMPLEXITY_ROUNDS
from app.services.safety import SafetyMonitor
from app.services.file_store import (
    load_session_meta,
    save_conflict_profile,
    save_elicitation,
    save_session_meta,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])

# In-memory repo for standalone run; replace with pool when DB is configured
_session_repo = SessionRepository()
_safety_monitor = SafetyMonitor()


def _get_repo() -> SessionRepository:
    return _session_repo


_DEBUG_SESSION_SOURCE_ID = "8dd612dd-5877-4bb1-a0a3-533278c5dd9e"

# /debug-skip fabricates a profile when no fixture supplies one. Round count must
# come from the authoritative table (app.services.debate.round_state), never a literal.
_DEBUG_SKIP_LEVEL = "L3"
_DEBUG_SKIP_AGENT_COUNT = 5
_DEBUG_SKIP_MAX_ROUNDS = COMPLEXITY_ROUNDS[_DEBUG_SKIP_LEVEL]


def _debug_session_source() -> Path:
    return Path(config.SESSION_EXPORT_DIR).expanduser() / _DEBUG_SESSION_SOURCE_ID


def _has_debug_session_fixture() -> bool:
    source = _debug_session_source()
    return any(
        (source / filename).exists()
        for filename in ("session_meta.json", "conflict_profile.json", "elicitation.json")
    )


def _read_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _default_elicitation_path() -> Path | None:
    explicit = os.getenv("COUNCIL_DEFAULT_ELICITATION_PATH")
    if explicit:
        path = Path(explicit).expanduser()
        if path.exists():
            return path

    workspace_root = Path(config.WORKSPACE_ROOT).expanduser()
    project_root = Path(config.PROJECT_ROOT).expanduser()
    for path in (
        workspace_root.parent / "elicitation.json",
        workspace_root / "elicitation.json",
        project_root / "elicitation.json",
    ):
        if path.exists():
            return path
    return None


def _conflict_profile_from_elicitation(elicitation: dict) -> dict:
    extracted = elicitation.get("extracted_info")
    if not isinstance(extracted, dict):
        extracted = {}

    profile = dict(extracted)
    profile.setdefault("debate_level", _DEBUG_SKIP_LEVEL)
    profile.setdefault("agent_count", _DEBUG_SKIP_AGENT_COUNT)
    profile.setdefault("max_rounds", _DEBUG_SKIP_MAX_ROUNDS)
    return profile


def _clean_display_name(value: str | None) -> str:
    return (value or "").strip()[:40]


@router.post("/debug-skip")
async def debug_skip():
    """调试专用：加载固定 session 数据，直接跳到 identity_pending 阶段。"""
    def _read(filename: str) -> dict:
        return _read_json_file(_debug_session_source() / filename)

    session_meta = _read("session_meta.json")
    conflict_profile = _read("conflict_profile.json")
    elicitation = _read("elicitation.json")
    default_elicitation_path = _default_elicitation_path()
    if default_elicitation_path is not None and not _has_debug_session_fixture():
        elicitation = _read_json_file(default_elicitation_path)
        conflict_profile = _conflict_profile_from_elicitation(elicitation)

    framing = session_meta.get("framing_preference", "inner_parts")
    debate_level = conflict_profile.get("debate_level", _DEBUG_SKIP_LEVEL)
    agent_count = int(conflict_profile.get("agent_count", _DEBUG_SKIP_AGENT_COUNT))
    max_rounds = int(conflict_profile.get("max_rounds", _DEBUG_SKIP_MAX_ROUNDS))
    core_dilemma = conflict_profile.get("core_dilemma", "")

    repo = _get_repo()
    session_id = await repo.create()
    await repo.update_profile(
        session_id,
        framing_preference=framing,
        conflict_profile_snapshot=conflict_profile,
        elicitation_history=elicitation.get("conversation_history", []),
        core_dilemma=core_dilemma,
        debate_level=debate_level,
        agent_count=agent_count,
        max_rounds=max_rounds,
    )
    await repo.update_status(session_id, "identity_pending")
    save_session_meta(session_id, {
        "session_id": str(session_id),
        "status": "identity_pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "framing_preference": framing,
        "debug_skip_source": (
            str(default_elicitation_path)
            if default_elicitation_path is not None
            else str(_debug_session_source())
        ),
    })
    save_elicitation(session_id, elicitation)
    save_conflict_profile(session_id, conflict_profile)

    return {
        "session_id": str(session_id),
        "status": "identity_pending",
        "framing_preference": framing,
        "conflict_profile": conflict_profile,
        "debate_level": debate_level,
    }


@router.post("", response_model=CreateSessionResponse)
async def create_session(body: CreateSessionBody | None = None):
    """Create a new session. Returns session_id and status=created."""
    body = body or CreateSessionBody()
    user_id = body.user_id or uuid4()
    display_name = _clean_display_name(body.display_name)
    session_id = await _get_repo().create(user_id=user_id)
    # Create session folder on disk immediately
    save_session_meta(session_id, {
        "session_id": str(session_id),
        "user_id": str(user_id),
        "status": "created",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "display_name": display_name,
    })
    return CreateSessionResponse(session_id=session_id, status="created")


@router.get("/{session_id}")
async def get_session(session_id: UUID):
    """Get session by id. 404 if not found."""
    repo = _get_repo()
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    session_meta = load_session_meta(session_id)
    return {
        "session_id": str(row.session_id),
        "user_id": str(row.user_id),
        "display_name": _clean_display_name(session_meta.get("display_name")),
        "status": row.status,
        "framing_preference": row.framing_preference,
        "created_at": row.created_at.isoformat(),
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "debate_level": row.debate_level,
        "agent_count": row.agent_count,
        "max_rounds": row.max_rounds,
        "conflict_profile_snapshot": row.conflict_profile_snapshot,
        "elicitation_history": row.elicitation_history,
    }


@router.post("/{session_id}/consent")
async def consent(session_id: UUID, body: ConsentBody):
    """Record that user accepted the honesty disclaimer. Expects status=created."""
    repo = _get_repo()
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.status != "created":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"expected_status": "created", "current_status": row.status},
        )
    if body.accepted:
        pass  # Could store consent timestamp in users table
    save_session_meta(session_id, {
        "consent_accepted": bool(body.accepted),
        "consent_at": datetime.now(timezone.utc).isoformat() if body.accepted else None,
    })
    return {"ok": True}


@router.post("/{session_id}/framing")
async def framing(session_id: UUID, body: FramingBody):
    """Set framing preference and transition to eliciting. Expects status=created."""
    repo = _get_repo()
    row = await repo.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.status != "created":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"expected_status": "created", "current_status": row.status},
        )
    allowed = {"inner_parts", "perspective", "advisory", "neutral"}
    if body.framing not in allowed:
        raise HTTPException(status_code=400, detail=f"framing must be one of {allowed}")
    await repo.update_profile(session_id, framing_preference=body.framing)
    await repo.update_status(session_id, "eliciting")
    save_session_meta(session_id, {"framing_preference": body.framing, "status": "eliciting"})
    return {"ok": True, "status": "eliciting"}
