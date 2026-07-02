"""Cross-session synthesis history endpoints."""
from uuid import UUID
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.history import SynthesisHistoryService

router = APIRouter(prefix="/users", tags=["history"])

# Pool will be injected at startup; default to None (in-memory mode returns empty)
_pool = None


def set_pool(pool):
    global _pool
    _pool = pool


def _service() -> SynthesisHistoryService:
    return SynthesisHistoryService(pool=_pool)


@router.get("/{user_id}/history/syntheses")
async def list_syntheses(
    user_id: UUID,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List past synthesis records for a user, newest first."""
    return await _service().list_user_syntheses(user_id, limit=limit, offset=offset)


@router.get("/{user_id}/history/syntheses/{synthesis_id}")
async def get_synthesis_detail(user_id: UUID, synthesis_id: UUID):
    """Get full synthesis detail."""
    result = await _service().get_synthesis_detail(synthesis_id)
    if not result:
        raise HTTPException(status_code=404, detail="Synthesis not found")
    return result


class CompareRequest(BaseModel):
    id_a: UUID
    id_b: UUID


@router.post("/{user_id}/history/compare")
async def compare_syntheses(user_id: UUID, body: CompareRequest):
    """Compare two synthesis records."""
    result = await _service().compare_syntheses(body.id_a, body.id_b)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/{user_id}/history/patterns")
async def detect_patterns(user_id: UUID):
    """Detect cross-session patterns for a user."""
    return await _service().detect_patterns(user_id)
