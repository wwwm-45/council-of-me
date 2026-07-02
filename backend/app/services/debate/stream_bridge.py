"""
StreamBridge: SSE-based streaming for debate output.

Wraps DebateOrchestrator to emit Server-Sent Events as agents generate
statements. Uses simulated streaming (split into small character chunks,
emit with delay) since LLM calls go through app/services/llm.py which
returns complete strings.
"""
import asyncio
import json
from dataclasses import dataclass
from typing import Any, AsyncGenerator, TYPE_CHECKING

from app.services.debate.publish_formatter import PublishFormatter
from app.services.file_store import (
    load_debate_statements,
    save_debate_artifacts,
    save_debate_statements,
    save_synthesis,
)

if TYPE_CHECKING:
    from app.services.debate.orchestrator import DebateOrchestrator


@dataclass
class SSEEvent:
    """Single Server-Sent Event."""
    event: str            # event type name
    data: dict[str, Any]  # JSON payload

    def encode(self) -> str:
        """Format as SSE wire format."""
        return f"event: {self.event}\ndata: {json.dumps(self.data, ensure_ascii=False)}\n\n"


class StreamBridge:
    """
    Bridges DebateOrchestrator to SSE event stream.
    Does NOT replace the orchestrator — wraps it to emit events
    as agents generate statements.
    """

    def __init__(
        self,
        orchestrator: "DebateOrchestrator",
        char_delay: float = 0.12,
        inter_agent_delay: float = 0.5,
        chunk_size: int = 1,
    ):
        self._orch = orchestrator
        self._char_delay = char_delay
        self._inter_agent_delay = inter_agent_delay
        self._chunk_size = chunk_size
        self._publish_formatter = PublishFormatter()

    def _get_agent_roster(self) -> list[dict[str, str]]:
        """Return roster of all possible speakers (order not guaranteed)."""
        return [
            {
                "agent_id": a.get_agent_id(),
                "agent_name": a.get_display_name(),
            }
            for a in self._orch._agents
        ]

    def _persist_round_outputs(self, statements: list[dict[str, Any]]) -> None:
        """Persist the current round transcript plus latest artifact snapshot."""
        if statements:
            existing = load_debate_statements(self._orch.session_id)
            seen_ids = {
                stmt.get("statement_id")
                for stmt in existing
                if stmt.get("statement_id")
            }
            merged = list(existing)
            for statement in statements:
                statement_id = statement.get("statement_id")
                if statement_id and statement_id in seen_ids:
                    continue
                merged.append(statement)
                if statement_id:
                    seen_ids.add(statement_id)
            save_debate_statements(self._orch.session_id, merged)
        artifacts = self._orch.get_artifacts()
        if artifacts:
            save_debate_artifacts(self._orch.session_id, artifacts)

    def _persist_synthesis(self, synthesis_result: dict[str, Any]) -> None:
        """Persist final synthesis alongside the latest artifacts."""
        save_synthesis(self._orch.session_id, synthesis_result)
        artifacts = self._orch.get_artifacts()
        if artifacts:
            save_debate_artifacts(self._orch.session_id, artifacts)

    async def stream_full_debate(self) -> AsyncGenerator[str, None]:
        """
        Stream the entire remaining debate from current state to completion.
        Includes all remaining rounds + synthesis.
        """
        from app.services.debate.round_state import DebatePhase

        _R4_PHASES = (
            DebatePhase.R4_REFLECTION,
            DebatePhase.R4_MAPPING,
            DebatePhase.R4_FINAL,
        )

        blocked_reason = self._get_blocked_reason()
        if self._orch.current_phase == DebatePhase.ROUND1_OPENING and blocked_reason:
            yield SSEEvent("error", {
                "message": blocked_reason,
                "kind": "preflight_blocked",
            }).encode()
            return

        # Stream R1 if not yet done
        if self._orch.current_phase == DebatePhase.ROUND1_OPENING:
            async for event in self.stream_round1():
                yield event

        # Stream subsequent rounds
        while not self._orch.is_done:
            if self._orch.current_phase in _R4_PHASES:
                async for event in self.stream_r4():
                    yield event
            else:
                async for event in self.stream_round_n(allow_early_termination=False):
                    yield event

        # Stream synthesis
        synthesis = self._orch.generate_synthesis()
        self._persist_synthesis(synthesis)
        async for event in self.stream_synthesis(synthesis):
            yield event

        yield SSEEvent("debate_complete", {
            "total_rounds": self._orch.current_round,
        }).encode()

    async def stream_round1(self) -> AsyncGenerator[str, None]:
        """
        Stream R1 live so user turns can be inserted between openings.
        """
        blocked_reason = self._get_blocked_reason()
        if blocked_reason:
            yield SSEEvent("error", {
                "message": blocked_reason,
                "kind": "preflight_blocked",
            }).encode()
            return

        yield SSEEvent("round_start", {
            "round": 1,
            "phase": "round1_opening",
            "agent_order": self._get_agent_roster(),
            "agent_roster": self._get_agent_roster(),
        }).encode()

        i = 0
        round_statements: list[dict[str, Any]] = []
        incremental = getattr(self._orch, "execute_round1_live_incremental", None)
        if incremental is None:
            incremental = self._orch.execute_round1_parallel_incremental
        async for stmt in incremental():
            round_statements.append(stmt)
            if stmt.get("is_user_turn") or stmt.get("type") == "user_turn":
                yield SSEEvent("user_turn", stmt).encode()
                continue

            # Pause between agents for natural pacing
            if i > 0:
                await asyncio.sleep(self._inter_agent_delay)
            i += 1

            yield SSEEvent("agent_start", {
                "agent_id": stmt["agent_id"],
                "agent_name": stmt["agent_name"],
                "round": 1,
                "reply_to": stmt.get("reply_to"),
            }).encode()

            for chunk in self._split_chunks(stmt["content"]):
                if self._orch.is_paused:
                    await self._wait_for_resume()
                yield SSEEvent("agent_token", {
                    "agent_id": stmt["agent_id"],
                    "content": chunk,
                }).encode()
                await asyncio.sleep(self._char_delay)

            yield SSEEvent("agent_end", {
                "agent_id": stmt["agent_id"],
                "statement_id": stmt["statement_id"],
            }).encode()

        yield SSEEvent("round_end", {
            "round": 1,
        }).encode()
        self._persist_round_outputs(round_statements)

        # Emit artifact event if available for R1
        artifact = self._get_round_artifact(1)
        if artifact:
            yield SSEEvent("round_artifact", artifact).encode()
        agent_evolutions = self._get_agent_evolution_payload()
        if agent_evolutions:
            yield SSEEvent("agent_evolution", agent_evolutions).encode()

    async def stream_round_n(
        self,
        *,
        allow_early_termination: bool = True,
    ) -> AsyncGenerator[str, None]:
        """
        Stream current round (R2/R3/R4) as forum-style discussion.
        Speaker order is dynamic — the DiscussionEngine decides who speaks next.

        ROUND3_DEEPEN chains straight into R3_ACKNOWLEDGE (divergence map +
        follow-up gate) in the same stream: both are user-visible "round 3",
        so the client never needs an extra next-round press between them.
        """
        from app.services.debate.round_state import DebatePhase

        while True:
            entry_phase = self._orch.current_phase
            async for event in self._stream_single_phase():
                yield event
            # Chain only on the deepen -> acknowledge transition; any other
            # outcome (paused mid-phase, other phases) ends this press.
            if (
                entry_phase == DebatePhase.ROUND3_DEEPEN
                and self._orch.current_phase == DebatePhase.R3_ACKNOWLEDGE
                and not self._orch.is_paused
            ):
                continue
            break

        if allow_early_termination:
            prepare_offer = getattr(self._orch, "prepare_early_termination_offer_if_needed", None)
            await_resolution = getattr(self._orch, "await_early_termination_resolution", None)
            if callable(prepare_offer) and callable(await_resolution):
                offer = await prepare_offer()
                if offer:
                    yield SSEEvent("convergence_high", offer).encode()
                    resolution = await await_resolution()
                    for skipped_phase in resolution.get("skipped_phases", []):
                        yield SSEEvent("round_skip", {
                            "phase": skipped_phase,
                            "reason": "early_termination",
                        }).encode()
                    self._persist_round_outputs([])

    async def _stream_single_phase(self) -> AsyncGenerator[str, None]:
        """Stream exactly one orchestrator phase plus its follow-up gate."""
        from app.services.debate.discussion_engine import get_exchange_limits

        round_num = self._orch.current_round
        phase = self._orch.current_phase

        get_dynamic_limits = getattr(self._orch, "get_phase_exchange_limits", None)
        dynamic_limits = (
            get_dynamic_limits(phase)
            if callable(get_dynamic_limits)
            else None
        )
        if not (
            isinstance(dynamic_limits, (tuple, list))
            and len(dynamic_limits) == 2
        ):
            phase_exchange_limits = getattr(self._orch, "_phase_exchange_limits", None)
            dynamic_limits = (
                phase_exchange_limits.get(phase.value)
                if isinstance(phase_exchange_limits, dict)
                else None
            )
        if (
            isinstance(dynamic_limits, (tuple, list))
            and len(dynamic_limits) == 2
        ):
            min_exchanges, max_exchanges = dynamic_limits
        else:
            limits = get_exchange_limits(phase, self._orch.complexity)
            min_exchanges, max_exchanges = limits.min_exchanges, limits.max_exchanges

        yield SSEEvent("round_start", {
            "round": round_num,
            "phase": phase.value,
            "agent_order": self._get_agent_roster(),   # backward compat
            "agent_roster": self._get_agent_roster(),
            "expected_exchanges": [min_exchanges, max_exchanges],
        }).encode()

        first = True
        exchange_count = 0
        round_statements: list[dict[str, Any]] = []
        status_event_types = {"phase_evaluating", "artifact_start", "artifact_end"}
        async for stmt in self._orch.execute_round_n_incremental():
            stmt_type = stmt.get("type")
            if stmt_type in status_event_types:
                payload = {key: value for key, value in stmt.items() if key != "type"}
                yield SSEEvent(stmt_type, payload).encode()
                continue

            if stmt.get("is_user_turn") or stmt.get("type") == "user_turn":
                round_statements.append(stmt)
                yield SSEEvent("user_turn", stmt).encode()
                continue

            exchange_count += 1
            round_statements.append(stmt)

            if not first:
                await asyncio.sleep(self._inter_agent_delay)
            first = False

            yield SSEEvent("agent_start", {
                "agent_id": stmt["agent_id"],
                "agent_name": stmt["agent_name"],
                "round": stmt["round_number"],
                "reply_to": stmt.get("reply_to"),
                "exchange_seq": stmt.get("exchange_seq", exchange_count),
            }).encode()

            for chunk in self._split_chunks(stmt["content"]):
                if self._orch.is_paused:
                    await self._wait_for_resume()
                yield SSEEvent("agent_token", {
                    "agent_id": stmt["agent_id"],
                    "content": chunk,
                }).encode()
                await asyncio.sleep(self._char_delay)

            yield SSEEvent("agent_end", {
                "agent_id": stmt["agent_id"],
                "statement_id": stmt["statement_id"],
            }).encode()

            # Exchange progress metadata
            yield SSEEvent("exchange_meta", {
                "exchange_seq": stmt.get("exchange_seq", exchange_count),
                "total_min": min_exchanges,
                "total_max": max_exchanges,
            }).encode()

        yield SSEEvent("round_end", {
            "round": round_num,
            "total_exchanges": exchange_count,
        }).encode()
        self._persist_round_outputs(round_statements)

        # Emit artifact event if available for this round
        artifact = self._get_round_artifact(round_num)
        if artifact:
            yield SSEEvent("round_artifact", artifact).encode()
        agent_evolutions = self._get_agent_evolution_payload()
        if agent_evolutions:
            yield SSEEvent("agent_evolution", agent_evolutions).encode()

        async for followup_event in self._stream_followup_gate(phase, round_num):
            yield followup_event

    async def _stream_followup_gate(
        self, phase, round_num: int,
    ) -> AsyncGenerator[str, None]:
        """Open the follow-up gate (if the orchestrator wants one) and stream its events."""
        prepare = getattr(self._orch, "prepare_followup_gate_if_needed", None)
        resolve = getattr(self._orch, "await_followup_resolution", None)
        if not (callable(prepare) and callable(resolve)):
            return
        # round_end already told the client this round finished speaking, so the
        # next-round button is live. Composing the question is a slow LLM call,
        # so announce it up front: the client suppresses the button until the
        # gate opens (followup_questions) or is skipped, closing the race where a
        # next-round click would land mid-compose.
        gate_possible_fn = getattr(self._orch, "followup_gate_possible", None)
        gate_possible = bool(gate_possible_fn(phase)) if callable(gate_possible_fn) else False
        if gate_possible:
            yield SSEEvent("followup_preparing", {"round": round_num}).encode()
        offer = await prepare(phase, round_num)
        if not offer:
            if gate_possible:
                yield SSEEvent("followup_skipped", {"round": round_num}).encode()
            return
        yield SSEEvent("followup_questions", offer).encode()
        resolution = await resolve()
        yield SSEEvent("followup_resolved", resolution).encode()

    async def stream_r4(self) -> AsyncGenerator[str, None]:
        """Stream the R4 3-step convergence protocol."""
        current_phase = self._orch.current_phase
        round_statements: list[dict[str, Any]] = []
        yield SSEEvent("round_start", {
            "round": 4,
            "phase": current_phase.value,
            "agent_order": self._get_agent_roster(),
            "agent_roster": self._get_agent_roster(),
        }).encode()

        async for event in self._orch.execute_r4_protocol():
            event_type = event.get("type", "unknown")

            if event_type == "divergence_reanchor":
                round_statements.append(event)
                async for speech_event in self._stream_agent_statement(event):
                    yield speech_event
                continue

            if event_type == "r4_reflection":
                round_statements.append(event)
                yield SSEEvent("r4_reflection", {
                    "agent_id": event["agent_id"],
                    "agent_name": event["agent_name"],
                    "content": event["content"],
                    "round_number": 4,
                    "statement_id": event.get("statement_id", ""),
                    "streamed": True,
                }).encode()
                async for speech_event in self._stream_agent_statement(event):
                    yield speech_event
            elif event_type == "r4_mapping":
                yield SSEEvent("r4_mapping", {
                    "convergence_map": event["convergence_map"],
                }).encode()
            elif event_type == "r4_final":
                round_statements.append(event)
                yield SSEEvent("r4_final", {
                    "agent_id": event["agent_id"],
                    "agent_name": event["agent_name"],
                    "content": event["content"],
                    "round_number": 4,
                    "statement_id": event.get("statement_id", ""),
                    "streamed": True,
                }).encode()
                async for speech_event in self._stream_agent_statement(event):
                    yield speech_event

        yield SSEEvent("round_end", {
            "round": 4,
        }).encode()
        self._persist_round_outputs(round_statements)
        agent_evolutions = self._get_agent_evolution_payload()
        if agent_evolutions:
            yield SSEEvent("agent_evolution", agent_evolutions).encode()

    async def _stream_agent_statement(
        self,
        statement: dict[str, Any],
    ) -> AsyncGenerator[str, None]:
        """Emit an already-generated statement through the standard speech stream."""
        yield SSEEvent("agent_start", {
            "agent_id": statement["agent_id"],
            "agent_name": statement["agent_name"],
            "round": statement.get("round_number", 4),
            "reply_to": statement.get("reply_to"),
        }).encode()

        for chunk in self._split_chunks(statement.get("content", "")):
            if self._orch.is_paused:
                await self._wait_for_resume()
            yield SSEEvent("agent_token", {
                "agent_id": statement["agent_id"],
                "content": chunk,
            }).encode()
            await asyncio.sleep(self._char_delay)

        yield SSEEvent("agent_end", {
            "agent_id": statement["agent_id"],
            "statement_id": statement.get("statement_id", ""),
        }).encode()

    async def stream_synthesis(
        self, synthesis_result: dict[str, Any],
    ) -> AsyncGenerator[str, None]:
        """Stream synthesis result in small chunks."""
        yield SSEEvent("synthesis_start", {}).encode()

        narrative = synthesis_result.get("narrative", "")
        for chunk in self._split_chunks(narrative):
            yield SSEEvent("synthesis_token", {
                "content": chunk,
            }).encode()
            await asyncio.sleep(self._char_delay)

        yield SSEEvent("synthesis_end", {
            "synthesis_type": synthesis_result.get("synthesis_type", ""),
            "voice_positions": synthesis_result.get("voice_positions", []),
        }).encode()

    def _get_round_artifact(self, round_num: int) -> dict[str, Any] | None:
        """Return the artifact dict for the given round, or None."""
        artifact = self._get_round_artifact_source(round_num)
        if artifact is None:
            return None

        data = self._publish_formatter.build_round_payload(
            round_number=round_num,
            phase=artifact["phase"],
            artifact=artifact["object"],
            spine=self._get_spine(),
            audit_result=self._get_round_audit(round_num),
            statements=artifact.get("statements", []),
            display_name_map=self._get_display_name_map(),
        )
        return {
            "type": artifact["type"],
            "data": data,
        }

    def _get_agent_evolution_payload(self) -> dict[str, Any] | None:
        getter = getattr(self._orch, "get_agent_evolutions", None)
        if callable(getter):
            evolutions = getter()
        else:
            evolutions = [
                evo.to_dict()
                for evo in getattr(self._orch, "_agent_evolutions", {}).values()
            ]
        if not isinstance(evolutions, list) or not evolutions:
            return None
        return {"agent_evolutions": evolutions}

    def _get_round_artifact_source(self, round_num: int) -> dict[str, Any] | None:
        getter = getattr(self._orch, "get_round_artifact", None)
        if callable(getter):
            artifact = getter(round_num)
            if artifact is not None:
                return artifact
        return self._get_legacy_round_artifact(round_num)

    def _get_legacy_round_artifact(self, round_num: int) -> dict[str, Any] | None:
        orch = self._orch
        statements = [
            statement
            for statement in self._get_all_statements()
            if statement.get("round_number") == round_num
        ]
        if round_num == 1 and getattr(orch, "_position_map", None):
            return {
                "type": "position_map",
                "phase": "round1_opening",
                "object": orch._position_map,
                "statements": statements,
            }
        if round_num == 2 and getattr(orch, "_tension_map", None):
            return {
                "type": "tension_map",
                "phase": "round2_cross",
                "object": orch._tension_map,
                "statements": statements,
            }
        if round_num == 3 and getattr(orch, "_engagement_record", None):
            return {
                "type": "engagement_record",
                "phase": "round3_deepen",
                "object": orch._engagement_record,
                "statements": statements,
            }
        return None

    def _get_round_audit(self, round_num: int) -> Any:
        getter = getattr(self._orch, "get_round_audit", None)
        if callable(getter):
            return getter(round_num)
        round_audits = getattr(self._orch, "_round_audits", {})
        return round_audits.get(round_num)

    def _get_blocked_reason(self) -> str | None:
        getter = getattr(self._orch, "get_state", None)
        if callable(getter):
            state = getter()
            reason = state.get("blocked_reason")
            if reason:
                return reason
        return getattr(self._orch, "_blocked_reason", None)

    def _get_display_name_map(self) -> dict[str, str]:
        getter = getattr(self._orch, "get_display_name_map", None)
        if callable(getter):
            display_name_map = getter()
            if display_name_map:
                return dict(display_name_map)
        return {
            agent.get_agent_id(): agent.get_display_name()
            for agent in getattr(self._orch, "_agents", [])
        }

    def _get_spine(self) -> Any:
        return getattr(self._orch, "spine", None) or getattr(self._orch, "_spine", None)

    def _get_all_statements(self) -> list[dict[str, Any]]:
        getter = getattr(self._orch, "get_all_statements", None)
        if callable(getter):
            return list(getter())
        return list(getattr(self._orch, "_all_statements", []))

    async def _wait_for_resume(self) -> None:
        """Block until orchestrator is unpaused."""
        while self._orch.is_paused:
            await asyncio.sleep(0.2)

    def _split_chunks(self, text: str) -> list[str]:
        """Split text into small character chunks for streaming."""
        if not text:
            return []
        size = self._chunk_size
        return [text[i:i + size] for i in range(0, len(text), size)]
