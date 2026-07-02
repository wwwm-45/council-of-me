"""Structured artifact dataclasses for debate round information flow.

Each debate round produces a typed artifact that feeds into the next round:

    R1 -> PositionMap -> R2 -> TensionMap -> R3 -> EngagementRecord -> R4 -> ConvergenceMap

AgentEvolution tracks how an individual agent's stance changes across rounds.

Every artifact exposes:
    to_dict()        -- JSON-serialisable dict for persistence / API responses
    to_prompt_text() -- Chinese-language summary for injection into agent prompts
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Display-name mapping (agent_id -> Chinese name for prompts)
# ---------------------------------------------------------------------------

_DISPLAY_NAMES: dict[str, str] = {
    "empathic_listener": "共情倾听者",
    "rational_analyst": "理性分析者",
    "critical_examiner": "批判审视者",
    "creative_explorer": "创意探索者",
    "synthesizer": "整合者",
}


def _dn(agent_id: str) -> str:
    """Return the Chinese display name for *agent_id*, falling back to the raw id."""
    return _DISPLAY_NAMES.get(agent_id, agent_id)


# ===========================================================================
# Publish helpers
# ===========================================================================

@dataclass
class RoundSummary:
    current_dispute: str
    key_change: str
    unresolved_issue: str
    language: str = "zh-CN"

    def to_dict(self) -> dict:
        return {
            "current_dispute": self.current_dispute,
            "key_change": self.key_change,
            "unresolved_issue": self.unresolved_issue,
            "language": self.language,
        }


@dataclass
class SignificantTurn:
    statement_id: str
    label: str
    agent_name: str

    def to_dict(self) -> dict:
        return {
            "statement_id": self.statement_id,
            "label": self.label,
            "agent_name": self.agent_name,
        }


@dataclass
class IrreducibleAcknowledgement:
    agent_id: str
    related_tension_id: str
    disagreement: str
    legitimacy_reason: str
    stake: str
    raw_text: str

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "related_tension_id": self.related_tension_id,
            "disagreement": self.disagreement,
            "legitimacy_reason": self.legitimacy_reason,
            "stake": self.stake,
            "raw_text": self.raw_text,
        }


# ===========================================================================
# 1. PositionMap  (R1 output)
# ===========================================================================

@dataclass
class AgentPosition:
    """A single agent's initial stance after Round 1."""

    agent_id: str
    core_stance: str
    key_values: list[str]

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "core_stance": self.core_stance,
            "key_values": list(self.key_values),
        }


@dataclass
class PositionMap:
    """Aggregate of all agents' R1 positions plus an editorial landscape summary."""

    positions: list[AgentPosition]
    initial_landscape: str

    def to_dict(self) -> dict:
        return {
            "positions": [p.to_dict() for p in self.positions],
            "initial_landscape": self.initial_landscape,
        }

    def to_prompt_text(self) -> str:
        lines: list[str] = ["## 立场地图 (R1)"]
        if not self.positions:
            lines.append("暂无立场信息。")
        else:
            for p in self.positions:
                name = _dn(p.agent_id)
                values = "、".join(p.key_values) if p.key_values else "无"
                lines.append(f"- {name}: {p.core_stance} (核心价值: {values})")
            lines.append(f"\n整体格局: {self.initial_landscape}")
        return "\n".join(lines)


# ===========================================================================
# 2. TensionMap  (R2 output)
# ===========================================================================

HORIZONS = {"immediate", "medium", "long", "unscoped"}

@dataclass
class Tension:
    """A single tension identified during cross-examination."""

    id: int
    description: str
    sides: dict[str, str]   # agent_id -> stance on this tension
    depth: str               # surface | moderate | deep
    user_verdict: Optional[dict] = None  # {status, text, from_question_id} once the user answers a follow-up
    horizon: str = "unscoped"  # decision horizon: immediate | medium | long | unscoped

    def to_dict(self) -> dict:
        data = {
            "id": self.id,
            "description": self.description,
            "sides": dict(self.sides),
            "depth": self.depth,
            "horizon": self.horizon,
        }
        if self.user_verdict is not None:
            data["user_verdict"] = dict(self.user_verdict)
        return data


@dataclass
class TensionMap:
    """Aggregate tension landscape after R2."""

    tensions: list[Tension]
    dominant_tension_id: Optional[int]
    unaddressed_angles: list[str]
    new_tensions_since_last: int
    overall_progress: str  # emerging | developing | stabilized

    # -- helpers --------------------------------------------------------

    def set_user_verdict(
        self, tension_id, *, status: str, text: str, from_question_id: str,
    ) -> bool:
        """Attach the user's follow-up answer to the matching tension. Returns True if applied."""
        for tension in self.tensions:
            if str(tension.id) == str(tension_id):
                tension.user_verdict = {
                    "status": status,
                    "text": text,
                    "from_question_id": from_question_id,
                }
                return True
        return False

    # -- serialisation --------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "tensions": [t.to_dict() for t in self.tensions],
            "dominant_tension_id": self.dominant_tension_id,
            "unaddressed_angles": list(self.unaddressed_angles),
            "new_tensions_since_last": self.new_tensions_since_last,
            "overall_progress": self.overall_progress,
        }

    def to_prompt_text(self) -> str:
        lines: list[str] = ["## 张力地图 (R2)"]
        if not self.tensions:
            lines.append("暂无张力信息。")
        else:
            for t in self.tensions:
                marker = " [主导]" if t.id == self.dominant_tension_id else ""
                lines.append(f"### 张力 #{t.id}{marker}: {t.description} (深度: {t.depth})")
                for aid, stance in t.sides.items():
                    lines.append(f"  - {_dn(aid)}: {stance}")
                if t.user_verdict:
                    vs = {"confirmed": "确认", "denied": "否认", "refined": "补充"}.get(
                        t.user_verdict.get("status"), "回应"
                    )
                    lines.append(f"  - 【用户已{vs}】{t.user_verdict.get('text', '')}")
            if self.unaddressed_angles:
                lines.append(f"\n尚未触及的角度: {'、'.join(self.unaddressed_angles)}")
            lines.append(f"进展状态: {self.overall_progress}")
        return "\n".join(lines)


# ===========================================================================
# 3. EngagementRecord  (R3 output)
# ===========================================================================

@dataclass
class TensionEngagement:
    """How deeply one agent engaged with one tension in R3."""

    tension_id: int
    agent_id: str
    depth: str   # surface | moderate | deep
    shift: str   # free-text description of stance change
    disagreement_layer: str = "mixed"

    def to_dict(self) -> dict:
        return {
            "tension_id": self.tension_id,
            "agent_id": self.agent_id,
            "depth": self.depth,
            "shift": self.shift,
            "disagreement_layer": self.disagreement_layer,
        }


@dataclass
class PositionShift:
    """Records a before/after position change for one agent."""

    agent_id: str
    from_position: str
    to_position: str

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "from_position": self.from_position,
            "to_position": self.to_position,
        }


@dataclass
class EngagementRecord:
    """Aggregate engagement data from R3 deepening."""

    tension_engagement: list[TensionEngagement]
    position_shifts: list[PositionShift]
    concessions_made: list[dict[str, str]]
    highlight_moments: list[str]
    r3_5_acknowledgements: list[IrreducibleAcknowledgement] = field(default_factory=list)
    divergence_map: str | None = None
    divergence_map_base: str | None = None
    unresolved_disagreements: list[str] = field(default_factory=list)
    summary: RoundSummary | None = None
    significant_turns: list[SignificantTurn] = field(default_factory=list)
    low_trust: bool = False

    # -- serialisation --------------------------------------------------

    def to_dict(self) -> dict:
        data = {
            "tension_engagement": [te.to_dict() for te in self.tension_engagement],
            "position_shifts": [ps.to_dict() for ps in self.position_shifts],
            "concessions_made": list(self.concessions_made),
            "highlight_moments": list(self.highlight_moments),
            "r3_5_acknowledgements": [
                item.to_dict() for item in self.r3_5_acknowledgements
            ],
            "divergence_map": self.divergence_map,
            "divergence_map_base": self.divergence_map_base,
            "unresolved_disagreements": list(self.unresolved_disagreements),
            "low_trust": self.low_trust,
        }
        if self.summary is not None:
            data["summary"] = self.summary.to_dict()
        if self.significant_turns:
            data["significant_turns"] = [turn.to_dict() for turn in self.significant_turns]
        return data

    def to_prompt_text(self) -> str:
        lines: list[str] = ["## 深化参与记录 (R3)"]

        if self.tension_engagement:
            lines.append("### 张力参与情况")
            for te in self.tension_engagement:
                lines.append(
                    f"  - {_dn(te.agent_id)} 对张力 #{te.tension_id}: "
                    f"深度 {te.depth}, 变化: {te.shift}"
                )

        if self.position_shifts:
            lines.append("### 立场变化")
            for ps in self.position_shifts:
                lines.append(
                    f"  - {_dn(ps.agent_id)}: "
                    f"从「{ps.from_position}」转向「{ps.to_position}」"
                )

        if self.concessions_made:
            lines.append("### 让步")
            for c in self.concessions_made:
                aid = c.get("agent_id", "?")
                concession = c.get("concession", "")
                lines.append(f"  - {_dn(aid)}: {concession}")

        if self.highlight_moments:
            lines.append("### 高光时刻")
            for h in self.highlight_moments:
                lines.append(f"  - {h}")

        if self.unresolved_disagreements:
            lines.append("### Unresolved disagreements")
            for disagreement in self.unresolved_disagreements:
                lines.append(f"  - {disagreement}")

        if len(lines) == 1:
            lines.append("暂无深化参与数据。")

        return "\n".join(lines)


# ===========================================================================
# 4. ConvergenceMap  (R4 output)
# ===========================================================================

@dataclass
class ProductiveTension:
    """A tension that remains but is mutually understood."""

    description: str
    understanding: str

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "understanding": self.understanding,
        }


@dataclass
class IrreducibleDifference:
    """A difference that cannot (and perhaps should not) be resolved."""

    description: str
    why_irreducible: str

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "why_irreducible": self.why_irreducible,
        }


@dataclass
class ConvergenceMap:
    """Aggregate convergence snapshot from R4."""

    consensus: list[str]
    productive_tensions: list[ProductiveTension]
    irreducible_differences: list[IrreducibleDifference]
    key_insight: str
    agent_final_positions: dict[str, str]  # agent_id -> final stance
    agent_voice_similarity_matrix: dict[str, dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "consensus": list(self.consensus),
            "productive_tensions": [pt.to_dict() for pt in self.productive_tensions],
            "irreducible_differences": [
                ir.to_dict() for ir in self.irreducible_differences
            ],
            "key_insight": self.key_insight,
            "agent_final_positions": dict(self.agent_final_positions),
            "agent_voice_similarity_matrix": {
                agent_id: dict(scores)
                for agent_id, scores in self.agent_voice_similarity_matrix.items()
            },
        }

    def to_prompt_text(self) -> str:
        lines: list[str] = ["## 收敛地图 (R4)"]

        if self.consensus:
            lines.append("### 共识")
            for c in self.consensus:
                lines.append(f"  - {c}")

        if self.productive_tensions:
            lines.append("### 有益张力")
            for pt in self.productive_tensions:
                lines.append(f"  - {pt.description}: {pt.understanding}")

        if self.irreducible_differences:
            lines.append("### 不可消除的分歧")
            for ir in self.irreducible_differences:
                lines.append(f"  - {ir.description} -- {ir.why_irreducible}")

        if self.key_insight:
            lines.append(f"\n核心洞察: {self.key_insight}")

        if self.agent_final_positions:
            lines.append("### 各方最终立场")
            for aid, stance in self.agent_final_positions.items():
                lines.append(f"  - {_dn(aid)}: {stance}")

        if len(lines) == 1:
            lines.append("暂无收敛数据。")

        return "\n".join(lines)


# ===========================================================================
# 5. AgentEvolution  (cross-round tracker)
# ===========================================================================

@dataclass
class AgentEvolution:
    """Tracks one agent's stance evolution across debate rounds."""

    agent_id: str
    r1_position: str
    current_position: str
    shift_type: str          # none | expansion | revision | reversal
    shift_trigger: Optional[str]
    emotional_state: str

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "r1_position": self.r1_position,
            "current_position": self.current_position,
            "shift_type": self.shift_type,
            "shift_trigger": self.shift_trigger,
            "emotional_state": self.emotional_state,
        }

    def to_prompt_text(self) -> str:
        name = _dn(self.agent_id)
        shift_cn = {
            "none": "无变化",
            "expansion": "扩展",
            "revision": "修正",
            "reversal": "逆转",
        }.get(self.shift_type, self.shift_type)

        lines: list[str] = [
            f"## {name} 的立场演化",
            f"- R1 立场: {self.r1_position}",
            f"- 当前立场: {self.current_position}",
            f"- 变化类型: {shift_cn}",
        ]
        if self.shift_trigger:
            lines.append(f"- 触发因素: {self.shift_trigger}")
        lines.append(f"- 情绪状态: {self.emotional_state}")
        return "\n".join(lines)
