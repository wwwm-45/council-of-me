"""LLM-based round evaluator for structured artifact extraction and termination decisions.

The RoundEvaluator is called by the orchestrator at key points during the debate:

    After R1     -> extract_position_map + track_evolution per agent
    Every 3 in R2 -> extract_tension_map (stability check)
    R2 end       -> final extract_tension_map + track_evolution
    Every 3 in R3 -> evaluate_engagement (depth check)
    R3 end       -> final evaluate_engagement + track_evolution
    R4 Step 2    -> extract_convergence_map

Each extraction method:
  1. Builds a prompt asking the LLM for JSON with a specific schema
  2. Calls ``generate()`` with ``temperature=0.3``
  3. Parses the JSON response into the corresponding artifact dataclass
  4. Falls back gracefully when the LLM returns unparseable output
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable, Optional, TypeVar

from app.services.debate.artifacts import (
    AgentEvolution,
    AgentPosition,
    ConvergenceMap,
    EngagementRecord,
    IrreducibleDifference,
    PositionMap,
    PositionShift,
    ProductiveTension,
    HORIZONS,
    Tension,
    TensionEngagement,
    TensionMap,
)
from app.services.debate.spine import DebateSpine
from app.services.language_guard import (
    chinese_system_prompt,
    find_low_chinese_fields,
    record_failure,
    record_retry,
)
from app.services.llm import generate

logger = logging.getLogger(__name__)

# How many exchanges between evaluator checks (used by the orchestrator)
EVAL_INTERVAL = 3
_JSON_SYSTEM_PROMPT = chinese_system_prompt("只返回 JSON。")
_RETRY_SUFFIX = (
    "\n\n上一次输出里有面向用户的说明字段出现了整段英文。"
    "请重新生成完整 JSON，并确保所有说明性字段使用中文，只保留必要的英文专有名词。"
)
TArtifact = TypeVar("TArtifact")


# ---------------------------------------------------------------------------
# JSON parsing helper
# ---------------------------------------------------------------------------

def _parse_json(text: str) -> Optional[dict]:
    """Try to extract a JSON dict from *text*.

    Strategy:
      1. Direct ``json.loads()`` on the full text.
      2. Extract content inside markdown code blocks (````` ```json ... ``` ````` or
         ````` ``` ... ``` `````).
      3. Return ``None`` on failure.
    """
    if not text or not text.strip():
        return None

    # 1. Try direct parse
    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, dict):
            return parsed
        return None
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. Try extracting from markdown code blocks
    # Match ```json ... ``` or ``` ... ```
    pattern = r"```(?:json)?\s*\n?(.*?)\n?\s*```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(1).strip())
            if isinstance(parsed, dict):
                return parsed
            return None
        except (json.JSONDecodeError, ValueError):
            pass

    return None


# ---------------------------------------------------------------------------
# RoundEvaluator
# ---------------------------------------------------------------------------


class _DefaultRouter:
    async def generate(
        self,
        *,
        task: str,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> str:
        return await generate(
            prompt=prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
        )


class RoundEvaluator:
    """Extracts structured artifacts from debate rounds via LLM calls.

    All LLM methods are async and use ``temperature=0.3`` for consistent
    structured output.  Every method returns a valid artifact even when the
    LLM output is unparseable -- fallbacks construct minimal instances from
    the raw input data.
    """

    def __init__(self, router=None) -> None:
        self._router = router or _DefaultRouter()

    async def _generate_with_language_retry(
        self,
        *,
        task: str,
        prompt: str,
        temperature: float,
        parse_fn: Callable[[str], TArtifact | None],
        collect_failures: Callable[[TArtifact], list[str]],
    ) -> TArtifact | None:
        initial = await self._router.generate(
            task=task,
            prompt=prompt,
            system=_JSON_SYSTEM_PROMPT,
            temperature=temperature,
        )
        parsed = parse_fn(initial)
        if parsed is None:
            return None

        initial_failures = collect_failures(parsed)
        if not initial_failures:
            return parsed

        record_retry()
        try:
            retry_raw = await self._router.generate(
                task=task,
                prompt=prompt + _RETRY_SUFFIX,
                system=_JSON_SYSTEM_PROMPT,
                temperature=temperature,
            )
        except Exception:
            logger.warning(
                "language_guard_warning: %s retry failed for %s",
                task,
                ", ".join(initial_failures),
            )
            record_failure()
            return parsed

        retry_parsed = parse_fn(retry_raw)
        if retry_parsed is None:
            logger.warning(
                "language_guard_warning: %s retry returned invalid JSON for %s",
                task,
                ", ".join(initial_failures),
            )
            record_failure()
            return parsed

        retry_failures = collect_failures(retry_parsed)
        if not retry_failures:
            return retry_parsed

        best = retry_parsed if len(retry_failures) < len(initial_failures) else parsed
        logger.warning(
            "language_guard_warning: %s fields still English-heavy after retry; keeping best parsed artifact (%s)",
            task,
            ", ".join(retry_failures),
        )
        record_failure()
        return best

    @staticmethod
    def _position_map_language_failures(artifact: PositionMap) -> list[str]:
        fields = [("initial_landscape", artifact.initial_landscape)]
        for index, position in enumerate(artifact.positions):
            fields.append((f"positions[{index}].core_stance", position.core_stance))
        return find_low_chinese_fields(fields)

    @staticmethod
    def _tension_map_language_failures(artifact: TensionMap) -> list[str]:
        fields: list[tuple[str, str]] = []
        for index, tension in enumerate(artifact.tensions):
            fields.append((f"tensions[{index}].description", tension.description))
            for agent_id, stance in tension.sides.items():
                fields.append((f"tensions[{index}].sides[{agent_id}]", stance))
        for index, angle in enumerate(artifact.unaddressed_angles):
            fields.append((f"unaddressed_angles[{index}]", angle))
        return find_low_chinese_fields(fields)

    @staticmethod
    def _engagement_language_failures(artifact: EngagementRecord) -> list[str]:
        fields: list[tuple[str, str]] = []
        for index, item in enumerate(artifact.tension_engagement):
            fields.append((f"tension_engagement[{index}].shift", item.shift))
        for index, item in enumerate(artifact.position_shifts):
            fields.extend(
                [
                    (f"position_shifts[{index}].from_position", item.from_position),
                    (f"position_shifts[{index}].to_position", item.to_position),
                ]
            )
        for index, item in enumerate(artifact.concessions_made):
            fields.append((f"concessions_made[{index}].concession", item.get("concession", "")))
        for index, value in enumerate(artifact.highlight_moments):
            fields.append((f"highlight_moments[{index}]", value))
        for index, value in enumerate(artifact.unresolved_disagreements):
            fields.append((f"unresolved_disagreements[{index}]", value))
        return find_low_chinese_fields(fields)

    @staticmethod
    def _convergence_language_failures(artifact: ConvergenceMap) -> list[str]:
        fields: list[tuple[str, str]] = [("key_insight", artifact.key_insight)]
        for index, value in enumerate(artifact.consensus):
            fields.append((f"consensus[{index}]", value))
        for index, item in enumerate(artifact.productive_tensions):
            fields.extend(
                [
                    (f"productive_tensions[{index}].description", item.description),
                    (f"productive_tensions[{index}].understanding", item.understanding),
                ]
            )
        for index, item in enumerate(artifact.irreducible_differences):
            fields.extend(
                [
                    (f"irreducible_differences[{index}].description", item.description),
                    (f"irreducible_differences[{index}].why_irreducible", item.why_irreducible),
                ]
            )
        for agent_id, stance in artifact.agent_final_positions.items():
            fields.append((f"agent_final_positions[{agent_id}]", stance))
        return find_low_chinese_fields(fields)

    @staticmethod
    def _evolution_language_failures(artifact: AgentEvolution) -> list[str]:
        return find_low_chinese_fields(
            [
                ("r1_position", artifact.r1_position),
                ("current_position", artifact.current_position),
                ("shift_trigger", artifact.shift_trigger or ""),
                ("emotional_state", artifact.emotional_state),
            ]
        )

    # ------------------------------------------------------------------ R1
    async def extract_position_map(
        self, r1_statements: list[dict]
    ) -> PositionMap:
        """Analyse R1 statements and return a :class:`PositionMap`.

        Parameters
        ----------
        r1_statements:
            Each dict has ``agent_id``, ``agent_name``, ``content``.
        """
        statements_text = "\n".join(
            f"- {s.get('agent_name', s.get('agent_id', '?'))}: {s.get('content', '')}"
            for s in r1_statements
        )

        prompt = (
            "You are analysing the first round (R1) of a multi-agent debate.\n"
            "Each agent has made an independent opening statement.\n\n"
            "## Statements\n"
            f"{statements_text}\n\n"
            "## Task\n"
            "Produce a JSON object with exactly this schema:\n"
            "```\n"
            "{\n"
            '  "positions": [\n'
            '    {"agent_id": "<id>", "core_stance": "<one-sentence summary>", "key_values": ["<value1>", ...]},\n'
            "    ...\n"
            "  ],\n"
            '  "initial_landscape": "<1-2 sentence overview of the overall landscape>"\n'
            "}\n"
            "```\n"
            "Return ONLY the JSON object, no extra text."
        )

        try:
            artifact = await self._generate_with_language_retry(
                task="position_extraction",
                prompt=prompt,
                temperature=0.3,
                parse_fn=self._parse_position_map_artifact,
                collect_failures=self._position_map_language_failures,
            )
        except Exception:
            logger.exception("extract_position_map: LLM call failed, using fallback")
            return self._fallback_position_map(r1_statements)

        if artifact is not None:
            return artifact

        # Fallback: construct from raw statement data
        logger.warning("extract_position_map: LLM JSON parse failed, using fallback")
        return self._fallback_position_map(r1_statements)

    # ------------------------------------------------------------------ R2
    async def extract_tension_map(
        self,
        exchanges: list[dict],
        previous_tension_map: Optional[TensionMap] = None,
        *,
        spine: Optional[DebateSpine] = None,
    ) -> TensionMap:
        """Identify tensions from R2 exchanges.

        Parameters
        ----------
        exchanges:
            Each dict has ``agent_id``, ``agent_name``, ``content``.
        previous_tension_map:
            If provided, included in the prompt so the LLM can compare and
            detect stability.
        """
        exchanges_text = "\n".join(
            f"- {e.get('agent_name', e.get('agent_id', '?'))}: {e.get('content', '')}"
            for e in exchanges
        )

        prev_section = ""
        if previous_tension_map is not None:
            prev_section = (
                "\n## \u4e0a\u4e00\u6b21\u5f20\u529b\u56fe\n"
                f"{previous_tension_map.to_prompt_text()}\n"
            )

        spine_section = ""
        if spine is not None:
            spine_section = (
                "\n## \u5f53\u524d debate spine\n"
                f"{spine.to_prompt_text()}\n"
            )

        prompt = (
            "\u4f60\u5728\u5ba1\u6838\u7b2c 2 \u8f6e\u4ea4\u950b\uff0c\u4e0d\u662f\u505a\u81ea\u7531\u603b\u7ed3\u3002\n\n"
            "## \u672c\u8f6e\u53d1\u8a00\n"
            f"{exchanges_text}\n"
            f"{prev_section}"
            f"{spine_section}\n"
            "## \u4efb\u52a1\n"
            "\u8bf7\u8f93\u51fa JSON\uff0c\u5e76\u663e\u5f0f\u5224\u65ad voice_fidelity\u3001issue_coupling\u3001"
            "target_specificity\u3001novelty\u3001pressure_response\u3001user_resonance\u3002\n"
            "\u6240\u6709\u5b57\u6bb5\u548c\u8bf4\u660e\u90fd\u4f7f\u7528\u4e2d\u6587\u3002\n"
            "\u8fd4\u56de\u5b57\u6bb5\u5fc5\u987b\u81f3\u5c11\u5305\u542b\uff1atensions\u3001dominant_tension_id\u3001"
            "unaddressed_angles\u3001new_tensions_since_last\u3001overall_progress\u3001summary\u3002\n"
            "summary \u5fc5\u987b\u542b\u6709 current_dispute\u3001key_change\u3001unresolved_issue "
            "\u4e09\u4e2a\u4e2d\u6587\u5b57\u6bb5\u3002\n"
            "\u5982\u679c\u8fde\u7eed\u53d1\u8a00\u53ea\u662f\u6362\u53e5\u8bdd\u91cd\u590d\u540c\u4e00"
            "\u4e2a\u538b\u529b\u70b9\uff0c\u8bf7\u628a novelty \u8bc4\u5206\u538b\u4f4e\uff0c"
            "\u5e76\u5728 summary.key_change \u91cc\u660e\u786e\u5199\u51fa\u201c\u65e0\u5b9e\u8d28"
            "\u63a8\u8fdb\u201d\u3002\n"
            "\u6bcf\u6761\u5f20\u529b\u8fd8\u8981\u6807 horizon\uff08\u65f6\u95f4/\u51b3\u7b56\u89c6\u91ce\uff09\uff1aimmediate=\u4eca\u5929\u6216\u8fd9\u51e0\u5929\u80fd\u505a\u7684\u5373\u65f6\u52a8\u4f5c\uff1bmedium=\u6570\u5468\u5230\u6570\u6708\u7684\u4e2d\u671f\u6295\u9012/\u4ea4\u4ed8\uff08\u6295\u7b80\u5386\u3001\u62a5\u540d\u3001\u7533\u8bf7\u3001\u9879\u76ee\u4ea4\u4ed8\uff09\uff1blong=\u4eba\u751f\u65b9\u5411\u6216\u8eab\u4efd\u5c42\u9762\u7684\u957f\u671f\u8def\u7ebf\u3002\n"
            "\u8fd4\u56de\u7ed3\u6784\u793a\u4f8b\uff1a\n"
            "```\n"
            "{\n"
            '  "tensions": [\n'
            '    {"id": 1, "description": "<\u5f20\u529b\u63cf\u8ff0>", "sides": {"<agent_id>": "<\u7acb\u573a>", ...}, "depth": "surface|moderate|deep", "horizon": "immediate|medium|long"},\n'
            "    ...\n"
            "  ],\n"
            '  "dominant_tension_id": <int or null>,\n'
            '  "unaddressed_angles": ["<\u8fd8\u6ca1\u88ab\u56de\u7b54\u7684\u89d2\u5ea6>", ...],\n'
            '  "new_tensions_since_last": <int>,\n'
            '  "overall_progress": "emerging|developing|stabilized",\n'
            '  "summary": {\n'
            '    "current_dispute": "<\u5f53\u524d\u5728\u5435\u4ec0\u4e48>",\n'
            '    "key_change": "<\u6709\u4ec0\u4e48\u65b0\u53d8\u5316>",\n'
            '    "unresolved_issue": "<\u8fd8\u6ca1\u6495\u5f00\u7684\u7ed3>"\n'
            "  }\n"
            "}\n"
            "```\n"
            "\u53ea\u8fd4\u56de JSON \u5bf9\u8c61\uff0c\u4e0d\u8981\u9644\u52a0\u989d\u5916\u6587\u5b57\u3002"
        )

        try:
            artifact = await self._generate_with_language_retry(
                task="tension_extraction",
                prompt=prompt,
                temperature=0.3,
                parse_fn=self._parse_tension_map_artifact,
                collect_failures=self._tension_map_language_failures,
            )
        except Exception:
            logger.exception("extract_tension_map: LLM call failed, using fallback")
            return self._fallback_tension_map()

        if artifact is not None:
            return artifact

        logger.warning("extract_tension_map: LLM JSON parse failed, using fallback")
        return self._fallback_tension_map()

    # ------------------------------------------------------------------ R3
    async def evaluate_engagement(
        self,
        exchanges: list[dict],
        tension_map: TensionMap,
    ) -> EngagementRecord:
        """Evaluate depth of engagement with tensions during R3.

        Parameters
        ----------
        exchanges:
            R3 exchange dicts.
        tension_map:
            The TensionMap produced by R2.
        """
        exchanges_text = "\n".join(
            f"- {e.get('agent_name', e.get('agent_id', '?'))}: {e.get('content', '')}"
            for e in exchanges
        )

        prompt = (
            "You are analysing Round 3 (deepening) of a multi-agent debate.\n\n"
            "## Tension Map from R2\n"
            f"{tension_map.to_prompt_text()}\n\n"
            "## R3 Exchanges\n"
            f"{exchanges_text}\n\n"
            "## Task\n"
            "Evaluate how deeply each agent engaged with each tension.\n"
            "Produce a JSON object:\n"
            "```\n"
            "{\n"
            '  "tension_engagement": [\n'
            '    {"tension_id": <int>, "agent_id": "<id>", "depth": "surface|moderate|deep", "shift": "<description>", "disagreement_layer": "epistemic|axiological|identity|mixed"},\n'
            "    ...\n"
            "  ],\n"
            '  "position_shifts": [\n'
            '    {"agent_id": "<id>", "from_position": "<old>", "to_position": "<new>"},\n'
            "    ...\n"
            "  ],\n"
            '  "concessions_made": [\n'
            '    {"agent_id": "<id>", "concession": "<what they conceded>"},\n'
            "    ...\n"
            "  ],\n"
            '  "highlight_moments": ["<moment description>", ...],\n'
            '  "unresolved_disagreements": ["<explicit unresolved disagreement or boundary>", ...]\n'
            "}\n"
            "```\n"
            "Return ONLY the JSON object, no extra text."
        )

        try:
            artifact = await self._generate_with_language_retry(
                task="engagement_evaluation",
                prompt=prompt,
                temperature=0.3,
                parse_fn=self._parse_engagement_artifact,
                collect_failures=self._engagement_language_failures,
            )
        except Exception:
            logger.exception("evaluate_engagement: LLM call failed, using fallback")
            return self._fallback_engagement_record()

        if artifact is not None:
            return artifact

        logger.warning("evaluate_engagement: LLM JSON parse failed, using fallback")
        return self._fallback_engagement_record()

    # ------------------------------------------------------------------ R4
    async def extract_convergence_map(
        self,
        reflections: list[dict],
        full_history: list[dict],
        reanchor_landing: str | None = None,
    ) -> ConvergenceMap:
        """Produce a convergence snapshot from R4 reflections.

        Parameters
        ----------
        reflections:
            R4 reflection statement dicts.
        full_history:
            The complete list of exchange dicts across all rounds.
        reanchor_landing:
            Optional 3-A user re-anchor patch text. When set, the prompt asks
            ``key_insight`` to surface the landing the user's answer validated
            instead of a generic takeaway. ``None`` leaves the prompt unchanged.
        """
        reflections_text = "\n".join(
            f"- {r.get('agent_name', r.get('agent_id', '?'))}: {r.get('content', '')}"
            for r in reflections
        )

        # Summarise full history (truncate to avoid huge prompts)
        history_lines = []
        for h in full_history[-30:]:  # Keep last 30 exchanges max
            name = h.get("agent_name", h.get("agent_id", "?"))
            content = h.get("content", "")
            if len(content) > 200:
                content = content[:200] + "..."
            history_lines.append(f"- {name}: {content}")
        history_text = "\n".join(history_lines)

        verdict_block = ""
        if reanchor_landing:
            verdict_block = (
                "## User Verdict (re-anchored)\n"
                f"The user answered a follow-up that re-anchored the map: {reanchor_landing}\n"
                "When producing `key_insight`, make it the landing the user's answer "
                "validated (which tension collapsed toward which side), not a generic takeaway.\n\n"
            )

        prompt = (
            "You are analysing Round 4 (convergence) of a multi-agent debate.\n\n"
            "## Debate History (recent)\n"
            f"{history_text}\n\n"
            "## R4 Reflection Statements\n"
            f"{reflections_text}\n\n"
            f"{verdict_block}"
            "## Task\n"
            "Produce a convergence map as a JSON object:\n"
            "```\n"
            "{\n"
            '  "consensus": ["<point of agreement>", ...],\n'
            '  "productive_tensions": [\n'
            '    {"description": "<tension>", "understanding": "<mutual understanding>"},\n'
            "    ...\n"
            "  ],\n"
            '  "irreducible_differences": [\n'
            '    {"description": "<difference>", "why_irreducible": "<reason>"},\n'
            "    ...\n"
            "  ],\n"
            '  "key_insight": "<the most important insight from this debate>",\n'
            '  "agent_final_positions": {"<agent_id>": "<final stance>", ...}\n'
            "}\n"
            "```\n"
            "Return ONLY the JSON object, no extra text."
        )

        try:
            artifact = await self._generate_with_language_retry(
                task="convergence_mapping",
                prompt=prompt,
                temperature=0.3,
                parse_fn=self._parse_convergence_artifact,
                collect_failures=self._convergence_language_failures,
            )
        except Exception:
            logger.exception("extract_convergence_map: LLM call failed, using fallback")
            return self._fallback_convergence_map()

        if artifact is not None:
            return artifact

        logger.warning("extract_convergence_map: LLM JSON parse failed, using fallback")
        return self._fallback_convergence_map()

    # --------------------------------------------------------- cross-round
    async def track_evolution(
        self,
        agent_id: str,
        agent_statements: dict[int, list[str]],
    ) -> AgentEvolution:
        """Analyse one agent's position evolution across rounds.

        Parameters
        ----------
        agent_id:
            The agent whose evolution to track.
        agent_statements:
            Mapping of round number to list of statement strings.
        """
        statements_text = ""
        for rnd in sorted(agent_statements.keys()):
            for stmt in agent_statements[rnd]:
                statements_text += f"- Round {rnd}: {stmt}\n"

        prompt = (
            f"You are tracking the position evolution of agent '{agent_id}' across a debate.\n\n"
            "## Statements by Round\n"
            f"{statements_text}\n"
            "## Task\n"
            "Analyse how this agent's position has evolved and produce a JSON object:\n"
            "```\n"
            "{\n"
            f'  "agent_id": "{agent_id}",\n'
            '  "r1_position": "<their initial R1 stance>",\n'
            '  "current_position": "<their latest stance>",\n'
            '  "shift_type": "none|expansion|revision|reversal",\n'
            '  "shift_trigger": "<what caused the shift, or null>",\n'
            '  "emotional_state": "<current emotional tone>"\n'
            "}\n"
            "```\n"
            "Return ONLY the JSON object, no extra text."
        )

        try:
            artifact = await self._generate_with_language_retry(
                task="evolution_tracking",
                prompt=prompt,
                temperature=0.3,
                parse_fn=lambda raw: self._parse_agent_evolution_artifact(raw, agent_id),
                collect_failures=self._evolution_language_failures,
            )
        except Exception:
            logger.exception("track_evolution: LLM call failed, using fallback for %s", agent_id)
            return self._fallback_agent_evolution(agent_id, agent_statements)

        if artifact is not None:
            return artifact

        logger.warning("track_evolution: LLM JSON parse failed, using fallback for %s", agent_id)
        return self._fallback_agent_evolution(agent_id, agent_statements)

    # --------------------------------------------------------- termination
    def should_end_r2(
        self,
        tension_map: TensionMap,
        consecutive_stable: int | None = None,
    ) -> bool:
        """Pure logic: end R2 when no new tensions for >= 2 consecutive checks.

        Returns ``True`` when ``tension_map.new_tensions_since_last == 0``
        AND ``consecutive_stable >= 2``.
        """
        stable_count = consecutive_stable or 0
        return (
            tension_map.new_tensions_since_last == 0
            and stable_count >= 2
        )

    def should_end_r3(
        self,
        engagement_record: EngagementRecord,
        top_tension_ids: list[int] | None = None,
        allow_convergence_handoff: bool = False,
    ) -> bool:
        """Pure logic: end R3 when engagement and disagreement criteria are met.

        Returns ``True`` when every tension in *top_tension_ids* has at least
        2 agents whose engagement depth is ``"moderate"`` or ``"deep"``.
        For L2 (``allow_convergence_handoff=False``), unresolved disagreement
        must remain explicit before R3 can end. For L3
        (``allow_convergence_handoff=True``), clean handoff to R4 is allowed.
        """
        top_tension_ids = top_tension_ids or []

        for tid in top_tension_ids:
            count = sum(
                1
                for te in engagement_record.tension_engagement
                if te.tension_id == tid and te.depth in ("moderate", "deep")
            )
            if count < 2:
                return False

        if not allow_convergence_handoff and not engagement_record.unresolved_disagreements:
            return False

        return True

    # ===================================================================
    # Private builders: JSON dict -> artifact dataclass
    # ===================================================================

    def _parse_position_map_artifact(self, raw: str) -> PositionMap | None:
        data = _parse_json(raw)
        if data and "positions" in data:
            return self._build_position_map(data)
        return None

    def _parse_tension_map_artifact(self, raw: str) -> TensionMap | None:
        data = _parse_json(raw)
        if data and "tensions" in data:
            return self._build_tension_map(data)
        return None

    def _parse_engagement_artifact(self, raw: str) -> EngagementRecord | None:
        data = _parse_json(raw)
        if data and "tension_engagement" in data:
            return self._build_engagement_record(data)
        return None

    def _parse_convergence_artifact(self, raw: str) -> ConvergenceMap | None:
        data = _parse_json(raw)
        if data and ("consensus" in data or "key_insight" in data):
            return self._build_convergence_map(data)
        return None

    def _parse_agent_evolution_artifact(self, raw: str, agent_id: str) -> AgentEvolution | None:
        data = _parse_json(raw)
        if data and "shift_type" in data:
            return self._build_agent_evolution(data, agent_id)
        return None

    @staticmethod
    def _build_position_map(data: dict) -> PositionMap:
        positions = []
        for p in data.get("positions", []):
            positions.append(
                AgentPosition(
                    agent_id=p.get("agent_id", "unknown"),
                    core_stance=p.get("core_stance", ""),
                    key_values=p.get("key_values", []),
                )
            )
        return PositionMap(
            positions=positions,
            initial_landscape=data.get("initial_landscape", ""),
        )

    @staticmethod
    def _fallback_position_map(statements: list[dict]) -> PositionMap:
        """Build a minimal PositionMap from raw R1 statement dicts."""
        positions = []
        for s in statements:
            content = s.get("content", "")
            # Use first 100 chars as a crude stance summary
            stance = content[:100] if content else "No stance recorded"
            positions.append(
                AgentPosition(
                    agent_id=s.get("agent_id", "unknown"),
                    core_stance=stance,
                    key_values=[],
                )
            )
        return PositionMap(
            positions=positions,
            initial_landscape="(Auto-generated fallback -- LLM extraction failed)",
        )

    @staticmethod
    def _build_tension_map(data: dict) -> TensionMap:
        tensions = []
        for t in data.get("tensions", []):
            horizon = t.get("horizon", "unscoped")
            if not isinstance(horizon, str) or horizon not in HORIZONS:
                horizon = "unscoped"
            tensions.append(
                Tension(
                    id=t.get("id", 0),
                    description=t.get("description", ""),
                    sides=t.get("sides", {}),
                    depth=t.get("depth", "surface"),
                    horizon=horizon,
                )
            )
        return TensionMap(
            tensions=tensions,
            dominant_tension_id=data.get("dominant_tension_id"),
            unaddressed_angles=data.get("unaddressed_angles", []),
            new_tensions_since_last=data.get("new_tensions_since_last", 0),
            overall_progress=data.get("overall_progress", "emerging"),
        )

    @staticmethod
    def _fallback_tension_map() -> TensionMap:
        return TensionMap(
            tensions=[],
            dominant_tension_id=None,
            unaddressed_angles=[],
            new_tensions_since_last=0,
            overall_progress="emerging",
        )

    @staticmethod
    def _build_engagement_record(data: dict) -> EngagementRecord:
        tension_engagement = []
        for te in data.get("tension_engagement", []):
            tension_engagement.append(
                TensionEngagement(
                    tension_id=te.get("tension_id", 0),
                    agent_id=te.get("agent_id", "unknown"),
                    depth=te.get("depth", "surface"),
                    shift=te.get("shift", ""),
                    disagreement_layer=te.get("disagreement_layer", "mixed"),
                )
            )

        position_shifts = []
        for ps in data.get("position_shifts", []):
            position_shifts.append(
                PositionShift(
                    agent_id=ps.get("agent_id", "unknown"),
                    from_position=ps.get("from_position", ""),
                    to_position=ps.get("to_position", ""),
                )
            )

        concessions = []
        for c in data.get("concessions_made", []):
            concessions.append({
                "agent_id": c.get("agent_id", "unknown"),
                "concession": c.get("concession", ""),
            })

        return EngagementRecord(
            tension_engagement=tension_engagement,
            position_shifts=position_shifts,
            concessions_made=concessions,
            highlight_moments=data.get("highlight_moments", []),
            unresolved_disagreements=data.get("unresolved_disagreements", []),
        )

    @staticmethod
    def _fallback_engagement_record() -> EngagementRecord:
        return EngagementRecord(
            tension_engagement=[],
            position_shifts=[],
            concessions_made=[],
            highlight_moments=[],
            unresolved_disagreements=[],
        )

    @staticmethod
    def _build_convergence_map(data: dict) -> ConvergenceMap:
        productive_tensions = []
        for pt in data.get("productive_tensions", []):
            productive_tensions.append(
                ProductiveTension(
                    description=pt.get("description", ""),
                    understanding=pt.get("understanding", ""),
                )
            )

        irreducible_differences = []
        for ir in data.get("irreducible_differences", []):
            irreducible_differences.append(
                IrreducibleDifference(
                    description=ir.get("description", ""),
                    why_irreducible=ir.get("why_irreducible", ""),
                )
            )

        return ConvergenceMap(
            consensus=data.get("consensus", []),
            productive_tensions=productive_tensions,
            irreducible_differences=irreducible_differences,
            key_insight=data.get("key_insight", ""),
            agent_final_positions=data.get("agent_final_positions", {}),
        )

    @staticmethod
    def _fallback_convergence_map() -> ConvergenceMap:
        return ConvergenceMap(
            consensus=[],
            productive_tensions=[],
            irreducible_differences=[],
            key_insight="(Auto-generated fallback -- LLM extraction failed)",
            agent_final_positions={},
        )

    @staticmethod
    def _build_agent_evolution(data: dict, agent_id: str) -> AgentEvolution:
        return AgentEvolution(
            agent_id=data.get("agent_id", agent_id),
            r1_position=data.get("r1_position", ""),
            current_position=data.get("current_position", ""),
            shift_type=data.get("shift_type", "none"),
            shift_trigger=data.get("shift_trigger"),
            emotional_state=data.get("emotional_state", "neutral"),
        )

    @staticmethod
    def _fallback_agent_evolution(
        agent_id: str,
        agent_statements: dict[int, list[str]],
    ) -> AgentEvolution:
        """Build a minimal AgentEvolution from raw statements."""
        sorted_rounds = sorted(agent_statements.keys())
        r1_text = ""
        current_text = ""
        if sorted_rounds:
            r1_stmts = agent_statements.get(sorted_rounds[0], [])
            r1_text = r1_stmts[0][:100] if r1_stmts else ""
            last_stmts = agent_statements.get(sorted_rounds[-1], [])
            current_text = last_stmts[-1][:100] if last_stmts else ""
        return AgentEvolution(
            agent_id=agent_id,
            r1_position=r1_text,
            current_position=current_text,
            shift_type="none",
            shift_trigger=None,
            emotional_state="neutral",
        )
