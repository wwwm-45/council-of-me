"""
Phase 4: Debate engine - Round 1--4.

This module delegates round execution to the AG2-based DebateOrchestrator.
The public API surface remains stable so route handlers do not need to know
whether data is coming from a live orchestrator or a persisted debug snapshot.
"""
import logging
from typing import Any

from app.services.debate.orchestrator import DebateOrchestrator
from app.services.file_store import (
    load_debate_artifacts,
    load_debate_statements,
    load_session_meta,
    load_synthesis,
    save_debate_artifacts,
    save_synthesis,
)

logger = logging.getLogger(__name__)

# Session-scoped orchestrator storage.
# Created on first run_round1(), reused for subsequent rounds.
_orchestrators: dict[str, DebateOrchestrator] = {}

# Legacy in-memory state kept for backward compatibility with get_debate_state().
_debate_state: dict[str, list[dict[str, Any]]] = {}

_EMPTY_SYNTHESIS = {
    "synthesis_type": "NONE",
    "narrative": "暂无辩论内容。",
    "voice_positions": [],
}


def seed_debate_state(session_id: str, statements: list[dict[str, Any]]) -> None:
    """Seed debate state from persisted/debug-loaded data."""
    _debate_state[session_id] = list(statements)


def _get_persisted_statements(session_id: str) -> list[dict[str, Any]]:
    data = load_debate_statements(session_id)
    return data if isinstance(data, list) else []


def _get_persisted_synthesis(session_id: str) -> dict[str, Any] | None:
    data = load_synthesis(session_id)
    if isinstance(data, dict) and data:
        return data
    return None


def _get_or_create_orchestrator(
    session_id: str,
    identity_cards: list[dict],
    profile: dict[str, Any],
) -> DebateOrchestrator:
    """Get existing orchestrator or create a new one."""
    if session_id not in _orchestrators:
        meta = load_session_meta(session_id)
        display_name = str(meta.get("display_name") or "").strip()
        if display_name and not profile.get("user_display_name"):
            profile = {**profile, "user_display_name": display_name}
        _orchestrators[session_id] = DebateOrchestrator(
            session_id=session_id,
            identity_cards=identity_cards,
            profile=profile,
        )
    return _orchestrators[session_id]


async def run_round1(
    session_id: str,
    identity_cards: list[dict],
    profile: dict[str, Any],
) -> list[dict]:
    """Generate one opening statement per agent (R1 parallel); append to state."""
    orchestrator = _get_or_create_orchestrator(session_id, identity_cards, profile)
    statements = await orchestrator.execute_round1_parallel()
    _debate_state[session_id] = list(statements)
    return statements


async def run_next_round(
    session_id: str,
    identity_cards: list[dict],
    profile: dict[str, Any],
    max_rounds: int = 4,
) -> dict[str, Any]:
    """
    Run the next debate round (2, 3, or 4). Appends to state.
    Returns { statements, current_round, done, all_statements }.
    """
    orchestrator = _orchestrators.get(session_id)
    if not orchestrator:
        orchestrator = _get_or_create_orchestrator(session_id, identity_cards, profile)

    if orchestrator.is_done:
        existing = _debate_state.get(session_id, [])
        return {
            "statements": existing,
            "current_round": orchestrator.current_round,
            "done": True,
        }

    result = await orchestrator.execute_round_n()

    existing = _debate_state.get(session_id, [])
    _debate_state[session_id] = existing + result.get("statements", [])
    return result


async def handle_inject(
    session_id: str,
    user_content: str,
    identity_cards: list[dict],
    profile: dict[str, Any],
    target_agent_id: str | None = None,
) -> list[dict]:
    """Generate agent responses to user injection."""
    orchestrator = _orchestrators.get(session_id)
    if not orchestrator:
        orchestrator = _get_or_create_orchestrator(session_id, identity_cards, profile)

    responses = await orchestrator.handle_inject(user_content, target_agent_id)

    existing = _debate_state.get(session_id, [])
    _debate_state[session_id] = existing + responses
    return responses


def get_artifacts(session_id: str) -> dict[str, Any]:
    """Get current debate artifacts for a session."""
    orchestrator = _orchestrators.get(session_id)
    if orchestrator:
        return orchestrator.get_artifacts()
    return load_debate_artifacts(session_id)


def get_debate_state(session_id: str) -> dict[str, Any]:
    """Return current_round and statements."""
    orchestrator = _orchestrators.get(session_id)
    if orchestrator:
        state = orchestrator.get_state()
        return {
            "current_round": state["current_round"],
            "statements": state["statements"],
        }

    statements = _debate_state.get(session_id) or _get_persisted_statements(session_id)
    if statements and session_id not in _debate_state:
        _debate_state[session_id] = list(statements)

    current = max((s.get("round_number") or 1) for s in statements) if statements else 0
    return {
        "current_round": current,
        "statements": statements,
    }


def get_or_create_orchestrator(
    session_id: str,
    identity_cards: list[dict],
    profile: dict[str, Any],
) -> DebateOrchestrator:
    """Public accessor for the orchestrator instance (used by streaming routes)."""
    return _get_or_create_orchestrator(session_id, identity_cards, profile)


def get_orchestrator(session_id: str) -> DebateOrchestrator | None:
    """Return orchestrator for session if it exists (used by SSE endpoints)."""
    return _orchestrators.get(session_id)


def _persist_synthesis_outputs(
    session_id: str,
    orchestrator: DebateOrchestrator,
    result: dict[str, Any],
) -> dict[str, Any]:
    save_synthesis(session_id, result)
    artifacts = orchestrator.get_artifacts()
    if artifacts:
        save_debate_artifacts(session_id, artifacts)
    return result


async def get_enhanced_synthesis(session_id: str) -> dict[str, Any] | None:
    """Return cached enhanced synthesis, or persisted synthesis when available."""
    orchestrator = _orchestrators.get(session_id)
    if not orchestrator:
        return _get_persisted_synthesis(session_id)
    result = await orchestrator.generate_synthesis_async()
    return _persist_synthesis_outputs(session_id, orchestrator, result)


def generate_synthesis_for_session(session_id: str) -> dict[str, Any]:
    """Generate synthesis from the live transcript or persisted debug snapshot."""
    orchestrator = _orchestrators.get(session_id)
    if not orchestrator:
        cached = _get_persisted_synthesis(session_id)
        if cached:
            return cached

        from app.services.synthesis import generate_synthesis as _generate_synthesis

        state = get_debate_state(session_id)
        statements = state.get("statements") or []
        if not statements:
            return dict(_EMPTY_SYNTHESIS)

        result = _generate_synthesis(statements, {})
        save_synthesis(session_id, result)
        return result

    result = orchestrator.generate_synthesis()
    return _persist_synthesis_outputs(session_id, orchestrator, result)


async def generate_synthesis_for_session_async(session_id: str) -> dict[str, Any]:
    """Generate LLM-enhanced synthesis (async). Falls back to persisted/heurstic output."""
    orchestrator = _orchestrators.get(session_id)
    if not orchestrator:
        cached = _get_persisted_synthesis(session_id)
        if cached:
            return cached
        return generate_synthesis_for_session(session_id)

    result = await orchestrator.generate_synthesis_async()
    return _persist_synthesis_outputs(session_id, orchestrator, result)


def record_resonance(session_id: str, agent_id: str, reason: str) -> bool:
    """Record which voice resonated with the user."""
    orchestrator = _orchestrators.get(session_id)
    if not orchestrator:
        return False
    return orchestrator.record_resonance(agent_id, reason)


def submit_followup_response(
    session_id: str,
    followup_id: str,
    responses: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve a pending follow-up gate by routing the answers to the orchestrator."""
    orchestrator = _orchestrators.get(session_id)
    if orchestrator is None:
        raise RuntimeError("No live orchestrator for session")
    return orchestrator.submit_followup_response(followup_id, responses)


def handle_pause(session_id: str) -> bool:
    """Pause the debate. Returns True if orchestrator found."""
    orchestrator = _orchestrators.get(session_id)
    if orchestrator:
        orchestrator.pause()
        return True
    return False


def handle_resume(session_id: str) -> bool:
    """Resume the debate. Returns True if orchestrator found."""
    orchestrator = _orchestrators.get(session_id)
    if orchestrator:
        orchestrator.resume()
        return True
    return False


def cleanup_session(session_id: str) -> None:
    """Remove orchestrator and state for a completed session."""
    _orchestrators.pop(session_id, None)
    _debate_state.pop(session_id, None)
    logger.info("Cleaned up debate state for session %s", session_id)
