"""
Session repository: create, get, update status and profile.
Sensitive fields (core_dilemma, etc.) can be encrypted before write; decrypt on read.
"""
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.models.session import DebateSessionRow


def _as_datetime(value: Any) -> Optional[datetime]:
    """Parse persisted timestamps and always return timezone-aware values."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _as_uuid(value: Any) -> Optional[UUID]:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return None
    return None


class SessionRepository:
    """Session data access with disk recovery when the DB is not configured."""

    def __init__(self, pool=None) -> None:
        self._pool = pool
        self._memory: dict[UUID, dict[str, Any]] = {}

    async def create(self, session_id: Optional[UUID] = None, user_id: Optional[UUID] = None) -> UUID:
        sid = session_id or uuid4()
        uid = user_id or uuid4()
        now = datetime.now(timezone.utc)
        row = {
            "session_id": sid,
            "user_id": uid,
            "created_at": now,
            "completed_at": None,
            "core_dilemma": "",
            "dilemma_type": None,
            "complexity_score": None,
            "debate_level": None,
            "max_rounds": None,
            "agent_count": None,
            "total_rounds": None,
            "total_duration_seconds": None,
            "user_interventions_count": 0,
            "status": "created",
            "framing_preference": None,
            "elicitation_history": None,
            "conflict_profile_snapshot": None,
            "identity_cards_snapshot": None,
            "crisis_returned_at": None,
            "rounds_since_crisis_return": 0,
        }
        if self._pool:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO debate_sessions (
                        session_id, user_id, created_at, core_dilemma, status,
                        crisis_returned_at, rounds_since_crisis_return
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    sid,
                    uid,
                    now,
                    "",
                    "created",
                    None,
                    0,
                )
        else:
            self._memory[sid] = row
        return sid

    async def get(self, session_id: UUID) -> Optional[DebateSessionRow]:
        if self._pool:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM debate_sessions WHERE session_id = $1",
                    session_id,
                )
                if not row:
                    return None
                return self._row_to_model(row)
        data = self._memory.get(session_id)
        if data is None:
            data = self._rehydrate_from_disk(session_id)
        if data is None:
            return None
        return DebateSessionRow(
            session_id=data["session_id"],
            user_id=data["user_id"],
            created_at=data["created_at"],
            completed_at=data["completed_at"],
            core_dilemma=data["core_dilemma"],
            dilemma_type=data["dilemma_type"],
            complexity_score=data["complexity_score"],
            debate_level=data["debate_level"],
            max_rounds=data["max_rounds"],
            agent_count=data["agent_count"],
            total_rounds=data["total_rounds"],
            total_duration_seconds=data["total_duration_seconds"],
            user_interventions_count=data["user_interventions_count"],
            status=data["status"],
            framing_preference=data["framing_preference"],
            elicitation_history=data["elicitation_history"],
            conflict_profile_snapshot=data["conflict_profile_snapshot"],
            identity_cards_snapshot=data["identity_cards_snapshot"],
            crisis_returned_at=data.get("crisis_returned_at"),
            rounds_since_crisis_return=data.get("rounds_since_crisis_return", 0),
        )

    def _rehydrate_from_disk(self, session_id: UUID) -> Optional[dict[str, Any]]:
        """Rebuild a standalone row from the session's incremental exports."""
        from app.services.file_store import (
            load_conflict_profile,
            load_elicitation,
            load_identity_cards,
            load_session_meta,
        )

        meta = load_session_meta(session_id)
        if not meta:
            return None

        elicitation = load_elicitation(session_id)
        conflict_profile = load_conflict_profile(session_id)
        identity_cards = load_identity_cards(session_id)
        created_at = _as_datetime(meta.get("created_at")) or datetime.fromtimestamp(0, timezone.utc)
        user_id = _as_uuid(meta.get("user_id")) or uuid5(
            NAMESPACE_URL,
            f"council-of-me:standalone-session:{session_id}",
        )

        def profile_value(key: str) -> Any:
            value = meta.get(key)
            return value if value is not None else conflict_profile.get(key)

        data = {
            "session_id": session_id,
            "user_id": user_id,
            "created_at": created_at,
            "completed_at": _as_datetime(meta.get("completed_at")),
            "core_dilemma": profile_value("core_dilemma") or "",
            "dilemma_type": profile_value("dilemma_type"),
            "complexity_score": profile_value("complexity_score"),
            "debate_level": profile_value("debate_level"),
            "max_rounds": profile_value("max_rounds"),
            "agent_count": profile_value("agent_count"),
            "total_rounds": meta.get("total_rounds"),
            "total_duration_seconds": meta.get("total_duration_seconds"),
            "user_interventions_count": meta.get("user_interventions_count") or 0,
            "status": meta.get("status") or "created",
            "framing_preference": meta.get("framing_preference"),
            "elicitation_history": elicitation or None,
            "conflict_profile_snapshot": conflict_profile or None,
            "identity_cards_snapshot": identity_cards or None,
            "crisis_returned_at": _as_datetime(meta.get("crisis_returned_at")),
            "rounds_since_crisis_return": meta.get("rounds_since_crisis_return") or 0,
        }
        self._memory[session_id] = data
        return data

    async def update_status(self, session_id: UUID, status: str) -> None:
        if self._pool:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "UPDATE debate_sessions SET status = $1 WHERE session_id = $2",
                    status,
                    session_id,
                )
        elif session_id in self._memory:
            self._memory[session_id]["status"] = status

    def get_pool(self):
        """Return DB pool if configured (for archive)."""
        return getattr(self, "_pool", None)

    async def update_profile(
        self,
        session_id: UUID,
        *,
        framing_preference: Optional[str] = None,
        elicitation_history: Optional[Any] = None,
        conflict_profile_snapshot: Optional[Any] = None,
        identity_cards_snapshot: Optional[Any] = None,
        core_dilemma: Optional[str] = None,
        debate_level: Optional[str] = None,
        agent_count: Optional[int] = None,
        max_rounds: Optional[int] = None,
        crisis_returned_at: Optional[datetime] = None,
        rounds_since_crisis_return: Optional[int] = None,
        completed_at: Optional[datetime] = None,
    ) -> None:
        if self._pool:
            # Build dynamic update
            sets, vals = [], []
            i = 1
            if framing_preference is not None:
                sets.append(f"framing_preference = ${i}")
                vals.append(framing_preference)
                i += 1
            if elicitation_history is not None:
                sets.append(f"elicitation_history = ${i}")
                vals.append(elicitation_history)
                i += 1
            if conflict_profile_snapshot is not None:
                sets.append(f"conflict_profile_snapshot = ${i}")
                vals.append(conflict_profile_snapshot)
                i += 1
            if identity_cards_snapshot is not None:
                sets.append(f"identity_cards_snapshot = ${i}")
                vals.append(identity_cards_snapshot)
                i += 1
            if core_dilemma is not None:
                sets.append(f"core_dilemma = ${i}")
                vals.append(core_dilemma)
                i += 1
            if debate_level is not None:
                sets.append(f"debate_level = ${i}")
                vals.append(debate_level)
                i += 1
            if agent_count is not None:
                sets.append(f"agent_count = ${i}")
                vals.append(agent_count)
                i += 1
            if max_rounds is not None:
                sets.append(f"max_rounds = ${i}")
                vals.append(max_rounds)
                i += 1
            if crisis_returned_at is not None:
                sets.append(f"crisis_returned_at = ${i}")
                vals.append(crisis_returned_at)
                i += 1
            if rounds_since_crisis_return is not None:
                sets.append(f"rounds_since_crisis_return = ${i}")
                vals.append(rounds_since_crisis_return)
                i += 1
            if completed_at is not None:
                sets.append(f"completed_at = ${i}")
                vals.append(completed_at)
                i += 1
            if not sets:
                return
            vals.append(session_id)
            async with self._pool.acquire() as conn:
                await conn.execute(
                    f"UPDATE debate_sessions SET {', '.join(sets)} WHERE session_id = ${i}",
                    *vals,
                )
        elif session_id in self._memory:
            d = self._memory[session_id]
            if framing_preference is not None:
                d["framing_preference"] = framing_preference
            if elicitation_history is not None:
                d["elicitation_history"] = elicitation_history
            if conflict_profile_snapshot is not None:
                d["conflict_profile_snapshot"] = conflict_profile_snapshot
            if identity_cards_snapshot is not None:
                d["identity_cards_snapshot"] = identity_cards_snapshot
            if core_dilemma is not None:
                d["core_dilemma"] = core_dilemma
            if debate_level is not None:
                d["debate_level"] = debate_level
            if agent_count is not None:
                d["agent_count"] = agent_count
            if max_rounds is not None:
                d["max_rounds"] = max_rounds
            if crisis_returned_at is not None:
                d["crisis_returned_at"] = crisis_returned_at
            if rounds_since_crisis_return is not None:
                d["rounds_since_crisis_return"] = rounds_since_crisis_return
            if completed_at is not None:
                d["completed_at"] = completed_at

    @staticmethod
    def _row_to_model(row: Any) -> DebateSessionRow:
        return DebateSessionRow(
            session_id=row["session_id"],
            user_id=row["user_id"],
            created_at=row["created_at"],
            completed_at=row.get("completed_at"),
            core_dilemma=row.get("core_dilemma") or "",
            dilemma_type=row.get("dilemma_type"),
            complexity_score=row.get("complexity_score"),
            debate_level=row.get("debate_level"),
            max_rounds=row.get("max_rounds"),
            agent_count=row.get("agent_count"),
            total_rounds=row.get("total_rounds"),
            total_duration_seconds=row.get("total_duration_seconds"),
            user_interventions_count=row.get("user_interventions_count") or 0,
            status=row["status"],
            framing_preference=row.get("framing_preference"),
            elicitation_history=row.get("elicitation_history"),
            conflict_profile_snapshot=row.get("conflict_profile_snapshot"),
            identity_cards_snapshot=row.get("identity_cards_snapshot"),
            crisis_returned_at=row.get("crisis_returned_at"),
            rounds_since_crisis_return=row.get("rounds_since_crisis_return") or 0,
        )
