"""
DiscussionEngine: pure decision engine for forum-style free discussion.

No LLM calls, no async — purely heuristic scoring for speaker selection,
reply-target detection, and phase termination decisions.

Replaces the fixed round-robin ordering in R2/R3 with dynamic,
score-based speaker selection inspired by Free-MAD debate patterns.

R4 (ROUND4_CONVERGE) is handled by a dedicated 3-step protocol in the
orchestrator and does NOT use the discussion engine.
"""
import random
from dataclasses import dataclass, field
from typing import Optional

from app.services.debate.round_state import DebatePhase, OPPOSING_PAIRS


# ── Exchange limit configuration per phase × complexity ──────────────────────

@dataclass(frozen=True)
class ExchangeLimits:
    """Min/max exchanges and per-agent bounds for a discussion phase."""
    min_exchanges: int
    max_exchanges: int
    min_per_agent: int
    max_per_agent: int


# phase → complexity → limits
_EXCHANGE_CONFIG: dict[DebatePhase, dict[str, ExchangeLimits]] = {
    DebatePhase.ROUND2_CROSS: {
        "L1": ExchangeLimits(3, 6, 1, 3),
        "L2": ExchangeLimits(5, 10, 1, 3),
        "L3": ExchangeLimits(8, 15, 1, 3),
    },
    DebatePhase.ROUND3_DEEPEN: {
        "L1": ExchangeLimits(3, 6, 1, 3),   # L1 doesn't reach R3, but safe default
        "L2": ExchangeLimits(4, 8, 1, 3),
        "L3": ExchangeLimits(6, 12, 1, 3),
    },
}


def get_exchange_limits(phase: DebatePhase, complexity: str) -> ExchangeLimits:
    """Return exchange limits for a given phase and complexity level."""
    phase_config = _EXCHANGE_CONFIG.get(phase, {})
    return phase_config.get(complexity, ExchangeLimits(3, 6, 1, 3))


# ── Scoring weights ──────────────────────────────────────────────────────────

_SCORE_CHALLENGED = 3.0      # agent was directly mentioned/challenged
_SCORE_R3_PRESSURE_BONUS = 1.0  # extra R3 weight for unanswered pressure
_SCORE_R3_DIRECT_RESPONSE_BONUS = 2.0  # immediate answer to latest challenger
_SCORE_OPPOSING = 2.0        # stance opposition to last speaker
_SCORE_COLD_PER_TURN = 1.0   # per turn since last spoke, cap 3
_SCORE_COLD_CAP = 3.0
_SCORE_NEVER_SPOKEN = 2.0    # bonus for agents who haven't spoken at all yet
_SCORE_MONOPOLY = -2.0       # per excess over mean speak count
_SCORE_JITTER_MAX = 0.5      # random tiebreaker


# ── Agent display names for reply-target detection ───────────────────────────

# Chinese display names used in prompts/content — kept in sync with identity cards
_AGENT_NAME_ALIASES: dict[str, list[str]] = {
    "empathic_listener": ["共情倾听者", "共情", "倾听者"],
    "rational_analyst": ["理性分析者", "理性", "分析者"],
    "critical_examiner": ["批判审视者", "批判", "审视者"],
    "creative_explorer": ["创意探索者", "创意", "探索者"],
    "synthesizer": ["整合者", "综合者"],
}


@dataclass
class Exchange:
    """A single exchange (one agent's statement) in the discussion."""
    seq: int              # 1-based sequence number within this phase
    agent_id: str
    agent_name: str
    content: str
    reply_to: Optional[str] = None   # agent_id this is replying to


@dataclass
class DiscussionState:
    """Tracks mutable state during a discussion phase."""
    synthesizer_mid_slot_pending: bool = False
    synthesizer_end_slot_pending: bool = False
    exchanges: list[Exchange] = field(default_factory=list)
    speak_counts: dict[str, int] = field(default_factory=dict)
    was_challenged: dict[str, bool] = field(default_factory=dict)
    last_speaker: Optional[str] = None
    awaiting_user_response: bool = False
    last_spoke_at: dict[str, int] = field(default_factory=dict)  # agent → seq when last spoke


class DiscussionEngine:
    """
    Pure decision engine for free-form multi-agent discussion.

    Usage:
        engine = DiscussionEngine(agent_ids, agent_names, phase, complexity)
        while not engine.should_end_phase(evaluator_goal_met=goal_achieved):
            speaker_id, reply_to = engine.select_next_speaker()
            # ... generate statement via LLM ...
            engine.record_exchange(speaker_id, agent_name, content)
            goal_achieved = evaluator.check(engine.exchanges)
    """

    def __init__(
        self,
        agent_ids: list[str],
        agent_names: dict[str, str],
        phase: DebatePhase,
        complexity: str = "L2",
        limits: ExchangeLimits | None = None,
    ):
        self._agent_ids = list(agent_ids)
        self._agent_names = agent_names  # agent_id → display_name
        self._phase = phase
        self._complexity = complexity
        self._limits = limits or get_exchange_limits(phase, complexity)

        self._state = DiscussionState(
            speak_counts={aid: 0 for aid in agent_ids},
            was_challenged={aid: False for aid in agent_ids},
            last_spoke_at={aid: 0 for aid in agent_ids},
            synthesizer_mid_slot_pending=(
                phase == DebatePhase.ROUND2_CROSS and "synthesizer" in agent_ids
            ),
            synthesizer_end_slot_pending="synthesizer" in agent_ids,
        )

    @property
    def limits(self) -> ExchangeLimits:
        return self._limits

    @property
    def exchange_count(self) -> int:
        return len(self._state.exchanges)

    @property
    def exchanges(self) -> list[Exchange]:
        return list(self._state.exchanges)

    @property
    def awaiting_user_response(self) -> bool:
        return self._state.awaiting_user_response

    # ── Speaker selection ────────────────────────────────────────────────────

    def select_next_speaker(self) -> tuple[str, Optional[str]]:
        """
        Select the next speaker via heuristic scoring.
        Returns (speaker_id, reply_to_agent_id).
        """
        scores: dict[str, float] = {}
        state = self._state
        seq = len(state.exchanges)
        forced_speaker = self._forced_synthesizer_speaker()
        if forced_speaker is not None:
            reply_to = self._determine_reply_to(forced_speaker)
            if state.was_challenged.get(forced_speaker, False):
                state.was_challenged[forced_speaker] = False
            return forced_speaker, reply_to
        eligible_agents: list[str] = []

        for aid in self._agent_ids:
            # Skip agents who hit per-agent max
            if state.speak_counts[aid] >= self._limits.max_per_agent:
                continue
            # Avoid immediate repeat
            if aid == state.last_speaker and len(self._agent_ids) > 1:
                continue
            eligible_agents.append(aid)

        challenged_priority: set[str] = set()
        untouched_candidates: set[str] = set()
        if self._phase == DebatePhase.ROUND3_DEEPEN:
            challenged_priority = {
                aid for aid in eligible_agents if state.was_challenged.get(aid, False)
            }
            untouched_candidates = {
                aid for aid in eligible_agents if state.speak_counts.get(aid, 0) == 0
            }
            # Keep pressure strong early, but after a full round of opportunities,
            # force untouched voices to avoid starvation in sustained pressure loops.
            if (
                challenged_priority
                and untouched_candidates
                and seq >= len(self._agent_ids)
            ):
                eligible_agents = [
                    aid for aid in eligible_agents if aid in untouched_candidates
                ]

        for aid in eligible_agents:
            score = 0.0

            # 1) Challenged bonus
            if state.was_challenged.get(aid, False):
                challenged_bonus = _SCORE_CHALLENGED
                if self._phase == DebatePhase.ROUND3_DEEPEN:
                    challenged_bonus += _SCORE_R3_PRESSURE_BONUS
                    if self._find_latest_challenger(aid) == state.last_speaker:
                        challenged_bonus += _SCORE_R3_DIRECT_RESPONSE_BONUS
                score += challenged_bonus

            # 2) Stance opposition to last speaker
            if state.last_speaker:
                opposing = OPPOSING_PAIRS.get(state.last_speaker)
                if opposing == aid:
                    bonus = _SCORE_OPPOSING
                    if self._phase == DebatePhase.ROUND2_CROSS:
                        bonus *= 2  # R2 doubles opposition score
                    score += bonus

            # 3) Cold-seat bonus (longer silence → higher score)
            last_at = state.last_spoke_at.get(aid, 0)
            gap = seq - last_at
            cold_bonus = min(gap * _SCORE_COLD_PER_TURN, _SCORE_COLD_CAP)
            score += cold_bonus

            # 3b) Extra bonus if never spoken at all (ensures inclusion)
            if state.speak_counts.get(aid, 0) == 0:
                score += _SCORE_NEVER_SPOKEN

            # 4) Anti-monopoly penalty
            mean_speaks = (sum(state.speak_counts.values()) /
                           max(len(self._agent_ids), 1))
            excess = state.speak_counts[aid] - mean_speaks
            if excess > 0:
                score += excess * _SCORE_MONOPOLY

            # 5) Random jitter
            score += random.random() * _SCORE_JITTER_MAX

            scores[aid] = score

        if not scores:
            # All agents at max — pick the one with fewest speaks
            aid = min(self._agent_ids,
                      key=lambda a: state.speak_counts.get(a, 0))
            reply_to = self._determine_reply_to(aid)
            if state.was_challenged.get(aid, False):
                state.was_challenged[aid] = False
            return aid, reply_to

        speaker = max(scores, key=lambda a: scores[a])

        reply_to = self._determine_reply_to(speaker)

        # Clear challenged flag once they get to respond
        if state.was_challenged.get(speaker, False):
            state.was_challenged[speaker] = False

        return speaker, reply_to

    def _forced_synthesizer_speaker(self) -> Optional[str]:
        state = self._state
        if "synthesizer" not in self._agent_ids:
            return None
        if state.last_speaker == "synthesizer" and len(self._agent_ids) > 1:
            return None

        mid_trigger = self._limits.min_exchanges // 2
        end_trigger = max(self._limits.max_exchanges - 1, 1)

        if (
            self._phase == DebatePhase.ROUND2_CROSS
            and state.synthesizer_mid_slot_pending
            and len(state.exchanges) >= mid_trigger
        ):
            return "synthesizer"

        if state.synthesizer_end_slot_pending and len(state.exchanges) >= end_trigger:
            return "synthesizer"

        return None

    def _determine_reply_to(self, speaker_id: str) -> Optional[str]:
        """Determine who this speaker is most likely replying to.

        Synthesizer maps the field instead of replying to a specific agent.
        """
        if speaker_id == "synthesizer":
            return None
        state = self._state
        if not state.exchanges:
            return None

        # If challenged, reply to the challenger
        last_exchange = state.exchanges[-1]

        # If the last exchange challenged this speaker, reply to them
        if state.was_challenged.get(speaker_id, False):
            challenger = self._find_latest_challenger(speaker_id)
            if challenger:
                return challenger
            return last_exchange.agent_id

        # Default: reply to the most recent speaker (natural conversation flow)
        if last_exchange.agent_id != speaker_id:
            return last_exchange.agent_id

        # If the last speaker was self (shouldn't happen with anti-repeat),
        # reply to the one before
        for ex in reversed(state.exchanges[:-1]):
            if ex.agent_id != speaker_id:
                return ex.agent_id

        return None

    def _find_latest_challenger(self, target_agent_id: str) -> Optional[str]:
        """Find the most recent speaker that explicitly challenged target_agent_id."""
        for ex in reversed(self._state.exchanges):
            if ex.agent_id == target_agent_id:
                continue
            if self._mentions_agent(ex.content, target_agent_id):
                return ex.agent_id
        return None

    # ── Reply target detection ───────────────────────────────────────────────

    def detect_reply_target(self, content: str) -> Optional[str]:
        """
        Scan content for agent name references.
        Returns the agent_id that is most likely being addressed.
        """
        for agent_id in self._agent_ids:
            if self._mentions_agent(content, agent_id):
                return agent_id
        return None

    def resolve_reply_to(
        self,
        content: str,
        intended: Optional[str],
        *,
        speaker_id: Optional[str] = None,
    ) -> Optional[str]:
        """Reconcile the pre-assigned reply target with what was actually said.

        The speaker selector guesses a target before generation, but the model
        may address the user directly instead. Keep a reply label only when the
        statement names a voice (strict match), so the transcript never claims
        a reply that didn't happen.
        """
        if (
            intended
            and intended != speaker_id
            and self._mentions_agent_strict(content, intended)
        ):
            return intended
        for agent_id in self._agent_ids:
            if agent_id == speaker_id:
                continue
            if self._mentions_agent_strict(content, agent_id):
                return agent_id
        return None

    def _name_candidates(self, agent_id: str, *, strict: bool) -> list[str]:
        """Name strings that count as mentioning the agent.

        Display names are often possessive ("你的整合自我") while speakers
        address each other with the bare name ("整合自我"), so both forms
        count. Strict mode drops 2-char aliases ("理性", "共情") that collide
        with ordinary prose.
        """
        display = self._agent_names.get(agent_id, "")
        candidates = [display]
        if display.startswith("你的") and len(display) > 2:
            candidates.append(display[2:])
        aliases = _AGENT_NAME_ALIASES.get(agent_id, [])
        if strict:
            candidates.extend(a for a in aliases if len(a) >= 3)
        else:
            candidates.extend(aliases)
        return [c for c in candidates if c]

    def _mentions_agent(self, content: str, agent_id: str) -> bool:
        """Check if content mentions the given agent by name or alias."""
        return any(
            c in content for c in self._name_candidates(agent_id, strict=False)
        )

    def _mentions_agent_strict(self, content: str, agent_id: str) -> bool:
        """Mention check used for reply labelling — no short generic aliases."""
        return any(
            c in content for c in self._name_candidates(agent_id, strict=True)
        )

    # ── Exchange recording ───────────────────────────────────────────────────

    def record_exchange(
        self, agent_id: str, agent_name: str, content: str,
        reply_to: Optional[str] = None,
    ) -> Exchange:
        """
        Record a completed exchange and update internal state.
        Call this after the LLM generates the statement.
        """
        state = self._state
        seq = len(state.exchanges) + 1

        exchange = Exchange(
            seq=seq,
            agent_id=agent_id,
            agent_name=agent_name,
            content=content,
            reply_to=reply_to,
        )
        state.exchanges.append(exchange)

        # Update counters
        state.speak_counts[agent_id] = state.speak_counts.get(agent_id, 0) + 1
        state.last_speaker = agent_id
        state.last_spoke_at[agent_id] = seq
        state.awaiting_user_response = False
        if agent_id == "synthesizer":
            if (
                self._phase == DebatePhase.ROUND2_CROSS
                and state.synthesizer_mid_slot_pending
                and seq >= self._limits.min_exchanges // 2
            ):
                state.synthesizer_mid_slot_pending = False
            if (
                state.synthesizer_end_slot_pending
                and seq >= max(self._limits.max_exchanges - 1, 1)
            ):
                state.synthesizer_end_slot_pending = False

        # Detect who was challenged in this statement
        mentioned = self.detect_reply_target(content)
        if mentioned and mentioned != agent_id:
            state.was_challenged[mentioned] = True

        return exchange

    def record_user_turn(self, content: str) -> None:
        """Mark that a formal user turn needs the next agent response."""
        _ = content
        self._state.awaiting_user_response = True

    # ── Phase termination ────────────────────────────────────────────────────

    def should_end_phase(
        self,
        *,
        evaluator_goal_met: bool = False,
        hold_termination: bool = False,
        end_early: bool = False,
    ) -> bool:
        """
        Decide whether the current discussion phase should end.

        Ends when:
        - Hard cap: exchanges >= max_exchanges (always), OR
        - Goal-driven: exchanges >= min_exchanges AND all agents spoke min_per_agent
          AND evaluator signals the round goal has been achieved.

        The evaluator_goal_met flag is set by the orchestrator's RoundEvaluator
        once it determines the phase goal has been satisfied.
        """
        n = len(self._state.exchanges)
        limits = self._limits

        if self._state.awaiting_user_response:
            return False

        # Hard cap: always end
        if n >= limits.max_exchanges:
            return True

        # Below minimum: never end
        if n < limits.min_exchanges:
            return False

        if end_early:
            return True

        if hold_termination:
            return False

        # Check all agents spoke minimum times
        for aid in self._agent_ids:
            if self._state.speak_counts.get(aid, 0) < limits.min_per_agent:
                return False

        # Goal-driven: end when evaluator says goal achieved
        if evaluator_goal_met:
            return True

        return False

    # ── Helpers ──────────────────────────────────────────────────────────────

    def get_recent_exchanges(self, n: int = 5) -> list[Exchange]:
        """Return the last N exchanges."""
        return self._state.exchanges[-n:]

    def get_speak_counts(self) -> dict[str, int]:
        """Return a copy of speak counts."""
        return dict(self._state.speak_counts)
