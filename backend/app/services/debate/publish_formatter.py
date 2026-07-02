from __future__ import annotations

import re
from typing import Any

from app.services.debate.artifacts import (
    ConvergenceMap,
    EngagementRecord,
    PositionMap,
    RoundSummary,
    SignificantTurn,
    TensionMap,
)
from app.services.debate.audit import AuditResult
from app.services.debate.spine import DebateSpine


_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[a-z0-9]+", re.IGNORECASE)
_COST_SIGNAL_RE = re.compile(
    r"(代价|成本|后果|风险|回撤|承担|失败|cost|price|risk|rollback|fallback|downside|pay|pays|bear|bears|carry|carries|absorb|absorbs|fail|fails|failed)",
    re.IGNORECASE,
)
_CONCESSION_SIGNAL_RE = re.compile(
    r"(承认|让步|确实|理解|admit|accept|concede|acknowledge)",
    re.IGNORECASE,
)
_MOVEMENT_SIGNAL_RE = re.compile(
    r"(转向|开始|改口|变化|重新考虑|shift|changed|moved|reconsider)",
    re.IGNORECASE,
)


class PublishFormatter:
    def build_round_payload(
        self,
        *,
        round_number: int,
        phase: str,
        artifact: PositionMap | TensionMap | EngagementRecord | ConvergenceMap,
        spine: DebateSpine | None,
        audit_result: AuditResult | None,
        statements: list[dict[str, Any]] | None,
        display_name_map: dict[str, str] | None,
    ) -> dict[str, Any]:
        statements = statements or []
        display_name_map = display_name_map or {}
        spine = spine or DebateSpine(
            core_contradiction="",
            voice_entry_points={},
            active_questions=[],
            cost_ledger=[],
            progress_markers={},
        )

        summary = self._build_summary(
            round_number=round_number,
            phase=phase,
            artifact=artifact,
            spine=spine,
            audit_result=audit_result,
            statements=statements,
        )
        payload = artifact.to_dict()
        payload["summary"] = summary.to_dict()
        payload["significant_turns"] = [
            turn.to_dict()
            for turn in self._pick_significant_turns(
                statements=statements,
                display_name_map=display_name_map,
                spine=spine,
            )
        ]
        payload["low_trust"] = self._is_low_trust(audit_result)
        payload["phase"] = phase
        return self._replace_agent_ids(payload, display_name_map)

    def _build_summary(
        self,
        *,
        round_number: int,
        phase: str,
        artifact: PositionMap | TensionMap | EngagementRecord | ConvergenceMap,
        spine: DebateSpine,
        audit_result: AuditResult | None,
        statements: list[dict[str, Any]],
    ) -> RoundSummary:
        if isinstance(artifact, PositionMap):
            return RoundSummary(
                current_dispute=self._fallback_text(
                    spine.core_contradiction,
                    artifact.initial_landscape,
                    f"第 {round_number} 轮开始勾勒主要分歧",
                ),
                key_change=self._fallback_text(
                    artifact.initial_landscape,
                    self._contrast_from_positions(artifact),
                    "各个声音的初始站位已经展开。",
                ),
                unresolved_issue=self._fallback_text(
                    self._first_active_question(spine),
                    spine.core_contradiction,
                ),
            )

        if isinstance(artifact, TensionMap):
            return RoundSummary(
                current_dispute=self._fallback_text(
                    self._dominant_tension_description(artifact),
                    spine.core_contradiction,
                ),
                key_change=self._fallback_text(
                    self._round2_key_change(artifact, audit_result, statements, spine),
                    "无实质推进",
                ),
                unresolved_issue=self._fallback_text(
                    artifact.unaddressed_angles[0] if artifact.unaddressed_angles else "",
                    self._first_active_question(spine),
                    spine.core_contradiction,
                ),
            )

        if isinstance(artifact, EngagementRecord):
            return RoundSummary(
                current_dispute=self._fallback_text(
                    self._first_active_question(spine),
                    spine.core_contradiction,
                ),
                key_change=self._fallback_text(
                    artifact.highlight_moments[0] if artifact.highlight_moments else "",
                    self._first_position_shift_text(artifact),
                    "争论开始逼近真正难以回避的代价。",
                ),
                unresolved_issue=self._fallback_text(
                    artifact.unresolved_disagreements[0] if artifact.unresolved_disagreements else "",
                    self._first_active_question(spine),
                    spine.core_contradiction,
                ),
            )

        return RoundSummary(
            current_dispute=self._fallback_text(
                getattr(artifact, "key_insight", ""),
                spine.core_contradiction,
            ),
            key_change=self._fallback_text(
                self._first_list_item(getattr(artifact, "consensus", [])),
                getattr(artifact, "key_insight", ""),
                "讨论已经形成可供收束的主要认识。",
            ),
            unresolved_issue=self._fallback_text(
                self._first_irreducible_difference(artifact),
                self._first_active_question(spine),
                spine.core_contradiction,
            ),
        )

    def _pick_significant_turns(
        self,
        *,
        statements: list[dict[str, Any]],
        display_name_map: dict[str, str],
        spine: DebateSpine,
    ) -> list[SignificantTurn]:
        ranked: list[tuple[int, int, SignificantTurn, str]] = []
        seen_contents: set[str] = set()

        for index, statement in enumerate(statements):
            content = (statement.get("content") or "").strip()
            statement_id = (statement.get("statement_id") or "").strip()
            agent_id = (statement.get("agent_id") or "").strip()
            if not content or not statement_id:
                continue

            normalized = self._normalize_text(content)
            if normalized in seen_contents:
                continue
            seen_contents.add(normalized)

            label, score = self._label_and_score(
                content=content,
                spine=spine,
            )
            ranked.append(
                (
                    -score,
                    index,
                    SignificantTurn(
                        statement_id=statement_id,
                        label=label,
                        agent_name=display_name_map.get(agent_id, statement.get("agent_name") or agent_id),
                    ),
                    normalized,
                )
            )

        ranked.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in ranked[:2]]

    def _label_and_score(self, *, content: str, spine: DebateSpine) -> tuple[str, int]:
        normalized = self._normalize_text(content)
        score = 1

        if self._mentions_cost(content, spine):
            return "暴露新代价", 6 + self._focus_hits(normalized, spine)
        if _CONCESSION_SIGNAL_RE.search(content):
            return "承认关键约束", 5 + self._focus_hits(normalized, spine)
        if _MOVEMENT_SIGNAL_RE.search(content):
            return "立场发生移动", 4 + self._focus_hits(normalized, spine)
        return "逼近核心分歧", score + self._focus_hits(normalized, spine)

    def _mentions_cost(self, content: str, spine: DebateSpine) -> bool:
        if _COST_SIGNAL_RE.search(content):
            return True
        normalized = self._normalize_text(content)
        for item in spine.cost_ledger[:4]:
            keyword = self._normalize_text(item)
            if keyword and keyword in normalized:
                return True
        return False

    def _focus_hits(self, normalized: str, spine: DebateSpine) -> int:
        hits = 0
        for source in [
            spine.core_contradiction,
            self._first_active_question(spine),
            *spine.cost_ledger[:3],
        ]:
            keyword = self._normalize_text(source)
            if keyword and keyword[:12] in normalized:
                hits += 1
        return hits

    def _round2_key_change(
        self,
        artifact: TensionMap,
        audit_result: AuditResult | None,
        statements: list[dict[str, Any]],
        spine: DebateSpine,
    ) -> str:
        if audit_result is not None and (
            audit_result.recommended_action in {"tighten_next_prompt", "hold_termination"}
            or audit_result.metrics.get("novelty", 1.0) < 0.45
        ):
            return "无实质推进"

        for statement in statements:
            content = (statement.get("content") or "").strip()
            if self._mentions_cost(content, spine):
                return "新的现实代价被摊开了。"

        if artifact.new_tensions_since_last > 0:
            return "新的张力已经被摊开。"
        if artifact.overall_progress == "stabilized":
            return "争点开始收束到更清晰的主轴上。"
        return "争点开始集中到最难回避的冲突上。"

    @staticmethod
    def _contrast_from_positions(artifact: PositionMap) -> str:
        if len(artifact.positions) < 2:
            return ""
        left = artifact.positions[0].core_stance
        right = artifact.positions[1].core_stance
        return f"主要分歧已经显形：一方强调「{left}」，另一方强调「{right}」。"

    @staticmethod
    def _first_position_shift_text(artifact: EngagementRecord) -> str:
        if not artifact.position_shifts:
            return ""
        shift = artifact.position_shifts[0]
        return f"至少有一个声音开始从「{shift.from_position}」转向「{shift.to_position}」。"

    @staticmethod
    def _dominant_tension_description(artifact: TensionMap) -> str:
        for tension in artifact.tensions:
            if tension.id == artifact.dominant_tension_id:
                return tension.description
        return artifact.tensions[0].description if artifact.tensions else ""

    @staticmethod
    def _first_irreducible_difference(artifact: Any) -> str:
        differences = getattr(artifact, "irreducible_differences", [])
        if not differences:
            return ""
        first = differences[0]
        return getattr(first, "description", "") if first is not None else ""

    @staticmethod
    def _first_list_item(items: list[str]) -> str:
        return items[0] if items else ""

    @staticmethod
    def _first_active_question(spine: DebateSpine) -> str:
        return spine.active_questions[0].prompt_text if spine.active_questions else ""

    @staticmethod
    def _is_low_trust(audit_result: AuditResult | None) -> bool:
        if audit_result is None:
            return False
        return audit_result.recommended_action in {
            "downgrade_publish",
            "tighten_next_prompt",
            "hold_termination",
        }

    @staticmethod
    def _fallback_text(*values: str) -> str:
        for value in values:
            text = (value or "").strip()
            if text:
                return text
        return ""

    @staticmethod
    def _normalize_text(text: str) -> str:
        lowered = (text or "").strip().lower()
        if not lowered:
            return ""
        return "".join(_TOKEN_RE.findall(lowered))

    def _replace_agent_ids(self, value: Any, display_name_map: dict[str, str]) -> Any:
        if isinstance(value, dict):
            return {
                display_name_map.get(key, key): self._replace_agent_ids(item, display_name_map)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._replace_agent_ids(item, display_name_map) for item in value]
        if isinstance(value, str):
            return display_name_map.get(value, value)
        return value
