"""
Phase 7: Archive controller.
On session close, writes inner_voices, value_conflicts, debate_statements,
reflection_records, synthesis results and schedules review_reminders when DB pool is configured.
Without pool (e.g. in-memory tests), archive is no-op.

Incremental file export is now handled by app.services.file_store.
"""
import json
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Optional
from uuid import UUID, uuid4

from app.models.session import DebateSessionRow


class ArchiveResult:
    """Result of archive_session."""
    def __init__(self, success: bool, session_id: Optional[UUID] = None, message: str = "", error: str = ""):
        self.success = success
        self.session_id = session_id
        self.message = message
        self.error = error


class ArchiveController:
    """Archive session data to DB when pool is available."""

    def __init__(
        self,
        repo: Any,
        get_debate_state_fn: Optional[Callable[[str], dict]] = None,
        get_reflections_fn: Optional[Callable[[str], list]] = None,
        get_interventions_fn: Optional[Callable[[str], list]] = None,
        get_annotations_fn: Optional[Callable[[str], list]] = None,
        get_synthesis_fn: Optional[Callable[[str], Optional[dict]]] = None,
    ):
        self._repo = repo
        self._get_debate_state = get_debate_state_fn or (lambda _: {"statements": []})
        self._get_reflections = get_reflections_fn or (lambda _: [])
        self._get_interventions = get_interventions_fn or (lambda _: [])
        self._get_annotations = get_annotations_fn or (lambda _: [])
        self._get_synthesis = get_synthesis_fn or (lambda _: None)

    @staticmethod
    def _serialize_reflection_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float, bool)):
            return str(value).strip()
        if isinstance(value, list):
            parts = [ArchiveController._serialize_reflection_value(item) for item in value]
            clean_parts = [part for part in parts if part]
            return ", ".join(clean_parts)
        if isinstance(value, dict):
            label = str(value.get("label", "")).strip()
            key = str(value.get("id", "")).strip()
            scalar_value = value.get("value")
            if label and scalar_value not in (None, ""):
                return f"{label}={scalar_value}"
            if key and scalar_value not in (None, ""):
                return f"{key}={scalar_value}"
            if label:
                return label
            if scalar_value not in (None, ""):
                return str(scalar_value).strip()
            if key:
                return key
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return str(value).strip()

    def _serialize_reflection_entry(self, entry: Any) -> str:
        if not isinstance(entry, dict):
            return ""

        content = entry.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()

        responses = entry.get("responses") or []
        if not isinstance(responses, list):
            return ""

        lines: list[str] = []
        for index, response in enumerate(responses, start=1):
            if not isinstance(response, dict):
                continue
            question_id = str(response.get("question_id", "")).strip() or f"q{index}"
            value_text = self._serialize_reflection_value(response.get("value"))
            if not value_text:
                value_text = self._serialize_reflection_value(response.get("options"))
            if not value_text:
                continue
            lines.append(f"{question_id}: {value_text}")

        return "\n".join(lines).strip()

    async def archive_session(self, session_id: UUID) -> ArchiveResult:
        """Persist session to inner_voices, value_conflicts, debate_statements, reflection_records, review_reminders."""
        pool = self._repo.get_pool() if hasattr(self._repo, "get_pool") else getattr(self._repo, "_pool", None)
        if not pool:
            return ArchiveResult(success=True, session_id=session_id, message="archived_skipped_no_db")

        row = await self._repo.get(session_id)
        if not row:
            return ArchiveResult(success=False, error="session_not_found")

        now = datetime.now(timezone.utc)
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await self._update_completed_at(conn, session_id, now)
                    await self._save_inner_voices(conn, session_id, row)
                    await self._save_value_conflicts(conn, session_id, row)
                    await self._save_debate_statements(conn, session_id, row)
                    await self._save_reflection_records(conn, session_id, row)
                    await self._save_user_interventions(conn, session_id, row)
                    await self._save_annotations(conn, session_id, row)
                    await self._save_synthesis(conn, session_id, row)
                    await self._schedule_review_reminders(conn, session_id, row, now)
        except Exception as e:
            return ArchiveResult(success=False, session_id=session_id, error=str(e))
        return ArchiveResult(success=True, session_id=session_id, message="会话已安全存档")

    async def _update_completed_at(self, conn: Any, session_id: UUID, completed_at: datetime) -> None:
        await conn.execute(
            "UPDATE debate_sessions SET completed_at = $1 WHERE session_id = $2",
            completed_at,
            session_id,
        )

    async def _save_inner_voices(self, conn: Any, session_id: UUID, row: DebateSessionRow) -> None:
        profile = row.conflict_profile_snapshot or {}
        voices = profile.get("inner_voices") or []
        for v in voices:
            if isinstance(v, dict) and v.get("name"):
                await conn.execute(
                    """
                    INSERT INTO inner_voices (voice_id, session_id, name, core_concern, source)
                    VALUES ($1, $2, $3, $4, 'explicit')
                    """,
                    uuid4(),
                    session_id,
                    (v.get("name") or "")[:100],
                    (v.get("core_concern") or "")[:2000],
                )

    async def _save_value_conflicts(self, conn: Any, session_id: UUID, row: DebateSessionRow) -> None:
        profile = row.conflict_profile_snapshot or {}
        conflicts = profile.get("value_conflicts") or []
        for c in conflicts:
            if isinstance(c, dict) and c.get("value_a") and c.get("value_b"):
                await conn.execute(
                    """
                    INSERT INTO value_conflicts (conflict_id, session_id, value_a, value_b, tension_description)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    uuid4(),
                    session_id,
                    (c.get("value_a") or "")[:100],
                    (c.get("value_b") or "")[:100],
                    (c.get("tension_description") or "")[:2000],
                )

    async def _save_debate_statements(self, conn: Any, session_id: UUID, row: DebateSessionRow) -> None:
        state = self._get_debate_state(str(session_id))
        statements = state.get("statements") or []
        for s in statements:
            await conn.execute(
                """
                INSERT INTO debate_statements (statement_id, session_id, agent_id, agent_name, round_number, content)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                uuid4(),
                session_id,
                (s.get("agent_id") or "")[:50],
                (s.get("agent_name") or "")[:100],
                int(s.get("round_number") or 1),
                (s.get("content") or "")[:10000],
            )

    async def _save_reflection_records(self, conn: Any, session_id: UUID, row: DebateSessionRow) -> None:
        reflections = self._get_reflections(str(session_id))
        level_map = {"R1": 1, "R2": 2, "R3": 3, "R4": 4}
        for r in reflections:
            if not isinstance(r, dict):
                continue
            level = str(r.get("level", "R1"))
            content = self._serialize_reflection_entry(r)
            if not content:
                continue
            lv = level_map.get(level, 1)
            await conn.execute(
                """
                INSERT INTO reflection_records (reflection_id, session_id, user_id, reflection_level, content, reflection_type)
                VALUES ($1, $2, $3, $4, $5, 'immediate')
                """,
                uuid4(),
                session_id,
                row.user_id,
                lv,
                content[:10000],
            )

    async def _save_user_interventions(self, conn: Any, session_id: UUID, row: DebateSessionRow) -> None:
        for i in self._get_interventions(str(session_id)):
            await conn.execute(
                """
                INSERT INTO user_interventions (intervention_id, session_id, user_id, intervention_type, round_number, content, target_agent_id, agent_response)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                uuid4(),
                session_id,
                row.user_id,
                (i.get("intervention_type") or "unknown")[:50],
                i.get("round_number"),
                (i.get("content") or "")[:10000],
                (i.get("target_agent_id") or "")[:50],
                (i.get("agent_response") or "")[:10000],
            )

    async def _save_annotations(self, conn: Any, session_id: UUID, row: DebateSessionRow) -> None:
        for a in self._get_annotations(str(session_id)):
            await conn.execute(
                """
                INSERT INTO user_annotations (annotation_id, session_id, user_id, content, round_number, agent_speaking, emotion_tag)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                uuid4(),
                session_id,
                row.user_id,
                (a.get("content") or "")[:10000],
                a.get("round_number"),
                (a.get("agent_speaking") or "")[:50],
                (a.get("emotion_tag") or "")[:50],
            )

    async def _save_synthesis(self, conn: Any, session_id: UUID, row: DebateSessionRow) -> None:
        synthesis = self._get_synthesis(str(session_id))
        if not synthesis or synthesis.get("synthesis_type") == "NONE":
            return
        meta = synthesis.get("meta") or {}
        dilemma = ""
        profile = row.conflict_profile_snapshot or {}
        if isinstance(profile, dict):
            dilemma = profile.get("core_dilemma", "")
        await conn.execute(
            """
            INSERT INTO session_syntheses (
                synthesis_id, session_id, user_id, synthesis_type, narrative,
                voice_positions, core_tensions, consensus_areas, protective_intents,
                convergence_score, novelty_score, value_conflict_intensity,
                debate_rounds, termination_mode, core_dilemma
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
            """,
            uuid4(),
            session_id,
            row.user_id,
            synthesis.get("synthesis_type", "POLYPHONIC_LANDSCAPE"),
            synthesis.get("narrative", "")[:20000],
            json.dumps(synthesis.get("voice_positions", []), ensure_ascii=False),
            json.dumps(synthesis.get("core_tensions", []), ensure_ascii=False),
            json.dumps(synthesis.get("consensus_areas", []), ensure_ascii=False),
            json.dumps(synthesis.get("protective_intents", []), ensure_ascii=False),
            meta.get("convergence_score"),
            meta.get("novelty_score"),
            meta.get("value_conflict_intensity"),
            meta.get("debate_rounds"),
            meta.get("termination_mode"),
            dilemma[:2000],
        )

    async def _schedule_review_reminders(
        self, conn: Any, session_id: UUID, row: DebateSessionRow, completed_at: datetime
    ) -> None:
        one_week = completed_at + timedelta(days=7)
        one_month = completed_at + timedelta(days=30)
        for scheduled_at, reminder_type in [(one_week, "one_week"), (one_month, "one_month")]:
            await conn.execute(
                """
                INSERT INTO review_reminders (reminder_id, session_id, user_id, scheduled_at, reminder_type, status)
                VALUES ($1, $2, $3, $4, $5, 'pending')
                """,
                uuid4(),
                session_id,
                row.user_id,
                scheduled_at,
                reminder_type,
            )

