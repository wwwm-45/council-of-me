"""
Dual-layer memory manager for multi-agent debate.

- AgentMemory: per-agent private context (own statements, challenges, concessions)
- SharedMemory: session-wide state (transcript, summaries, interventions)

Context building follows a 5-layer structured strategy with overflow protection.

Layers:
  1. Identity Anchor   (~600 chars, never compressed)
  2. Position Evolution (~400 chars, from R2 onwards)
  3. Current Artifact   (~500 chars, never compressed)
  4. Recent Exchanges   (last 10, ~2500 chars, compressible)
  5. User Interventions (~300 chars)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AgentMemory:
    """Private memory for a single agent."""
    agent_id: str
    identity_card: dict[str, Any]
    my_statements: dict[int, list[str]] = field(default_factory=dict)  # round -> [content, ...]
    stance_evolution: list[str] = field(default_factory=list)
    concessions: list[str] = field(default_factory=list)
    user_interventions_received: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SharedMemory:
    """Session-wide shared memory."""
    conflict_profile: dict[str, Any] = field(default_factory=dict)
    round_summaries: dict[int, str] = field(default_factory=dict)
    user_interventions: list[dict[str, Any]] = field(default_factory=list)
    user_turns: list[dict[str, Any]] = field(default_factory=list)
    full_transcript: list[dict[str, Any]] = field(default_factory=list)


# Maximum context length in characters before compression kicks in.
# Roughly 1 Chinese character ~ 1 token; 8000 is the restructured budget.
_MAX_CONTEXT_CHARS = 8000


class MemoryManager:
    """
    Manages dual-layer memory for a debate session.
    Provides 5-layer context building with artifact injection and overflow protection.
    """

    def __init__(self, conflict_profile: dict[str, Any]):
        self._agent_memories: dict[str, AgentMemory] = {}
        self._shared = SharedMemory(conflict_profile=conflict_profile)
        self._current_artifact: Any = None  # Current round's artifact (PositionMap, TensionMap, etc.)
        self._agent_evolutions: dict[str, Any] = {}  # Per-agent position evolution

    # -- Registration --

    def register_agent(self, agent_id: str, identity_card: dict[str, Any]) -> None:
        self._agent_memories[agent_id] = AgentMemory(
            agent_id=agent_id,
            identity_card=identity_card,
        )

    # -- Artifact / Evolution setters --

    def set_current_artifact(self, artifact: Any) -> None:
        """Set the current round's artifact for context injection."""
        self._current_artifact = artifact

    def set_agent_evolution(self, agent_id: str, evolution: Any) -> None:
        """Set an agent's position evolution data."""
        self._agent_evolutions[agent_id] = evolution

    # -- Recording --

    def record_statement(
        self, agent_id: str, round_num: int, content: str, agent_name: str = "",
    ) -> None:
        mem = self._agent_memories.get(agent_id)
        if mem:
            mem.my_statements.setdefault(round_num, []).append(content)
            mem.stance_evolution.append(content)
        self._shared.full_transcript.append({
            "agent_id": agent_id,
            "agent_name": agent_name,
            "round": round_num,
            "content": content,
        })

    def record_round_summary(self, round_num: int, summary: str) -> None:
        self._shared.round_summaries[round_num] = summary

    def record_user_intervention(self, intervention: dict[str, Any]) -> None:
        self._shared.user_interventions.append(intervention)
        target = intervention.get("target_agent_id")
        if target and target in self._agent_memories:
            self._agent_memories[target].user_interventions_received.append(intervention)
        elif not target:
            # Broadcast: deliver to all agents
            for mem in self._agent_memories.values():
                mem.user_interventions_received.append(intervention)

    def record_user_turn(self, turn: dict[str, Any]) -> None:
        self._shared.user_turns.append(turn)
        self._shared.full_transcript.append({
            "agent_id": "__user__",
            "agent_name": "我",
            "round": turn.get("round_number", turn.get("round")),
            "content": turn.get("content", ""),
            "type": "user_turn",
            "is_user_turn": True,
        })

    def record_concession(self, agent_id: str, text: str) -> None:
        mem = self._agent_memories.get(agent_id)
        if mem:
            mem.concessions.append(text)

    # -- Queries --

    def get_agent_statements(
        self, agent_id: str, round_num: Optional[int] = None,
    ) -> Any:
        """
        Backward-compatible accessor.
        If round_num is given, return that round's content as a single joined string (or "").
        Otherwise return {round -> joined_content} dict.
        """
        mem = self._agent_memories.get(agent_id)
        if not mem:
            return "" if round_num is not None else {}
        if round_num is not None:
            stmts = mem.my_statements.get(round_num, [])
            return " ".join(stmts) if stmts else ""
        return {r: " ".join(stmts) for r, stmts in mem.my_statements.items()}

    def get_agent_statements_flat(self, agent_id: str) -> list[str]:
        """Return all of an agent's own statements across rounds, in round order."""
        mem = self._agent_memories.get(agent_id)
        if not mem:
            return []
        flat: list[str] = []
        for rnd in sorted(mem.my_statements.keys()):
            flat.extend(mem.my_statements[rnd])
        return flat

    def get_round_summary(self, round_num: int) -> str:
        return self._shared.round_summaries.get(round_num, "")

    def get_full_transcript(self) -> list[dict[str, Any]]:
        return list(self._shared.full_transcript)

    def get_shared_memory(self) -> SharedMemory:
        return self._shared

    # -- Context building (5-layer structured strategy) --

    def build_agent_context(self, agent_id: str, current_round: int) -> str:
        """
        Build a prompt-ready context string for the agent.

        5-layer structure:
        1. Identity Anchor   (~600 chars, never compressed)
        2. Position Evolution (~400 chars, from R2 onwards)
        3. Current Artifact   (~500 chars, never compressed)
        4. Recent Exchanges   (last 10, ~2500 chars, compressible)
        5. User Interventions (~300 chars)
        """
        mem = self._agent_memories.get(agent_id)
        if not mem:
            return ""

        # -- Layer 1: Identity Anchor (never compressed) --
        layer1 = self._build_identity_layer(mem)

        # -- Layer 2: Position Evolution (from R2 onwards) --
        layer2 = self._build_evolution_layer(agent_id, current_round)

        # -- Layer 3: Current Artifact (never compressed) --
        layer3 = self._build_artifact_layer()

        # -- Layer 4: Recent Exchanges (compressible) --
        layer4 = self._build_recent_exchanges_layer(current_round, num_exchanges=10)

        # -- Layer 5: User Participation --
        layer5 = self._build_user_participation_layer(mem)

        # Assemble non-empty layers
        parts = [p for p in (layer1, layer2, layer3, layer4, layer5) if p]
        context = "\n\n".join(parts)

        # Overflow protection with tiered compression
        if len(context) > _MAX_CONTEXT_CHARS:
            context = self._overflow_compress(
                mem, agent_id, current_round, layer1, layer3, layer5,
            )

        return context

    # -- Layer builders --

    @staticmethod
    def _build_identity_layer(mem: AgentMemory) -> str:
        """Layer 1: Identity anchor (~600 chars, never compressed)."""
        card = mem.identity_card
        name = card.get("display_name", card.get("role", ""))
        prompt_snippet = card.get("system_prompt", "")[:500]
        return f"\u3010\u4f60\u7684\u8eab\u4efd\u3011{name}\u3002{prompt_snippet}"

    def _build_evolution_layer(self, agent_id: str, current_round: int) -> str:
        """Layer 2: Position evolution (~400 chars, R2+ only)."""
        if current_round < 2:
            return ""
        evo = self._agent_evolutions.get(agent_id)
        if evo is None:
            return ""
        prompt_text = evo.to_prompt_text()
        return f"\u3010\u4f60\u7684\u7acb\u573a\u6f14\u5316\u3011\n{prompt_text}"

    def _build_artifact_layer(self) -> str:
        """Layer 3: Current artifact (~500 chars, never compressed)."""
        if self._current_artifact is None:
            return ""
        prompt_text = self._current_artifact.to_prompt_text()
        return f"\u3010\u5f53\u524d\u8bba\u8ff0\u5730\u56fe\u3011\n{prompt_text}"

    def _build_recent_exchanges_layer(
        self, current_round: int, num_exchanges: int = 10,
    ) -> str:
        """Layer 4: Recent exchanges from transcript."""
        recent_stmts = [
            e for e in self._shared.full_transcript
            if e["round"] in (current_round, current_round - 1)
        ][-num_exchanges:]
        if not recent_stmts:
            return ""
        lines: list[str] = []
        for s in recent_stmts:
            agent_label = "我" if s.get("is_user_turn") else (s.get("agent_name") or s["agent_id"])
            reply_to = s.get("reply_to")
            if reply_to:
                lines.append(
                    f"\u3010{agent_label}\u3011(\u56de\u590d{reply_to}) {s['content']}"
                )
            else:
                lines.append(f"\u3010{agent_label}\u3011{s['content']}")
        return "\u3010\u8fd1\u671f\u8ba8\u8bba\u3011\n" + "\n".join(lines)

    def _build_user_participation_layer(self, mem: AgentMemory) -> str:
        """Layer 5: formal user turns plus legacy participation signals."""
        recent_turns = self._shared.user_turns[-3:]
        recent_interventions = mem.user_interventions_received[-3:]
        if not recent_turns and not recent_interventions:
            return ""

        lines = [
            "These turns express where the user wants the debate to move and must be responded to as part of the debate, not side notes."
        ]
        for turn in recent_turns:
            content = turn.get("content", "")
            if turn.get("followup_id"):
                lines.append(
                    f"\u3010\u6211\u56de\u5e94\u8ffd\u95ee\u3011{content}"
                    "\uff08\u8bf7\u636e\u6b64\u63a8\u8fdb\uff0c\u522b\u518d\u66ff\u6211\u5047\u8bbe\uff1b\u82e5\u6211\u5df2\u5426\u8ba4\u8fd9\u4e00\u70b9\uff0c\u5c31\u6362\u4e00\u4e2a\u89d2\u5ea6\u3002\uff09"
                )
            else:
                lines.append(f"\u3010\u6211\u3011{content}")
        lines.extend(
            f"User {iv.get('type', '')}: {iv.get('content', '')}"
            for iv in recent_interventions
            if iv.get("content")
        )
        return "\u3010User Participation\u3011\n" + "\n".join(lines)

    # -- Overflow --

    def _overflow_compress(
        self,
        mem: AgentMemory,
        agent_id: str,
        current_round: int,
        layer1: str,
        layer3: str,
        layer5: str,
    ) -> str:
        """
        Tiered overflow strategy:
        1. Reduce Layer 4 from last 10 -> last 8 -> last 6
        2. Truncate Layer 2 (keep first 200 chars)
        3. Never compress Layer 1 or Layer 3
        """
        # Try reducing recent exchanges: 8, then 6
        for num_exchanges in (8, 6):
            layer2 = self._build_evolution_layer(agent_id, current_round)
            layer4 = self._build_recent_exchanges_layer(current_round, num_exchanges)
            parts = [p for p in (layer1, layer2, layer3, layer4, layer5) if p]
            context = "\n\n".join(parts)
            if len(context) <= _MAX_CONTEXT_CHARS:
                return context

        # Still over: truncate Layer 2
        layer2_full = self._build_evolution_layer(agent_id, current_round)
        if layer2_full and len(layer2_full) > 200:
            layer2_truncated = layer2_full[:200] + "...(已截断)"
        else:
            layer2_truncated = layer2_full

        layer4 = self._build_recent_exchanges_layer(current_round, num_exchanges=6)
        parts = [p for p in (layer1, layer2_truncated, layer3, layer4, layer5) if p]
        context = "\n\n".join(parts)

        # Final fallback: hard truncate preserving head (Layer 1 + Layer 3 always present)
        if len(context) > _MAX_CONTEXT_CHARS:
            context = context[:_MAX_CONTEXT_CHARS]

        return context
