"""
DebateOrchestrator: main entry point for the AG2-based debate system.

Self-built orchestration (閺傝顢岯): AG2 agents are state containers,
this class controls all round logic, agent ordering, and memory.
No AG2 GroupChat 閳?we iterate agents manually per round.

Task 9 integration: evaluator-driven artifacts, R4 3-step protocol,
LLM-based consistency, and structured memory with artifact injection.
"""
import asyncio
import logging
import re
from copy import deepcopy
from typing import Any, AsyncGenerator, Optional
from uuid import uuid4

from app import config
from app.services.debate.agent_adapter import AG2DebateAgent
from app.services.debate.agent_factory import (
    build_agent_states,
    build_llm_config,
    create_all_agents,
)
from app.services.debate.blackboard import DebateBlackboard, StatementRecord
from app.services.llm import is_llm_error
from app.services.debate.round_state import RoundStateMachine, DebatePhase
from app.services.debate.memory_manager import MemoryManager
from app.services.debate.consistency import ConsistencyMonitor
from app.services.debate.discussion_engine import DiscussionEngine
from app.services.debate.exchange_budget import compute_exchange_budget
from app.services.file_store import (
    save_debate_statements,
    save_session_meta,
)
from app.services.debate.model_router import ModelRouter
from app.services.debate.termination import detect_concession
from app.services.debate.round_evaluator import RoundEvaluator, EVAL_INTERVAL
from app.services.debate.audit import AuditResult, DebateAuditService
from app.services.debate.repetition import (
    is_self_repetition,
    max_overlap_against,
    most_similar,
    pair_overlap,
)
from app.services.synthesis import _detect_convergence_combined
from app.services.debate.horizon import tag_statements_with_horizon
import app.services.debate.prompt_composer as prompt_composer
from app.services.debate.artifacts import (
    PositionMap, TensionMap, EngagementRecord, ConvergenceMap, AgentEvolution,
)
from app.services.debate.spine import DebateSpineBuilder
from app.services.debate.followup import FollowupComposer
from app.services.psyche.builder import bundle_or_legacy

logger = logging.getLogger(__name__)

_VALID_COMPLEXITY = {"L1", "L2", "L3"}
EARLY_TERMINATION_CONVERGENCE_THRESHOLD = 0.75
EARLY_TERMINATION_TIMEOUT_SECONDS = 120
EARLY_TERMINATION_SKIPPED_PHASES = ["r4_reflection", "r4_mapping", "r4_final"]
STREAM_STATUS_EVENT_TYPES = {"phase_evaluating", "artifact_start", "artifact_end"}
FOLLOWUP_TRIGGER_PHASES = {DebatePhase.ROUND2_CROSS, DebatePhase.R3_ACKNOWLEDGE}
# R4 final restates the agent's own reflection above this bigram overlap.
R4_FINAL_REFLECTION_OVERLAP = 0.6

_STANCE_OPENING_SIGNALS = (
    "我不同意",
    "不同意",
    "我不接受",
    "我反对",
    "我仍坚持",
    "我坚持",
    "我部分同意",
    "部分同意",
    "我部分修正",
    "部分修正",
    "我补充",
    "我要补充",
    "我换个角度",
    "换个角度",
    "我提出一个新角度",
    "我的看法是",
    "我的位置是",
)
_BARE_AGREEMENT_OPENINGS = ("我同意", "我认同", "你说得对")
_AGREEMENT_BOUNDARY_SIGNALS = ("但", "不过", "同时", "并且", "还要", "补充", "仍")


class DebateOrchestrator:
    """
    Orchestrates a full debate session for one session_id.
    Created once per session (on Round 1 start), reused for subsequent rounds.
    """

    def __init__(
        self,
        session_id: str,
        identity_cards: list[dict[str, Any]],
        profile: dict[str, Any],
    ):
        self._session_id = session_id
        self._identity_cards = identity_cards
        self._profile = profile

        raw_level = profile.get("debate_level", "L2")
        if raw_level not in _VALID_COMPLEXITY:
            logger.warning(
                "Invalid complexity '%s', falling back to L2", raw_level,
            )
            raw_level = "L2"
        self._complexity = raw_level

        # Create agents
        llm_config = build_llm_config()
        self._agents = create_all_agents(identity_cards, llm_config)
        self._agent_map: dict[str, AG2DebateAgent] = {
            a.get_agent_id(): a for a in self._agents
        }
        self._spine = DebateSpineBuilder().build(
            profile=profile,
            identity_cards=identity_cards,
        )
        self._psyche_bundle = bundle_or_legacy(profile)
        self._audit = DebateAuditService()
        self._preflight_audit = self._audit.audit_preflight(
            profile=self._profile,
            spine=self._spine,
            identity_cards=self._identity_cards,
        )
        self._blocked_reason = (
            self._preflight_audit.summary
            if self._preflight_audit.recommended_action == "block_start"
            else None
        )
        self._round_audits: dict[int, AuditResult] = {}
        self._latest_round_action = "none"
        self._phase_exchange_limits: dict[str, tuple[int, int]] = {}

        # State machine
        self._state = RoundStateMachine(self._complexity)
        self._state.set_agent_count(len(self._agents))

        # Memory
        self._memory = MemoryManager(profile)
        for card in identity_cards:
            self._memory.register_agent(card.get("agent_id", ""), card)

        # Consistency monitor (applied to R2 and R3)
        self._consistency = ConsistencyMonitor()

        # Blackboard-first compatibility internals
        self._blackboard = DebateBlackboard(
            session_id=session_id,
            complexity=self._complexity,
            dilemma=profile.get("core_dilemma", ""),
            phase=DebatePhase.ROUND1_OPENING,
            agents=build_agent_states(identity_cards),
        )
        self._router = ModelRouter(
            primary_model=config.LLM_MODEL,
            auxiliary_model=getattr(config, "LLM_AUX_MODEL", config.LLM_MODEL),
        )

        # Evaluator (LLM-based artifact extraction and termination)
        self._evaluator = RoundEvaluator(router=self._router)

        # Artifact state
        self._position_map: Optional[PositionMap] = None
        self._tension_map: Optional[TensionMap] = None
        self._engagement_record: Optional[EngagementRecord] = None
        self._convergence_map: Optional[ConvergenceMap] = None
        self._agent_evolutions: dict[str, AgentEvolution] = {}

        # Dilemma info (cached for prompts)
        self._dilemma = profile.get("core_dilemma", "用户困境")
        self._user_display_name = str(profile.get("user_display_name") or "").strip()[:40]
        value_conflicts = profile.get("value_conflicts") or []
        self._vc_text = (
            "; ".join(
                f"{c.get('value_a', '')} vs {c.get('value_b', '')}"
                for c in value_conflicts[:3]
            )
            or "价值冲突"
        )

        # Flat statement list (for backward compatibility with old API)
        self._all_statements: list[dict[str, Any]] = []

        # Pause flag
        self._paused = False
        self._pending_early_termination_offer: dict[str, Any] | None = None
        self._early_termination_future: asyncio.Future[str] | None = None
        self._r4_was_skipped = False
        self._followup_composer = FollowupComposer(router=self._router)
        self._pending_followup: dict[str, Any] | None = None
        self._followup_future: asyncio.Future[dict[str, Any]] | None = None
        self._last_followup_resolution: dict[str, Any] | None = None
        self._reanchor_pending: bool = False
        self._reanchor_user_answers: list[str] = []

        # Synthesis cache (cleared when new statements are added)
        self._synthesis_cache: dict[str, Any] | None = None

        logger.info(
            "Orchestrator created: session=%s, complexity=%s, agents=%d",
            session_id, self._complexity, len(self._agents),
        )
        self._sync_blackboard_from_legacy()
        self._sync_legacy_aliases()

    def _statement_to_record(self, statement: dict[str, Any]) -> StatementRecord:
        return StatementRecord(
            statement_id=statement["statement_id"],
            agent_id=statement["agent_id"],
            agent_name=statement["agent_name"],
            round_number=statement["round_number"],
            content=statement["content"],
            horizon=statement.get("horizon"),
            reply_to=statement.get("reply_to"),
            exchange_seq=statement.get("exchange_seq"),
            type=statement.get("type"),
            intervention_type=statement.get("intervention_type"),
        )

    def _sync_blackboard_from_legacy(self) -> None:
        self._blackboard.phase = self.current_phase
        self._blackboard.position_map = self._position_map
        self._blackboard.tension_map = self._tension_map
        self._blackboard.engagement_record = self._engagement_record
        self._blackboard.convergence_map = self._convergence_map
        self._blackboard.transcript = []
        self._blackboard.exchange_count_this_phase = 0
        self._blackboard.consecutive_stable = 0
        self._blackboard.evaluator_goal_met = False
        self._blackboard.phase_speaker_counts = {}

        for agent_id, state in self._blackboard.agents.items():
            state.statements = {}
            state.challenged_points = []
            mem = self._memory._agent_memories.get(agent_id)
            state.concessions = list(mem.concessions) if mem else []
            state.evolution = self._agent_evolutions.get(agent_id)

        for statement in self._all_statements:
            if statement.get("is_user_turn") or statement.get("type") == "followup_questions":
                continue
            self._blackboard.record_statement(
                self._statement_to_record(statement),
            )

        current_round = self.current_round
        phase_rows = [
            row for row in self._blackboard.transcript
            if row.round_number == current_round
        ]
        self._blackboard.exchange_count_this_phase = max(
            (row.exchange_seq or 0) for row in phase_rows
        ) if phase_rows else 0
        self._blackboard.phase_speaker_counts = {}
        for row in phase_rows:
            self._blackboard.phase_speaker_counts[row.agent_id] = (
                self._blackboard.phase_speaker_counts.get(row.agent_id, 0) + 1
            )

    def _sync_legacy_aliases(self) -> None:
        self._position_map = self._blackboard.position_map
        self._tension_map = self._blackboard.tension_map
        self._engagement_record = self._blackboard.engagement_record
        self._convergence_map = self._blackboard.convergence_map
        self._agent_evolutions = {
            agent_id: state.evolution
            for agent_id, state in self._blackboard.agents.items()
            if state.evolution is not None
        }
        blackboard_by_id = {
            s.get("statement_id"): s
            for s in self._blackboard.all_statements
            if s.get("statement_id")
        }
        if not self._all_statements:
            self._all_statements = self._blackboard.all_statements
            return

        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for statement in self._all_statements:
            statement_id = statement.get("statement_id")
            if statement_id:
                seen.add(statement_id)
            if statement.get("is_user_turn") or statement.get("is_user_reanchor") or statement_id not in blackboard_by_id:
                merged.append(statement)
            else:
                merged.append(blackboard_by_id[statement_id])

        for statement in self._blackboard.all_statements:
            statement_id = statement.get("statement_id")
            if not statement_id or statement_id not in seen:
                merged.append(statement)

        self._all_statements = merged

    # -- Properties --

    @property
    def current_phase(self) -> DebatePhase:
        return self._state.current_phase

    @property
    def current_round(self) -> int:
        return self._state.current_round

    @property
    def is_done(self) -> bool:
        return self._state.is_done()

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def complexity(self) -> str:
        return self._complexity

    def _user_address_prompt(self, *, require_opening_address: bool = False) -> str:
        if not self._user_display_name:
            return ""
        base = (
            f"用户希望被称呼为「{self._user_display_name}」。"
            "你在对用户说话时可以自然使用这个称呼，避免用“该用户/这位用户”等旁观说法。"
        )
        if not require_opening_address:
            return base
        return (
            f"{base}这是你第一次直接对用户开口，"
            f"第一句必须自然叫到「{self._user_display_name}」，再表达你的开场立场。"
        )

    def _user_statement_name(self) -> str:
        return self._user_display_name or "我"

    @staticmethod
    def _first_sentence(content: str) -> str:
        return re.split(r"[。！？!?；;\n]", content.strip(), maxsplit=1)[0].strip()

    def _phase_compliance_correction(
        self,
        content: str,
        phase: DebatePhase,
    ) -> str | None:
        first_sentence = self._first_sentence(content)
        if (
            phase == DebatePhase.ROUND1_OPENING
            and self._user_display_name
            and self._user_display_name not in first_sentence
        ):
            return (
                f"第一句必须自然叫到「{self._user_display_name}」，"
                "保留原观点，只调整开头的称呼方式。"
            )

        if phase not in (DebatePhase.ROUND2_CROSS, DebatePhase.ROUND3_DEEPEN):
            return None

        if first_sentence.startswith(_BARE_AGREEMENT_OPENINGS):
            if any(marker in first_sentence for marker in _AGREEMENT_BOUNDARY_SIGNALS):
                return None
            return (
                "如果第一句表示同意，必须在同一句说明你要补充什么"
                "或仍保留什么边界。"
            )

        if not first_sentence.startswith(_STANCE_OPENING_SIGNALS):
            return (
                "第一句先声明你是同意、不同意、坚持原判断、补充遗漏，"
                "还是换一个角度，再展开理由。"
            )

        return None

    # -- Pause / Resume --

    def pause(self) -> None:
        self._paused = True
        logger.info("Debate paused: session=%s", self._session_id)

    def resume(self) -> None:
        self._paused = False
        logger.info("Debate resumed: session=%s", self._session_id)

    async def _compute_post_r3_convergence(self) -> float | None:
        statements = self._get_debate_only_statements()
        if not statements:
            return None

        try:
            return round(await _detect_convergence_combined(statements), 3)
        except Exception:
            logger.warning(
                "Convergence scoring failed; falling back to engagement heuristic",
                exc_info=True,
            )

        record = self._engagement_record
        if record is None:
            return None

        shifts = len(record.position_shifts)
        unresolved = len(record.unresolved_disagreements)
        total = shifts + unresolved
        return round(shifts / total, 3) if total else None

    def get_pending_early_termination_offer(self) -> dict[str, Any] | None:
        if self._pending_early_termination_offer is None:
            return None
        return deepcopy(self._pending_early_termination_offer)

    def _build_early_termination_offer(self, score: float) -> dict[str, Any]:
        tension_items: list[dict[str, Any]] = []
        if self._engagement_record is not None:
            for item in self._engagement_record.tension_engagement[:3]:
                tension_items.append(
                    {
                        "id": item.tension_id,
                        "label": self._tension_description_for_id(str(item.tension_id)),
                        "resolution": item.depth,
                    }
                )

        divergence_map = (
            self._engagement_record.divergence_map
            if self._engagement_record is not None
            else None
        )

        return {
            "convergence_score": score,
            "top_tensions": tension_items,
            "divergence_map": divergence_map,
            "irreducible_acknowledgements": [],
            "estimated_remaining_minutes": 5,
            "decision_required": True,
        }

    async def prepare_early_termination_offer_if_needed(self) -> dict[str, Any] | None:
        if self._pending_early_termination_offer is not None:
            return self.get_pending_early_termination_offer()

        if self._complexity != "L3" or self._state.current_phase != DebatePhase.R4_REFLECTION:
            return None

        score = await self._compute_post_r3_convergence()
        if score is None or score < EARLY_TERMINATION_CONVERGENCE_THRESHOLD:
            self._pending_early_termination_offer = None
            return None

        self._pending_early_termination_offer = self._build_early_termination_offer(score)
        self._early_termination_future = asyncio.get_running_loop().create_future()
        self.pause()
        save_session_meta(
            self._session_id,
            {
                "early_termination_offered": True,
                "early_termination_decision": "pending",
                "decision_timeout": False,
            },
        )
        return self.get_pending_early_termination_offer()

    def submit_early_termination_decision(self, decision: str) -> None:
        if decision not in {"continue", "close"}:
            raise ValueError(f"Unsupported early-termination decision: {decision}")
        if (
            self._pending_early_termination_offer is None
            or self._early_termination_future is None
            or self._early_termination_future.done()
        ):
            raise RuntimeError("No early-termination offer is pending.")
        self._early_termination_future.set_result(decision)

    async def await_early_termination_resolution(self) -> dict[str, Any]:
        if self._pending_early_termination_offer is None or self._early_termination_future is None:
            raise RuntimeError("No early-termination offer is pending.")

        timeout = False
        future = self._early_termination_future
        try:
            decision = await asyncio.wait_for(
                asyncio.shield(future),
                timeout=EARLY_TERMINATION_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            timeout = True
            decision = "continue"
            if not future.done():
                future.cancel()

        skipped_phases: list[str] = []
        if decision == "close":
            self._r4_was_skipped = True
            skipped_phases = list(EARLY_TERMINATION_SKIPPED_PHASES)
            self._state.skip_to_synthesis()
        else:
            self._r4_was_skipped = False

        self.resume()
        self._pending_early_termination_offer = None
        self._early_termination_future = None
        save_session_meta(
            self._session_id,
            {
                "early_termination_offered": True,
                "early_termination_decision": decision,
                "decision_timeout": timeout,
            },
        )
        self._sync_blackboard_from_legacy()
        self._sync_legacy_aliases()
        return {
            "decision": decision,
            "timeout": timeout,
            "skipped_phases": skipped_phases,
        }

    # -- Follow-up gate (asks the user after R2 and R3.5) --

    def _recent_phase_statements(self, after_round: int) -> list[dict[str, Any]]:
        rows = [
            s for s in self._all_statements
            if s.get("round_number") == after_round
            and not s.get("is_user_turn")
            and s.get("type") != "followup_questions"
        ]
        return rows[-8:]

    def _prior_followup_questions(self) -> list[dict[str, Any]]:
        """Flatten every question asked in prior follow-up gates this debate.

        Source of truth for "already asked" (card 2-C / #8): an answered tension
        was necessarily asked first, so this one list covers answered, skipped,
        and unanswered alike. A later gate uses it to avoid re-selecting tensions.
        """
        prior: list[dict[str, Any]] = []
        for row in self._all_statements:
            if row.get("type") != "followup_questions":
                continue
            for q in row.get("questions", []) or []:
                prior.append({
                    "target_tension_id": q.get("target_tension_id"),
                    "text": q.get("text", ""),
                })
        return prior

    def followup_gate_possible(self, after_phase: DebatePhase) -> bool:
        """Cheap, side-effect-free check: could a follow-up gate open after this
        phase? Composing the question is a slow LLM call, so the stream bridge
        uses this to announce 'preparing' before the wait and keep the next-round
        button suppressed until the gate actually opens (or is skipped)."""
        if not config.DEBATE_FOLLOWUP_ENABLED:
            return False
        if self._pending_followup is not None:
            return True
        return after_phase in FOLLOWUP_TRIGGER_PHASES

    async def prepare_followup_gate_if_needed(
        self, after_phase: DebatePhase, after_round: int,
    ) -> dict[str, Any] | None:
        if not config.DEBATE_FOLLOWUP_ENABLED:
            return None
        if self._pending_followup is not None:
            return deepcopy(self._pending_followup)
        if after_phase not in FOLLOWUP_TRIGGER_PHASES:
            return None

        synthesizer = next(
            (a for a in self._agents if a.get_agent_id() == "synthesizer"), None,
        )
        synthesizer_card = synthesizer.get_identity_card() if synthesizer else {}
        display_name = synthesizer.get_display_name() if synthesizer else "你的整合自我"

        try:
            lead_in, questions = await self._followup_composer.compose(
                after_phase=after_phase,
                tension_map=self._tension_map,
                engagement_record=self._engagement_record,
                spine=self._spine,
                recent_statements=self._recent_phase_statements(after_round),
                synthesizer_card=synthesizer_card,
                prior_questions=self._prior_followup_questions(),
            )
        except Exception:
            logger.exception("Follow-up compose failed; skipping gate")
            return None

        if not questions:
            return None

        followup_id = str(uuid4())
        self._last_followup_resolution = None
        content = lead_in + "\n" + "\n".join(f"- {q.text}" for q in questions)
        row = {
            "statement_id": str(uuid4()),
            "followup_id": followup_id,
            "agent_id": "synthesizer",
            "agent_name": display_name,
            "round_number": after_round,
            "after_phase": after_phase.value,
            "type": "followup_questions",
            "content": content,
            "questions": [q.to_dict() for q in questions],
        }
        self._all_statements.append(row)
        save_debate_statements(self._session_id, self._all_statements)

        self._pending_followup = {
            "followup_id": followup_id,
            "after_phase": after_phase.value,
            "round": after_round,
            "lead_in": lead_in,
            "questions": [q.to_dict() for q in questions],
            "timeout_seconds": None,  # card 2-C: gate no longer times out; None tells the client "no countdown"
        }
        self._followup_future = asyncio.get_running_loop().create_future()
        self.pause()
        return deepcopy(self._pending_followup)

    def submit_followup_response(
        self, followup_id: str, responses: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if (
            self._pending_followup is None
            or self._followup_future is None
            or self._followup_future.done()
        ):
            raise RuntimeError("No follow-up gate is pending.")
        if followup_id != self._pending_followup["followup_id"]:
            raise ValueError("followup_id does not match the pending gate.")
        future = self._followup_future
        cleaned = [
            r for r in responses
            if isinstance(r, dict) and (r.get("answer") or "").strip()
        ]
        resolution = self._finalize_followup_resolution(cleaned)
        future.set_result(resolution)
        return {"ok": True, "accepted": len(cleaned), "followup_id": followup_id}

    def _apply_followup_responses(
        self, responses: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        questions = {q["question_id"]: q for q in self._pending_followup["questions"]}
        after_round = self._pending_followup.get("round", self.current_round)
        followup_id = self._pending_followup["followup_id"]
        applied: list[dict[str, Any]] = []
        answered_ids: list[str] = []
        reanchor_answers: list[str] = []
        for resp in responses:
            qid = resp.get("question_id")
            answer = (resp.get("answer") or "").strip()
            if not answer or qid not in questions:
                continue
            target = questions[qid].get("target_tension_id")
            status = FollowupComposer.classify_verdict(answer)
            turn = {
                "statement_id": str(uuid4()),
                "agent_id": "__user__",
                "agent_name": self._user_statement_name(),
                "round_number": after_round,
                "content": answer,
                "type": "user_turn",
                "is_user_turn": True,
                "followup_id": followup_id,
                "question_id": qid,
                "target_tension_id": target,
            }
            self._all_statements.append(turn)
            self._memory.record_user_turn(turn)
            if self._tension_map is not None and target is not None:
                self._tension_map.set_user_verdict(
                    target, status=status, text=answer, from_question_id=qid,
                )
            applied.append({"target_tension_id": target, "status": status, "from_question_id": qid})
            answered_ids.append(qid)
            reanchor_answers.append(answer)
        if answered_ids:
            self._reanchor_pending = True
            self._reanchor_user_answers = reanchor_answers
        save_debate_statements(self._session_id, self._all_statements)
        self._invalidate_synthesis_cache()
        return applied, answered_ids

    def _finalize_followup_resolution(
        self, responses: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if self._pending_followup is None or self._followup_future is None:
            raise RuntimeError("No follow-up gate is pending.")

        followup_id = self._pending_followup["followup_id"]
        applied: list[dict[str, Any]] = []
        answered_ids: list[str] = []
        if responses:
            applied, answered_ids = self._apply_followup_responses(responses)

        status = "recorded" if answered_ids else "skipped"
        self.resume()
        self._pending_followup = None
        self._followup_future = None
        self._sync_blackboard_from_legacy()
        self._sync_legacy_aliases()
        resolution = {
            "followup_id": followup_id,
            "status": status,
            "applied_verdicts": applied,
            "answered_question_ids": answered_ids,
        }
        self._last_followup_resolution = resolution
        return resolution

    async def await_followup_resolution(self) -> dict[str, Any]:
        if self._pending_followup is None or self._followup_future is None:
            if self._last_followup_resolution is not None:
                return self._last_followup_resolution
            raise RuntimeError("No follow-up gate is pending.")

        future = self._followup_future
        # Card 2-C (#8): block until the user answers or explicitly skips (empty
        # submit). Submit finalizes the gate and resolves this future with the
        # resolution dict. Shield protects the shared future when an outer waiter
        # is cancelled, so a later explicit submit/skip can still resolve it.
        return await asyncio.shield(future)

    # -- R1: Parallel opening --

    async def execute_round1_parallel(self) -> list[dict[str, Any]]:
        """
        Round 1: all agents generate opening statements in parallel.
        Each agent sees ONLY: identity card + dilemma + value conflicts.
        No agent sees other agents' R1 output.

        After statements: extracts PositionMap, tracks evolution per agent,
        and injects artifacts into memory for R2 context.
        """
        if self._paused:
            logger.warning("execute_round1 called while paused")
            return []

        if self._blocked_reason:
            logger.warning("execute_round1 blocked by preflight audit: %s", self._blocked_reason)
            return []

        statements = list(await asyncio.gather(*[
            self._generate_round1_statement(agent) for agent in self._agents
        ]))
        await self._finalize_round1(statements)
        return statements

    async def execute_round1_parallel_incremental(
        self,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Round 1 incremental mode for SSE.

        Agents still generate in parallel, but completed statements are yielded
        as soon as each agent finishes so slow providers don't leave the UI idle
        until the whole round completes.
        """
        if self._paused:
            logger.warning("execute_round1_incremental called while paused")
            return

        if self._blocked_reason:
            logger.warning(
                "execute_round1_incremental blocked by preflight audit: %s",
                self._blocked_reason,
            )
            return

        tasks = [
            asyncio.create_task(self._generate_round1_statement(agent))
            for agent in self._agents
        ]
        statements: list[dict[str, Any]] = []

        try:
            for task in asyncio.as_completed(tasks):
                statement = await task
                statements.append(statement)
                yield statement
        finally:
            pending = [task for task in tasks if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        if len(statements) == len(tasks):
            await self._finalize_round1(statements)


    async def execute_round1_live_incremental(
        self,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Round 1 sequential mode that lets user turns shape later openings."""
        if self._paused:
            logger.warning("execute_round1_live_incremental called while paused")
            return

        if self._blocked_reason:
            logger.warning(
                "execute_round1_live_incremental blocked by preflight audit: %s",
                self._blocked_reason,
            )
            return

        statements: list[dict[str, Any]] = []

        for agent in self._agents:
            statement = await self._generate_round1_statement(
                agent,
                include_memory_context=True,
            )
            self._memory.record_statement(
                statement["agent_id"],
                1,
                statement["content"],
                statement["agent_name"],
            )
            self._state.record_spoken(statement["agent_id"])
            statements.append(statement)
            self._all_statements.append(statement)
            save_debate_statements(self._session_id, self._all_statements)
            yield statement

        await self._finalize_round1_from_recorded_openings(statements)


    async def _generate_round1_statement(
        self,
        agent: AG2DebateAgent,
        *,
        include_memory_context: bool = False,
    ) -> dict[str, Any]:
        """Generate one R1 opening statement without mutating orchestrator state."""
        context = (
            self._memory.build_agent_context(agent.get_agent_id(), 1)
            if include_memory_context
            else ""
        )
        phase_instruction = prompt_composer.compose_round_prompt(
            agent.get_agent_id(),
            DebatePhase.ROUND1_OPENING,
            agent.get_identity_card(),
            spine=self._spine,
            active_question_id=self._primary_active_question_id(),
            seed=self.current_round,
        )
        user_address = self._user_address_prompt(require_opening_address=True)
        prompt = (
            f"请以{agent.get_display_name()}的身份回应这个用户困境：'{self._dilemma}'。"
            f"{user_address}\n"
            f"当前最突出的价值冲突：{self._vc_text}。\n"
            f"{phase_instruction}\n"
            "请用60-110字、最多3句给出你的开场立场：先一句亮出立场，再一句给出关键证据或代价。"
        )
        if context:
            prompt = f"{context}\n\n{prompt}"
        try:
            content = await agent.generate_statement(prompt)
        except Exception as e:
            logger.error("R1 generation failed for %s: %s", agent.get_agent_id(), e)
            content = ""
        if not content or is_llm_error(content):
            opening_address = f"{self._user_display_name}，" if self._user_display_name else ""
            content = (
                f"{opening_address}作为{agent.get_display_name()}，我会先把"
                f"'{self._dilemma}'里最容易被忽略的拉扯说清楚。"
            )
        content = content.strip()
        content = await self._realign_language_once(
            agent,
            content,
            prompt,
            phase=DebatePhase.ROUND1_OPENING,
            limit=300,
        )
        content = await self._enforce_statement_length(agent, content, target_chars=110, hard_cap=150)

        return {
            "statement_id": str(uuid4()),
            "agent_id": agent.get_agent_id(),
            "agent_name": agent.get_display_name(),
            "round_number": 1,
            "content": content,
            "reply_to": None,
        }

    async def _realign_language_once(
        self,
        agent: AG2DebateAgent,
        content: str,
        original_prompt: str,
        *,
        phase: DebatePhase | None = None,
        limit: int,
    ) -> str:
        """Retry once when the line sounds misaligned with spoken debate."""
        result = self._consistency.check_language_alignment(
            content,
            agent.get_display_name(),
            self._user_display_name,
        )
        phase_correction = (
            self._phase_compliance_correction(content, phase)
            if phase is not None
            else None
        )
        corrections = [
            item
            for item in [
                result.correction if not result.passed else None,
                phase_correction,
            ]
            if item
        ]
        if not corrections:
            return content

        retry_prompt = (
            f"{original_prompt}\n\n"
            f"原句：{content}\n"
            "请只改写这句话，不要新增观点，也不要改变原来的立场。\n"
            f"语言回正提示：{' '.join(corrections)}"
        )
        try:
            revised = await agent.generate_statement(retry_prompt)
        except Exception as e:
            logger.error(
                "Language realignment retry failed for %s: %s",
                agent.get_agent_id(), e,
            )
            return content

        if not revised or is_llm_error(revised):
            return content

        revised = revised.strip()[:limit]
        revised_result = self._consistency.check_language_alignment(
            revised,
            agent.get_display_name(),
            self._user_display_name,
        )
        revised_phase_correction = (
            self._phase_compliance_correction(revised, phase)
            if phase is not None
            else None
        )
        if revised_result.passed and revised_phase_correction is None:
            return revised
        return content

    def _primary_active_question_id(self) -> str | None:
        if not self._spine.active_questions:
            return None
        return self._spine.active_questions[0].question_id

    async def _finalize_round1(self, statements: list[dict[str, Any]]) -> None:
        """Persist R1 state, extract artifacts, and advance to the next phase."""
        for stmt in statements:
            self._memory.record_statement(
                stmt["agent_id"], 1, stmt["content"], stmt["agent_name"],
            )
            self._state.record_spoken(stmt["agent_id"])

        summary_parts = [f"[{s['agent_name']}] {s['content']}" for s in statements]
        self._memory.record_round_summary(1, "\n".join(summary_parts))

        self._all_statements.extend(statements)
        self._position_map = await self._evaluator.extract_position_map(statements)
        self._memory.set_current_artifact(self._position_map)
        await self._track_all_agent_evolutions()
        self._state.advance()
        self._sync_blackboard_from_legacy()
        self._sync_legacy_aliases()

    async def _finalize_round1_from_recorded_openings(
        self,
        statements: list[dict[str, Any]],
    ) -> None:
        """Finalize sequential R1 after agent openings were already recorded."""
        summary_parts = [f"[{s['agent_name']}] {s['content']}" for s in statements]
        self._memory.record_round_summary(1, "\n".join(summary_parts))
        save_debate_statements(self._session_id, self._all_statements)
        self._position_map = await self._evaluator.extract_position_map(statements)
        self._memory.set_current_artifact(self._position_map)
        await self._track_all_agent_evolutions()
        self._state.advance()
        self._sync_blackboard_from_legacy()
        self._sync_legacy_aliases()

    def _create_discussion_engine(
        self, phase: DebatePhase,
    ) -> DiscussionEngine:
        """Create a DiscussionEngine for the current discussion phase."""
        agent_ids = [a.get_agent_id() for a in self._agents]
        agent_names = {a.get_agent_id(): a.get_display_name() for a in self._agents}
        limits = self._compute_phase_exchange_limits(phase)

        if limits is not None:
            self._phase_exchange_limits[phase.value] = (
                limits.min_exchanges,
                limits.max_exchanges,
            )

        return DiscussionEngine(
            agent_ids=agent_ids,
            agent_names=agent_names,
            phase=phase,
            complexity=self._complexity,
            limits=limits,
        )

    def get_phase_exchange_limits(self, phase: DebatePhase) -> tuple[int, int] | None:
        """Return dynamic min/max exchange limits for a phase, computing them if needed."""
        cached = self._phase_exchange_limits.get(phase.value)
        if isinstance(cached, (tuple, list)) and len(cached) == 2:
            return int(cached[0]), int(cached[1])

        limits = self._compute_phase_exchange_limits(phase)
        if limits is None:
            return None

        pair = (limits.min_exchanges, limits.max_exchanges)
        self._phase_exchange_limits[phase.value] = pair
        return pair

    def _compute_phase_exchange_limits(self, phase: DebatePhase):
        if phase not in (DebatePhase.ROUND2_CROSS, DebatePhase.ROUND3_DEEPEN):
            return None

        tension_count = self._estimate_tension_count_for_phase(phase)
        r2_limits, r3_limits = compute_exchange_budget(self._complexity, tension_count)
        return r2_limits if phase == DebatePhase.ROUND2_CROSS else r3_limits

    def _estimate_tension_count_for_phase(self, phase: DebatePhase) -> int:
        if phase == DebatePhase.ROUND2_CROSS:
            position_count = len(self._position_map.positions) if self._position_map else 0
            return max(2, position_count - 1) if position_count else 2

        if phase == DebatePhase.ROUND3_DEEPEN:
            return len(self._tension_map.tensions) if self._tension_map else 0

        return 0

    def _current_active_question_id(self) -> Optional[str]:
        if not self._spine.active_questions:
            return None

        question_index = max(0, self._state.current_round - 2)
        question_index = min(question_index, len(self._spine.active_questions) - 1)
        return self._spine.active_questions[question_index].question_id

    def _current_active_question_text(self) -> str:
        question_id = self._current_active_question_id()
        if not question_id:
            return ""

        for question in self._spine.active_questions:
            if question.question_id == question_id:
                return question.prompt_text
        return ""

    def _current_corrective_action(self) -> str:
        if self._latest_round_action in {"tighten_next_prompt", "hold_termination"}:
            return "tighten_next_prompt"
        return "none"

    def _build_round_artifact_payload(self, phase: DebatePhase) -> dict[str, Any]:
        if phase == DebatePhase.ROUND2_CROSS and self._tension_map is not None:
            payload = self._tension_map.to_dict()
            payload["summary"] = {
                "current_dispute": self._dominant_tension_description(),
                "key_change": (
                    "\u53c8\u6253\u5f00\u4e86\u65b0\u5f20\u529b"
                    if self._tension_map.new_tensions_since_last > 0
                    else "\u65e0\u5b9e\u8d28\u63a8\u8fdb"
                ),
                "unresolved_issue": (
                    self._tension_map.unaddressed_angles[0]
                    if self._tension_map.unaddressed_angles
                    else self._current_active_question_text() or self._spine.core_contradiction
                ),
            }
            return payload

        if phase == DebatePhase.ROUND3_DEEPEN and self._engagement_record is not None:
            payload = self._engagement_record.to_dict()
            payload["summary"] = {
                "current_dispute": self._dominant_tension_description(),
                "key_change": (
                    self._engagement_record.highlight_moments[0]
                    if self._engagement_record.highlight_moments
                    else "\u65e0\u5b9e\u8d28\u63a8\u8fdb"
                ),
                "unresolved_issue": (
                    self._engagement_record.unresolved_disagreements[0]
                    if self._engagement_record.unresolved_disagreements
                    else self._current_active_question_text() or self._spine.core_contradiction
                ),
            }
            return payload

        return {
            "summary": {
                "current_dispute": self._dominant_tension_description(),
                "key_change": "\u65e0\u5b9e\u8d28\u63a8\u8fdb",
                "unresolved_issue": self._current_active_question_text() or self._spine.core_contradiction,
            }
        }

    def _dominant_tension_description(self) -> str:
        if self._tension_map is not None:
            for tension in self._tension_map.tensions:
                if tension.id == self._tension_map.dominant_tension_id:
                    return tension.description
            if self._tension_map.tensions:
                return self._tension_map.tensions[0].description
        return self._spine.core_contradiction

    def get_agent_evolutions(self) -> list[dict[str, Any]]:
        """Return the latest stance evolution snapshot for the debate UI."""
        return [evo.to_dict() for evo in self._agent_evolutions.values()]

    def _audit_round_state(
        self,
        phase: DebatePhase,
        round_statements: list[dict[str, Any]],
    ) -> AuditResult:
        audit_result = self._audit.audit_round(
            phase=phase.value,
            spine=self._spine,
            statements=round_statements[-max(EVAL_INTERVAL, 2):],
            artifact=self._build_round_artifact_payload(phase),
        )
        self._round_audits[self._state.current_round] = audit_result
        self._latest_round_action = audit_result.recommended_action
        return audit_result

    def _tag_statement_horizons(self) -> None:
        """3-C: tag every accumulated statement with a decision horizon in place."""
        tag_statements_with_horizon(self._all_statements, self._tension_map)

    async def execute_round_n(self) -> dict[str, Any]:
        """
        Execute the current round (R2/R3/R4) as a forum-style discussion.
        Returns {statements, current_round, done, all_statements}.

        When the current phase is an R4 sub-phase, delegates to
        execute_r4_protocol() instead of execute_discussion_incremental().
        """
        self._invalidate_synthesis_cache()
        self._tag_statement_horizons()
        phase = self._state.current_phase
        round_num = self._state.current_round

        if self._state.is_done():
            return {
                "statements": [],
                "current_round": round_num,
                "done": True,
                "all_statements": self._all_statements,
            }

        if self._paused:
            logger.warning("execute_round_n called while paused (round %d)", round_num)
            return {
                "statements": [],
                "current_round": round_num,
                "done": False,
                "all_statements": self._all_statements,
                "paused": True,
            }

        statements: list[dict[str, Any]] = []

        if phase == DebatePhase.R3_ACKNOWLEDGE:
            statements = await self._execute_r3_divergence_map()
        # R4 sub-phases use the 3-step protocol
        elif phase in (
            DebatePhase.R4_REFLECTION,
            DebatePhase.R4_MAPPING,
            DebatePhase.R4_FINAL,
        ):
            async for stmt in self.execute_r4_protocol():
                if not self._is_stream_status_event(stmt):
                    statements.append(stmt)
        else:
            async for stmt in self.execute_discussion_incremental():
                if not self._is_stream_status_event(stmt):
                    statements.append(stmt)
            # R3.5 belongs to the same user-visible round 3: chain the
            # divergence map so one next-round call covers all of round 3
            # (no extra button press between the deepen loop and the gate).
            if (
                phase == DebatePhase.ROUND3_DEEPEN
                and self._state.current_phase == DebatePhase.R3_ACKNOWLEDGE
                and not self._paused
            ):
                statements.extend(await self._execute_r3_divergence_map())

        self._tag_statement_horizons()
        done = self._state.is_done()
        logger.info("Round %d (%s) complete: %d statements, done=%s",
                     round_num, phase.value, len(statements), done)
        return {
            "statements": statements,
            "current_round": round_num,
            "done": done,
            "all_statements": self._all_statements,
        }

    @staticmethod
    def _is_stream_status_event(item: dict[str, Any]) -> bool:
        return item.get("type") in STREAM_STATUS_EVENT_TYPES

    async def execute_round_n_incremental(
        self,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Yields each statement as generated. Delegates to discussion engine.
        Used by StreamBridge for per-agent streaming.
        """
        phase = self._state.current_phase
        try:
            if phase == DebatePhase.R3_ACKNOWLEDGE:
                for stmt in await self._execute_r3_divergence_map():
                    if not self._is_stream_status_event(stmt):
                        self._tag_statement_horizons()
                        tag_statements_with_horizon([stmt], self._tension_map)
                    yield stmt
            elif phase in (
                DebatePhase.R4_REFLECTION,
                DebatePhase.R4_MAPPING,
                DebatePhase.R4_FINAL,
            ):
                async for stmt in self.execute_r4_protocol():
                    if not self._is_stream_status_event(stmt):
                        self._tag_statement_horizons()
                        tag_statements_with_horizon([stmt], self._tension_map)
                    yield stmt
            else:
                async for stmt in self.execute_discussion_incremental():
                    if not self._is_stream_status_event(stmt):
                        self._tag_statement_horizons()
                        tag_statements_with_horizon([stmt], self._tension_map)
                    yield stmt
        finally:
            self._tag_statement_horizons()

    async def execute_discussion_incremental(
        self,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Forum-style discussion: DiscussionEngine picks speakers dynamically.
        Yields each statement as it's generated.

        Integrates evaluator checkpoints every EVAL_INTERVAL exchanges:
        - R2: extract tension map, check stability
        - R3: evaluate engagement, check depth coverage

        After the phase loop, extracts final artifacts and tracks evolution.
        """
        phase = self._state.current_phase
        round_num = self._state.current_round

        if self._state.is_done() or self._paused:
            return

        engine = self._create_discussion_engine(phase)
        round_statements: list[dict[str, Any]] = []

        # Evaluator-driven termination state
        consecutive_stable = 0
        evaluator_goal_met = False
        hold_termination = False
        end_early = False
        self._latest_round_action = "none"

        while not engine.should_end_phase(
            evaluator_goal_met=evaluator_goal_met,
            hold_termination=hold_termination,
            end_early=end_early,
        ):
            if self._paused:
                logger.info("Discussion paused mid-phase at exchange %d", engine.exchange_count)
                break

            speaker_id, reply_to = engine.select_next_speaker()
            agent = self._agent_map.get(speaker_id)
            if not agent:
                logger.error("Speaker %s not found in agent_map", speaker_id)
                break

            stmt = await self._generate_discussion_statement(
                agent, phase, round_num, engine, reply_to,
            )

            # Keep the reply label only if the statement actually engages a
            # voice by name — user-directed statements carry no reply label.
            resolved_reply_to = engine.resolve_reply_to(
                stmt["content"], reply_to, speaker_id=speaker_id,
            )

            # Record in discussion engine
            engine.record_exchange(
                speaker_id, agent.get_display_name(),
                stmt["content"], resolved_reply_to,
            )

            # Add exchange metadata
            stmt["exchange_seq"] = engine.exchange_count
            stmt["reply_to"] = resolved_reply_to

            round_statements.append(stmt)
            self._all_statements.append(stmt)
            save_debate_statements(self._session_id, self._all_statements)
            yield stmt

            # --- Evaluator checkpoints ---
            if engine.exchange_count % EVAL_INTERVAL == 0 and engine.exchange_count > 0:
                yield {
                    "type": "phase_evaluating",
                    "round": round_num,
                    "phase": phase.value,
                    "stage": "checkpoint",
                }
                if phase == DebatePhase.ROUND2_CROSS:
                    evaluator_goal_met, consecutive_stable = await self._r2_evaluator_checkpoint(
                        round_statements, consecutive_stable,
                    )
                elif phase == DebatePhase.ROUND3_DEEPEN:
                    evaluator_goal_met = await self._r3_evaluator_checkpoint(
                        round_statements,
                    )

                audit_result = self._audit_round_state(phase, round_statements)
                hold_termination = hold_termination or (
                    audit_result.recommended_action == "hold_termination"
                )
                end_early = (
                    not hold_termination
                    and audit_result.recommended_action == "end_early"
                )

        # --- Post-loop: final artifact extraction + evolution tracking ---
        if phase == DebatePhase.ROUND2_CROSS:
            yield {
                "type": "artifact_start",
                "round": round_num,
                "phase": phase.value,
            }
            await self._r2_post_loop(round_statements, engine)
            yield {
                "type": "artifact_end",
                "round": round_num,
                "phase": phase.value,
            }
        elif phase == DebatePhase.ROUND3_DEEPEN:
            yield {
                "type": "artifact_start",
                "round": round_num,
                "phase": phase.value,
            }
            await self._r3_post_loop(round_statements, engine)
            yield {
                "type": "artifact_end",
                "round": round_num,
                "phase": phase.value,
            }

        # Round summary + advance
        self._finalize_round(round_statements, round_num)

    async def _r2_evaluator_checkpoint(
        self,
        round_statements: list[dict[str, Any]],
        consecutive_stable: int,
    ) -> tuple[bool, int]:
        """R2 evaluator checkpoint: extract tension map, check stability."""
        new_tension_map = await self._evaluator.extract_tension_map(
            round_statements,
            self._tension_map,
            spine=self._spine,
        )
        if new_tension_map.new_tensions_since_last == 0:
            consecutive_stable += 1
        else:
            consecutive_stable = 0
        self._tension_map = new_tension_map
        goal_met = self._evaluator.should_end_r2(
            self._tension_map, consecutive_stable,
        )
        return goal_met, consecutive_stable

    async def _r3_evaluator_checkpoint(
        self,
        round_statements: list[dict[str, Any]],
    ) -> bool:
        """R3 evaluator checkpoint: evaluate engagement depth."""
        self._engagement_record = await self._evaluator.evaluate_engagement(
            round_statements,
            self._tension_map or TensionMap(
                tensions=[], dominant_tension_id=None,
                unaddressed_angles=[], new_tensions_since_last=0,
                overall_progress="emerging",
            ),
        )
        top_tension_ids = [
            t.id for t in (self._tension_map.tensions[:3] if self._tension_map else [])
        ]
        return self._evaluator.should_end_r3(
            self._engagement_record,
            top_tension_ids=top_tension_ids,
            allow_convergence_handoff=(self._complexity == "L3"),
        )

    async def _r2_post_loop(
        self,
        round_statements: list[dict[str, Any]],
        engine: DiscussionEngine,
    ) -> None:
        """After R2 loop: extract final tension map + track evolution."""
        # Extract final tension map if not already done at an interval boundary
        if self._tension_map is None or engine.exchange_count % EVAL_INTERVAL != 0:
            self._tension_map = await self._evaluator.extract_tension_map(
                round_statements,
                spine=self._spine,
            )

        # Track evolution for each agent
        await self._track_all_agent_evolutions()

        # Set tension map as current artifact for R3
        self._memory.set_current_artifact(self._tension_map)

    async def _r3_post_loop(
        self,
        round_statements: list[dict[str, Any]],
        engine: DiscussionEngine,
    ) -> None:
        """After R3 loop: extract final engagement record + track evolution."""
        # Extract final engagement record if not already done
        if self._engagement_record is None or engine.exchange_count % EVAL_INTERVAL != 0:
            self._engagement_record = await self._evaluator.evaluate_engagement(
                round_statements,
                self._tension_map or TensionMap(
                    tensions=[], dominant_tension_id=None,
                    unaddressed_angles=[], new_tensions_since_last=0,
                    overall_progress="emerging",
                ),
            )

        # Track evolution for each agent
        await self._track_all_agent_evolutions()

        # Set engagement record as artifact for R4
        self._memory.set_current_artifact(self._engagement_record)

    async def _track_all_agent_evolutions(self) -> None:
        """Track position evolution for all agents using the evaluator."""
        for agent in self._agents:
            agent_id = agent.get_agent_id()
            mem = self._memory._agent_memories.get(agent_id)
            if not mem:
                continue
            agent_stmts_lists = dict(mem.my_statements)
            evo = await self._evaluator.track_evolution(agent_id, agent_stmts_lists)
            self._agent_evolutions[agent_id] = evo
            self._memory.set_agent_evolution(agent_id, evo)

    async def _execute_r3_divergence_map(self) -> list[dict[str, Any]]:
        """Execute R3.5 as one synthesizer-authored divergence map."""
        round_num = 3
        descriptions: list[str] = []
        if self._tension_map is not None:
            descriptions = [
                tension.description
                for tension in self._tension_map.tensions[:3]
                if tension.description
            ]
        if not descriptions:
            descriptions = [self._dominant_tension_description()]

        synthesizer = next(
            (agent for agent in self._agents if agent.get_agent_id() == "synthesizer"),
            None,
        )
        display_name = synthesizer.get_display_name() if synthesizer else "整合者"
        content = ""

        if synthesizer is not None:
            context = self._memory.build_agent_context("synthesizer", round_num)
            map_prompt = prompt_composer.compose_r3_divergence_map_prompt(
                synthesizer.get_identity_card(),
                tensions=descriptions,
            )
            prompt = f"{context}\n\n{map_prompt}"
            try:
                content = await synthesizer.generate_statement(prompt)
            except Exception as e:
                logger.error("R3 divergence map failed for synthesizer: %s", e)
                content = ""

        content = (content or "").strip()
        if not content or is_llm_error(content):
            content = self._fallback_divergence_map_text(descriptions)

        content = self._truncate_to_sentence(content)
        if not content:
            content = self._truncate_to_sentence(
                self._fallback_divergence_map_text(descriptions)
            )

        self._memory.record_statement("synthesizer", round_num, content, display_name)
        self._state.record_spoken("synthesizer")

        if self._engagement_record is None:
            self._engagement_record = EngagementRecord(
                tension_engagement=[],
                position_shifts=[],
                concessions_made=[],
                highlight_moments=[],
            )
        self._engagement_record.divergence_map = content
        self._engagement_record.r3_5_acknowledgements = []
        self._reanchor_pending = False
        self._reanchor_user_answers = []
        self._memory.set_current_artifact(self._engagement_record)

        statement = {
            "statement_id": str(uuid4()),
            "agent_id": "synthesizer",
            "agent_name": display_name,
            "round_number": round_num,
            "content": content,
            "reply_to": None,
            "type": "r3_divergence_map",
            "round": 3.5,
        }

        summary = f"[{display_name}] {content}"
        existing_summary = self._memory.get_round_summary(round_num)
        merged_summary = f"{existing_summary}\n{summary}" if existing_summary else summary
        self._memory.record_round_summary(round_num, merged_summary)
        self._all_statements.append(statement)
        self._state.advance()
        self._sync_blackboard_from_legacy()
        self._sync_legacy_aliases()
        return [statement]

    def _tension_description_for_id(self, tension_id: str) -> str:
        if self._tension_map is not None:
            for tension in self._tension_map.tensions:
                if str(tension.id) == str(tension_id):
                    return tension.description
        return self._dominant_tension_description()

    def _divergence_map_anchor(self) -> str | None:
        if self._engagement_record is None:
            return None
        return self._engagement_record.divergence_map

    @staticmethod
    def _fallback_reanchor_text(user_answers: list[str]) -> str:
        answer = next((a.strip() for a in user_answers if a and a.strip()), "")
        if not answer:
            return ""
        return (
            f"用户在追问中明确：{answer}。"
            "这把之前的分歧地图重锚到了用户此刻的裁决上。"
        )

    async def _execute_user_reanchor_if_needed(self) -> dict[str, Any] | None:
        """After the follow-up gate, fold the user's verdict into the divergence map.

        Returns a stream event dict (type ``divergence_reanchor``) when a re-anchor
        was produced, otherwise ``None``. Never raises into the R4 protocol.
        """
        if not self._reanchor_pending:
            return None
        self._reanchor_pending = False
        if self._engagement_record is None or not self._engagement_record.divergence_map:
            return None
        user_answers = list(self._reanchor_user_answers)
        if not any(a and a.strip() for a in user_answers):
            return None

        base_map = self._engagement_record.divergence_map
        synthesizer = next(
            (agent for agent in self._agents if agent.get_agent_id() == "synthesizer"),
            None,
        )
        display_name = synthesizer.get_display_name() if synthesizer else "整合者"

        patch = ""
        if synthesizer is not None:
            context = self._memory.build_agent_context("synthesizer", 4)
            reanchor_prompt = prompt_composer.compose_divergence_reanchor_prompt(
                synthesizer.get_identity_card(),
                base_map=base_map,
                user_answers=user_answers,
            )
            prompt = f"{context}\n\n{reanchor_prompt}"
            try:
                patch = await synthesizer.generate_statement(prompt)
            except Exception as e:
                logger.error("divergence reanchor failed for synthesizer: %s", e)
                patch = ""

        patch = (patch or "").strip()
        if not patch or is_llm_error(patch):
            patch = self._fallback_reanchor_text(user_answers)
        patch = self._truncate_to_sentence(patch)
        if not patch:
            patch = self._fallback_reanchor_text(user_answers)

        self._engagement_record.divergence_map_base = base_map
        if patch:
            self._engagement_record.divergence_map = f"{base_map}\n{patch}"
        self._memory.set_current_artifact(self._engagement_record)

        if not patch:
            return None

        self._memory.record_statement("synthesizer", 4, patch, display_name)
        statement = {
            "statement_id": str(uuid4()),
            "agent_id": "synthesizer",
            "agent_name": display_name,
            "round_number": 4,
            "content": patch,
            "reply_to": None,
            "type": "r3_divergence_map",
            "round": 3.6,
            "is_user_reanchor": True,
        }
        self._all_statements.append(statement)
        self._sync_blackboard_from_legacy()
        self._sync_legacy_aliases()

        event = dict(statement)
        event["type"] = "divergence_reanchor"
        return event

    @staticmethod
    def _fallback_divergence_map_text(tension_descriptions: list[str]) -> str:
        focus = (
            tension_descriptions[0].strip()
            if tension_descriptions and tension_descriptions[0].strip()
            else "核心矛盾"
        )
        return (
            f"这张分歧图先标出：{focus}。"
            "几边都抓住了真实代价，但还没走到一起；"
            "表面靠近的地方，底下仍有未被消化的担心和取舍。"
        )

    @staticmethod
    def _truncate_to_sentence(text: str, max_chars: int = 320) -> str:
        text = (text or "").strip()
        if len(text) <= max_chars:
            return text

        window = text[:max_chars]
        boundary = max(window.rfind(mark) for mark in "。！？!?")
        if boundary >= 0 and boundary + 1 >= max_chars // 2:
            return window[: boundary + 1]
        return window

    @staticmethod
    def _trim_to_sentence_boundary(text: str, *, max_chars: int) -> str:
        text = (text or "").strip()
        if len(text) <= max_chars:
            return text
        window = text[:max_chars]
        floor = max(12, max_chars // 2)
        sentence_end = max(window.rfind(mark) for mark in ("。", "！", "？", "!", "?", "…"))
        if sentence_end >= floor:
            return window[: sentence_end + 1].strip()
        clause_end = max(window.rfind(mark) for mark in ("，", "；", "：", "、", ",", ";", ":"))
        if clause_end >= floor:
            return window[:clause_end].rstrip(" ，；：、,;:") + "。"
        return window.rstrip(" ，；：、,;:") + "。"

    async def _compress_statement(self, agent: "AG2DebateAgent", content: str, *, target_chars: int) -> str:
        prompt = (
            f"把下面这段话压缩到{target_chars}字以内，"
            "只保留你的立场和最关键的那个代价或证据，不要新增观点，必须用完整的句子结尾。\n\n"
            f"原话：{content}"
        )
        try:
            return await agent.generate_statement(prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("statement compression failed for %s: %s", agent.get_agent_id(), exc)
            return content

    async def _enforce_statement_length(
        self,
        agent: "AG2DebateAgent",
        content: str,
        *,
        target_chars: int,
        hard_cap: int,
    ) -> str:
        text = (content or "").strip()
        if len(text) <= hard_cap:
            return text
        compressed = (await self._compress_statement(agent, text, target_chars=target_chars)).strip()
        if compressed and not is_llm_error(compressed) and len(compressed) <= hard_cap:
            return compressed
        base = compressed if (compressed and not is_llm_error(compressed)) else text
        return self._trim_to_sentence_boundary(base, max_chars=hard_cap)

    # -- R4: 3-step convergence protocol --

    async def execute_r4_protocol(self) -> AsyncGenerator[dict[str, Any], None]:
        """Execute R4 as a 3-step convergence protocol."""
        phase = self._state.current_phase

        if phase == DebatePhase.R4_REFLECTION:
            reanchor_event = await self._execute_user_reanchor_if_needed()
            if reanchor_event is not None:
                yield reanchor_event
            reflection_stmts = await self._execute_r4_reflection()
            for stmt in reflection_stmts:
                stmt["type"] = "r4_reflection"
                yield stmt

            summary_parts = [f"[{s['agent_name']}] {s['content']}" for s in reflection_stmts]
            self._memory.record_round_summary(4, "\n".join(summary_parts))
            self._all_statements.extend(reflection_stmts)

            self._state.advance()
            self._convergence_map = await self._evaluator.extract_convergence_map(
                reflection_stmts,
                self._all_statements,
                reanchor_landing=self._reanchor_landing_text(),
            )
            self._memory.set_current_artifact(self._convergence_map)
            yield {
                "type": "r4_mapping",
                "convergence_map": self._convergence_map.to_dict(),
            }

            self._state.advance()
            final_stmts = await self._execute_r4_final()
            for stmt in final_stmts:
                if self._convergence_map is not None:
                    self._convergence_map.agent_final_positions[stmt["agent_id"]] = stmt["content"]
                stmt["type"] = "r4_final"
                yield stmt

            final_summary = [f"[{s['agent_name']}] {s['content']}" for s in final_stmts]
            self._memory.record_round_summary(4, "\n".join(final_summary))
            self._all_statements.extend(final_stmts)
            self._state.advance()
            self._sync_blackboard_from_legacy()
            self._sync_legacy_aliases()
            return

        if phase == DebatePhase.R4_MAPPING:
            self._convergence_map = await self._evaluator.extract_convergence_map(
                [],
                self._all_statements,
                reanchor_landing=self._reanchor_landing_text(),
            )
            self._memory.set_current_artifact(self._convergence_map)
            yield {
                "type": "r4_mapping",
                "convergence_map": self._convergence_map.to_dict(),
            }
            self._state.advance()

            final_stmts = await self._execute_r4_final()
            for stmt in final_stmts:
                if self._convergence_map is not None:
                    self._convergence_map.agent_final_positions[stmt["agent_id"]] = stmt["content"]
                stmt["type"] = "r4_final"
                yield stmt
            self._all_statements.extend(final_stmts)
            self._state.advance()
            self._sync_blackboard_from_legacy()
            self._sync_legacy_aliases()
            return

        if phase == DebatePhase.R4_FINAL:
            final_stmts = await self._execute_r4_final()
            for stmt in final_stmts:
                if self._convergence_map is not None:
                    self._convergence_map.agent_final_positions[stmt["agent_id"]] = stmt["content"]
                stmt["type"] = "r4_final"
                yield stmt
            self._all_statements.extend(final_stmts)
            self._state.advance()
            self._sync_blackboard_from_legacy()
            self._sync_legacy_aliases()

    async def _execute_r4_reflection(self) -> list[dict[str, Any]]:
        """R4 Step 1: parallel reflection, similar to R1."""
        async def _gen_reflection(agent: AG2DebateAgent) -> dict[str, Any]:
            agent_id = agent.get_agent_id()
            context = self._memory.build_agent_context(agent_id, 4)
            acknowledgement_anchor = (
                None if agent_id == "synthesizer" else self._divergence_map_anchor()
            )
            reflection_prompt = prompt_composer.compose_r4_reflection_prompt(
                agent_id,
                agent.get_identity_card(),
                acknowledgement_anchor,
            )
            evo = self._agent_evolutions.get(agent_id)
            evo_text = f"\n{evo.to_prompt_text()}\n" if evo else ""
            prompt = (
                f"{context}\n\n"
                f"用户困境：{self._dilemma}。{evo_text}\n"
                f"{self._user_address_prompt()}\n"
                f"{reflection_prompt}\n"
                "请用60-110字、最多3句诚实反思你现在的变化。"
            )

            try:
                content = await agent.generate_statement(prompt)
            except Exception as e:
                logger.error("R4 reflection failed for %s: %s", agent_id, e)
                content = ""
            if not content or is_llm_error(content):
                content = (
                    f"作为{agent.get_display_name()}，经过这轮交锋，"
                    f"我现在会更细地看待'{self._dilemma}'里的拉扯。"
                )
            content = content.strip()
            content = await self._realign_language_once(
                agent,
                content,
                prompt,
                limit=500,
            )
            content = await self._enforce_statement_length(agent, content, target_chars=110, hard_cap=150)

            self._memory.record_statement(agent_id, 4, content, agent.get_display_name())
            self._state.record_spoken(agent_id)
            return {
                "statement_id": str(uuid4()),
                "agent_id": agent_id,
                "agent_name": agent.get_display_name(),
                "round_number": 4,
                "content": content,
                "reply_to": None,
            }

        tasks = [_gen_reflection(agent) for agent in self._agents]
        return list(await asyncio.gather(*tasks))

    @staticmethod
    def _r4_final_length_suffix(agent_id: str) -> str:
        if agent_id == "synthesizer":
            return "请用50-75字呈现全场辩论的整体状态与可收束的方向。"
        return "请用60-100字、最多3句给出你的最终立场，并提出一个可执行的具体动作。"

    def _reanchor_landing_text(self) -> str | None:
        """Return the 3-A re-anchor patch text (is_user_reanchor statement), if any.

        3-A appends at most one ``is_user_reanchor`` statement per session, so the
        first match is the authoritative landing (first-wins is safe under that
        invariant; revisit if 3-A ever allows multiple re-anchor rounds).
        """
        for statement in self._all_statements:
            if statement.get("is_user_reanchor"):
                content = (statement.get("content") or "").strip()
                return content or None
        return None

    def _r4_final_reanchor_args(self, agent_id: str) -> tuple[bool, str | None]:
        """Decide (reanchored, landing) for the R4-final synthesizer closing turn."""
        if agent_id != "synthesizer":
            return (False, None)
        record = self._engagement_record
        reanchored = record is not None and getattr(record, "divergence_map_base", None) is not None
        return (reanchored, self._reanchor_landing_text())

    def _agent_r4_reflection_text(self, agent_id: str) -> str | None:
        """Return the agent's own R4 reflection content, if it was recorded."""
        for statement in self._all_statements:
            if (
                statement.get("type") == "r4_reflection"
                and statement.get("agent_id") == agent_id
            ):
                content = (statement.get("content") or "").strip()
                return content or None
        return None

    async def _dedup_r4_final_against_reflection(
        self,
        agent: AG2DebateAgent,
        content: str,
        original_prompt: str,
        *,
        agent_id: str,
        reflection: str | None,
    ) -> str:
        """Regenerate the final once when it merely restates the reflection.

        The reflection already aired what moved the agent; the final must add a
        concrete landing, not paraphrase. Keeps whichever wording is least like
        the reflection so a no-better retry never regresses.
        """
        if not reflection:
            return content
        if pair_overlap(content, reflection) < R4_FINAL_REFLECTION_OVERLAP:
            return content

        retry_prompt = (
            f"{original_prompt}\n\n"
            f"你在反思环节已经说过：「{reflection}」。"
            "最终立场不要复述它——把反思当背景，这一句只交付一个前面没出现过的"
            "具体落点：今天就能动手、一周内能看到反馈的下一步。"
        )
        try:
            revised = await agent.generate_statement(retry_prompt)
        except Exception as e:
            logger.error("R4 final dedup regeneration failed for %s: %s", agent_id, e)
            return content
        if not revised or is_llm_error(revised):
            return content
        revised = revised.strip()
        if pair_overlap(revised, reflection) < pair_overlap(content, reflection):
            return revised
        return content

    async def _execute_r4_final(self) -> list[dict[str, Any]]:
        """R4 Step 3: parallel final positioning."""
        async def _gen_final(agent: AG2DebateAgent) -> dict[str, Any]:
            agent_id = agent.get_agent_id()
            context = self._memory.build_agent_context(agent_id, 4)
            reanchored, reanchor_landing = self._r4_final_reanchor_args(agent_id)
            reflection = self._agent_r4_reflection_text(agent_id)
            final_prompt = prompt_composer.compose_r4_final_prompt(
                agent_id,
                agent.get_identity_card(),
                reanchored=reanchored,
                reanchor_landing=reanchor_landing,
            )
            reflection_anchor = (
                f"你反思环节已经说过：「{reflection}」。"
                "最终这句不要复述它，只补上一个它还没给出的具体落点。\n"
                if reflection
                else ""
            )
            prompt = (
                f"{context}\n\n"
                f"用户困境：{self._dilemma}。\n"
                f"{self._user_address_prompt()}\n"
                f"{reflection_anchor}"
                f"{final_prompt}\n"
                f"{self._r4_final_length_suffix(agent_id)}"
            )

            try:
                content = await agent.generate_statement(prompt)
            except Exception as e:
                logger.error("R4 final failed for %s: %s", agent_id, e)
                content = ""
            if not content or is_llm_error(content):
                content = (
                    f"作为{agent.get_display_name()}，我的最终立场仍会把"
                    f"'{self._dilemma}'里的核心价值冲突摆在桌面上。"
                )
            content = content.strip()
            content = await self._dedup_r4_final_against_reflection(
                agent, content, prompt, agent_id=agent_id, reflection=reflection,
            )
            content = await self._realign_language_once(
                agent,
                content,
                prompt,
                limit=300,
            )
            _is_synth = agent_id == "synthesizer"
            content = await self._enforce_statement_length(
                agent, content,
                target_chars=(75 if _is_synth else 100),
                hard_cap=(100 if _is_synth else 140),
            )

            self._memory.record_statement(agent_id, 4, content, agent.get_display_name())
            self._state.record_spoken(agent_id)
            return {
                "statement_id": str(uuid4()),
                "agent_id": agent_id,
                "agent_name": agent.get_display_name(),
                "round_number": 4,
                "content": content,
                "reply_to": None,
            }

        tasks = [_gen_final(agent) for agent in self._agents]
        return list(await asyncio.gather(*tasks))

    # -- Statement generation --

    async def _generate_discussion_statement(
        self,
        agent: AG2DebateAgent,
        phase: DebatePhase,
        round_num: int,
        engine: DiscussionEngine,
        reply_to: Optional[str] = None,
    ) -> dict[str, Any]:
        """Generate a statement for the discussion, with context from recent exchanges."""
        agent_id = agent.get_agent_id()
        context = self._memory.build_agent_context(agent_id, round_num)
        prompt = self._build_quality_controlled_discussion_prompt(
            agent,
            context,
            phase,
            round_num,
            engine,
            reply_to,
        )

        try:
            content = await agent.generate_statement(prompt)
        except Exception as e:
            logger.error(
                "Discussion agent %s generation failed (round %d): %s",
                agent_id,
                round_num,
                e,
            )
            content = ""
        if not content or is_llm_error(content):
            content = (
                f"作为{agent.get_display_name()}，我得把"
                f"'{self._dilemma}'里几股互相拉扯的顾虑一起算进去。"
            )
        content = content.strip()

        if phase in (DebatePhase.ROUND2_CROSS, DebatePhase.ROUND3_DEEPEN):
            content = await self._enforce_consistency_async(
                agent,
                content,
                round_num,
                prompt,
                phase=phase,
                check_identity=engine.exchange_count > 0,
                engine=engine,
            )
            content = await self._regenerate_if_self_repeats_once(
                agent,
                content,
                prompt,
                agent_id=agent_id,
                phase=phase,
            )

        content = await self._enforce_statement_length(
            agent, content,
            target_chars=(75 if phase == DebatePhase.ROUND4_CONVERGE else 110),
            hard_cap=(100 if phase == DebatePhase.ROUND4_CONVERGE else 150),
        )

        if phase in (DebatePhase.ROUND3_DEEPEN, DebatePhase.ROUND4_CONVERGE) and detect_concession(content):
            self._memory.record_concession(agent_id, content)

        self._memory.record_statement(agent_id, round_num, content, agent.get_display_name())
        self._state.record_spoken(agent_id)
        return {
            "statement_id": str(uuid4()),
            "agent_id": agent_id,
            "agent_name": agent.get_display_name(),
            "round_number": round_num,
            "content": content,
            "reply_to": reply_to,
        }

    def _finalize_round(
        self,
        statements: list[dict[str, Any]],
        round_num: int,
    ) -> None:
        """Record summary, update state list, and advance phase."""
        summary_parts = [f"[{s['agent_name']}] {s['content']}" for s in statements]
        self._memory.record_round_summary(round_num, "\n".join(summary_parts))
        existing_ids = {
            s.get("statement_id")
            for s in self._all_statements
            if s.get("statement_id")
        }
        for statement in statements:
            statement_id = statement.get("statement_id")
            if not statement_id or statement_id not in existing_ids:
                self._all_statements.append(statement)
                if statement_id:
                    existing_ids.add(statement_id)
        save_debate_statements(self._session_id, self._all_statements)
        self._state.advance()
        self._sync_blackboard_from_legacy()
        self._sync_legacy_aliases()

    # -- Consistency enforcement --

    async def _regenerate_if_self_repeats_once(
        self,
        agent: AG2DebateAgent,
        content: str,
        original_prompt: str,
        *,
        agent_id: str,
        phase: DebatePhase | None = None,
    ) -> str:
        """Regenerate once when content near-duplicates the agent's own prior lines."""
        prior = self._memory.get_agent_statements_flat(agent_id)
        if not is_self_repetition(content, prior):
            return content

        closest, _ = most_similar(content, prior)
        retry_prompt = (
            f"{original_prompt}\n\n"
            f"你前面已经说过：「{closest}」。"
            "别重复这个点，用你自己的视角带出一个前面没出现过的新内容——"
            "新的边界、新的因果，或一个还没被提到的对象，"
            "把立场往前顶，不要换句式重说同一件事。"
        )
        try:
            revised = await agent.generate_statement(retry_prompt)
        except Exception as e:
            logger.error(
                "Self-repetition regeneration failed for %s: %s", agent_id, e,
            )
            return content
        if not revised or is_llm_error(revised):
            return content
        revised = revised.strip()
        if phase is not None and self._phase_compliance_correction(revised, phase):
            return content
        if max_overlap_against(revised, prior) < max_overlap_against(content, prior):
            return revised
        return content

    async def _enforce_consistency_async(
        self,
        agent: AG2DebateAgent,
        content: str,
        round_num: int,
        original_prompt: str,
        *,
        phase: DebatePhase,
        check_identity: bool = True,
        engine: DiscussionEngine | None = None,
    ) -> str:
        """LLM-based consistency check. Retries once with correction if failed."""
        agent_id = agent.get_agent_id()
        corrections: list[str] = []
        phase_correction = self._phase_compliance_correction(content, phase)
        if phase_correction:
            corrections.append(phase_correction)

        if check_identity:
            mem = self._memory._agent_memories.get(agent_id)
            recent_flat: list[str] = []
            if mem:
                for stmts in mem.my_statements.values():
                    recent_flat.extend(stmts[-2:])

            result = await self._consistency.evaluate_async(
                agent_id,
                content,
                agent.get_identity_card(),
                recent_flat,
            )
            if not result.passed and result.correction:
                corrections.append(result.correction)

            if engine is not None:
                cad_correction = self._cross_agent_correction(engine, agent_id, content)
                if cad_correction:
                    corrections.append(cad_correction)

        if not corrections:
            return content

        correction_prompt = f"{original_prompt}\n\n回正提示：{' '.join(corrections)}"
        try:
            content = await agent.generate_statement(correction_prompt)
        except Exception as e:
            logger.error("Consistency retry failed for %s: %s", agent_id, e)
            content = ""
        if not content:
            content = f"作为{agent.get_display_name()}，我需要把自己的立场重新说准一点。"
        content = content.strip()[:500]

        return content

    def _cross_agent_correction(
        self, engine: DiscussionEngine, agent_id: str, content: str,
    ) -> str | None:
        """CAD: flag homogenization against same-phase peers (R2/R3 only).

        Pulls the most recent peer statements from the discussion engine
        (excluding this agent's own lines) and asks the ConsistencyMonitor
        whether *content* is too similar to them.
        """
        peer_texts = [
            exchange.content
            for exchange in engine.get_recent_exchanges(4)
            if exchange.agent_id != agent_id and exchange.content.strip()
        ]
        return self._consistency.build_cross_agent_correction(content, peer_texts)

    def _build_self_repetition_guard_block(self, agent_id: str) -> str:
        """List an agent's own prior lines so it does not replay them (R2/R3 only)."""
        prior = self._memory.get_agent_statements_flat(agent_id)
        lines = [f"- {stmt.strip()[:40]}" for stmt in prior if stmt.strip()]
        if not lines:
            return ""
        return (
            "【你已经说过这些点，别再重复，这一轮必须给出前面没说过的新内容】\n"
            + "\n".join(lines)
        )

    def _build_user_stance_guard_block(self) -> str:
        """4-D C4: honor the user's in-debate stance, cap to the 2 most recent."""
        shared = self._memory.get_shared_memory()
        turns = getattr(shared, "user_turns", None) or []
        texts = [
            str(turn.get("content", "")).strip()
            for turn in turns
            if str(turn.get("content", "")).strip()
        ]
        recent = list(reversed(texts[-2:]))
        return prompt_composer.build_user_stance_guard_block(recent)

    def _build_quality_controlled_discussion_prompt(
        self,
        agent: AG2DebateAgent,
        context: str,
        phase: DebatePhase,
        round_num: int,
        engine: DiscussionEngine,
        reply_to: Optional[str] = None,
    ) -> str:
        phase_instruction = prompt_composer.compose_round_prompt(
            agent.get_agent_id(),
            phase,
            agent.get_identity_card(),
            spine=self._spine,
            active_question_id=self._current_active_question_id(),
            corrective_action=self._current_corrective_action(),
            seed=(round_num * 100) + engine.exchange_count,
        )

        recent = engine.get_recent_exchanges(5)
        exchange_lines = ""
        if recent:
            parts: list[str] = []
            for ex in recent:
                reply_name = (
                    self._agent_map[ex.reply_to].get_display_name()
                    if ex.reply_to and ex.reply_to in self._agent_map
                    else ""
                )
                prefix = f"（回应{reply_name}）" if reply_name else ""
                parts.append(f"[{ex.agent_name}] {prefix}{ex.content}")
            exchange_lines = "\n[最近几轮交锋]\n" + "\n".join(parts)

        reply_instruction = ""
        if reply_to and reply_to in self._agent_map:
            target_name = self._agent_map[reply_to].get_display_name()
            bare_name = (
                target_name[2:]
                if target_name.startswith("你的") and len(target_name) > 2
                else target_name
            )
            reply_instruction = (
                f"\n你正在回应{target_name}。请在话里直接点出“{bare_name}”这个名字，"
                "并正面咬住那条论点里最站不住脚的一点，用你自己的视角回击。"
                "如果这句话其实是说给用户本人听的，就不要点任何声音的名字。"
            )

        audit_guidance_parts: list[str] = []
        active_question = self._current_active_question_text()
        if active_question:
            audit_guidance_parts.append(
                f"这一轮必须继续咬住当前问题：{active_question}"
                "（用你自己的话推进它，不要照抄这句话的措辞）"
            )
        if self._latest_round_action in {"tighten_next_prompt", "hold_termination"}:
            audit_guidance_parts.append(
                "上一轮开始打转了，这次必须给出前面没出现过的新内容——"
                "新的事实、新的边界，或新的因果链。"
            )
        audit_guidance = "\n".join(audit_guidance_parts)
        self_repetition_guard = (
            self._build_self_repetition_guard_block(agent.get_agent_id())
            if phase in (DebatePhase.ROUND2_CROSS, DebatePhase.ROUND3_DEEPEN)
            else ""
        )
        user_stance_guard = (
            self._build_user_stance_guard_block()
            if phase in (DebatePhase.ROUND2_CROSS, DebatePhase.ROUND3_DEEPEN)
            else ""
        )

        word_limit = (
            "50-75字"
            if phase == DebatePhase.ROUND4_CONVERGE
            else "60-110字"
        )

        return (
            f"{context}\n\n"
            f"用户困境：{self._dilemma}。价值冲突：{self._vc_text}。\n"
            f"{self._user_address_prompt()}\n"
            f"{exchange_lines}\n"
            f"{reply_instruction}\n"
            f"{audit_guidance}\n"
            f"{self_repetition_guard}\n"
            f"{user_stance_guard}\n"
            f"{phase_instruction}\n"
            f"请用{word_limit}、最多3句自然地说出你的观点，像辩论现场说话。"
        )

    # -- Interventions --

    async def handle_inject(
        self,
        user_content: str,
        target_agent_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Handle user inject intervention."""
        self._invalidate_synthesis_cache()
        self._memory.record_user_intervention({
            "type": "inject",
            "content": user_content,
            "target_agent_id": target_agent_id,
        })

        targets = (
            [a for a in self._agents if a.get_agent_id() == target_agent_id]
            if target_agent_id
            else self._agents
        )
        if not targets:
            targets = self._agents

        round_num = self._state.current_round
        responses: list[dict[str, Any]] = []
        for agent in targets:
            context = self._memory.build_agent_context(agent.get_agent_id(), round_num)
            prompt = (
                f"{context}\n\n"
                f"用户在辩论中注入了新信息：'{user_content}'。\n"
                "请保持你的角色立场，用30-50字回应。"
                f"用户困境：{self._dilemma}。"
            )
            try:
                content = await agent.generate_statement(prompt)
            except Exception as e:
                logger.error("Inject response failed for %s: %s", agent.get_agent_id(), e)
                content = ""
            if not content or is_llm_error(content):
                content = f"作为{agent.get_display_name()}，这个补充信息会影响我刚才的判断。"
            content = content.strip()[:200]

            self._memory.record_statement(
                agent.get_agent_id(),
                round_num,
                content,
                agent.get_display_name(),
            )
            stmt = {
                "statement_id": str(uuid4()),
                "agent_id": agent.get_agent_id(),
                "agent_name": agent.get_display_name(),
                "round_number": round_num,
                "content": content,
                "is_intervention_response": True,
                "intervention_type": "inject",
            }
            responses.append(stmt)

        self._all_statements.extend(responses)
        return responses

    # -- Synthesis --

    def _invalidate_synthesis_cache(self) -> None:
        """Clear cached synthesis so next call regenerates."""
        self._synthesis_cache = None

    def generate_synthesis(self) -> dict[str, Any]:
        """Sync synthesis (heuristic). Filters out intervention responses."""
        from app.services.synthesis import generate_synthesis as _gen_synthesis

        debate_statements = self._get_debate_only_statements()
        profile = self._memory.get_shared_memory().conflict_profile
        return _gen_synthesis(debate_statements, profile)

    async def generate_synthesis_async(self) -> dict[str, Any]:
        """Async enhanced synthesis with tensions, IFS intents, and consensus areas.
        Falls back to LLM-only, then heuristic if unavailable.
        Results are cached 閳?subsequent calls return instantly."""
        if self._synthesis_cache is not None:
            return self._synthesis_cache
        from app.services.synthesis import generate_synthesis_enhanced

        debate_statements = self._get_debate_only_statements()
        profile = self._memory.get_shared_memory().conflict_profile
        debate_artifacts = self._build_debate_artifacts()
        result = await generate_synthesis_enhanced(
            debate_statements, profile, debate_artifacts=debate_artifacts
        )
        self._synthesis_cache = result
        return result

    async def generate_synthesis_streaming(self) -> AsyncGenerator[dict[str, Any], None]:
        """Stream synthesis generation with stage-by-stage progress events.
        Returns cached result instantly if available."""
        if self._synthesis_cache is not None:
            yield {"event": "synthesis_cached", "data": self._synthesis_cache}
            return
        from app.services.synthesis import generate_synthesis_enhanced_streaming

        debate_statements = self._get_debate_only_statements()
        profile = self._memory.get_shared_memory().conflict_profile
        debate_artifacts = self._build_debate_artifacts()
        result = None
        async for event in generate_synthesis_enhanced_streaming(
            debate_statements, profile, debate_artifacts=debate_artifacts
        ):
            if event.get("event") == "synthesis_complete":
                result = event.get("data")
            yield event
        if result:
            self._synthesis_cache = result

    def _get_debate_only_statements(self) -> list[dict[str, Any]]:
        """Filter out intervention responses 閳?only official round statements."""
        return [
            s for s in self._all_statements
            if not s.get("is_intervention_response")
        ]

    def _build_debate_artifacts(self) -> dict[str, Any]:
        """Collect structured debate artifacts for synthesis."""
        artifacts: dict[str, Any] = {}
        artifacts["agent_evolutions"] = [
            evo.to_dict() for evo in self._agent_evolutions.values()
        ]
        artifacts["r4_present"] = self._convergence_map is not None and not self._r4_was_skipped
        if self._tension_map is not None:
            artifacts["tension_map"] = self._tension_map.to_dict()
        if self._convergence_map is not None:
            artifacts["convergence_map"] = self._convergence_map.to_dict()
        if self._engagement_record is not None:
            artifacts["engagement_record"] = self._engagement_record.to_dict()
        artifacts["dilemma_text"] = self._profile.get("core_dilemma") or ""
        artifacts["psyche_bundle"] = self._psyche_bundle.to_dict()
        return artifacts

    # -- Resonance --

    def record_resonance(self, agent_id: str, reason: str) -> bool:
        """Record which agent's voice resonated with the user."""
        if agent_id not in self._agent_map:
            return False
        self._memory.record_user_intervention({
            "type": "resonance",
            "target_agent_id": agent_id,
            "content": reason,
        })
        logger.info("Resonance recorded: agent=%s", agent_id)
        return True

    # -- State queries --

    @property
    def session_id(self) -> str:
        return self._session_id

    def get_artifacts(self) -> dict[str, Any]:
        """Return current structured debate artifacts for persistence/UI."""
        artifacts: dict[str, Any] = {}
        artifacts["preflight_audit"] = self._preflight_audit.to_dict()
        artifacts["r4_present"] = self._convergence_map is not None and not self._r4_was_skipped
        if self._position_map is not None:
            artifacts["position_map"] = self._position_map.to_dict()
        if self._tension_map is not None:
            artifacts["tension_map"] = self._tension_map.to_dict()
        if self._engagement_record is not None:
            artifacts["engagement_record"] = self._engagement_record.to_dict()
        if self._convergence_map is not None:
            artifacts["convergence_map"] = self._convergence_map.to_dict()
        if self._agent_evolutions:
            artifacts["agent_evolutions"] = [
                evo.to_dict() for evo in self._agent_evolutions.values()
            ]
        if self._round_audits:
            artifacts["round_audits"] = {
                str(round_number): audit.to_dict()
                for round_number, audit in self._round_audits.items()
            }
        if self._profile.get("core_dilemma"):
            artifacts["dilemma_text"] = self._profile.get("core_dilemma")
        artifacts["psyche_bundle"] = self._psyche_bundle.to_dict()
        return artifacts

    def get_state(self) -> dict[str, Any]:
        """Return current debate state (for API responses)."""
        return {
            "current_round": self._state.current_round,
            "current_phase": self._state.current_phase.value,
            "is_done": self._state.is_done(),
            "is_paused": self._paused,
            "blocked_reason": self._blocked_reason,
            "latest_round_action": self._latest_round_action,
            "statements": self._all_statements,
        }

    def get_all_statements(self) -> list[dict[str, Any]]:
        return list(self._all_statements)
