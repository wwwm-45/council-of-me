"""Assemble session data into a ReportContext for the downloadable report.

Pure functions only -- no network calls. The synthesis dict is read from disk
(or generated) by the endpoint layer and passed in.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def _top_evidence(pole: dict) -> dict | None:
    ev = pole.get("evidence_statements") or []
    if not ev:
        return None
    best = max(ev, key=lambda e: e.get("relevance_score", 0) or 0)
    content = best.get("content", "")
    if not content:
        return None
    return {"agent": best.get("agent_name", ""), "content": content}


@dataclass
class ReportContext:
    core_dilemma: str
    headline_seed: str
    narrative: str
    voices: list[dict]
    tensions: list[dict]
    consensus: list[dict]
    protective_intents: list[dict]
    highlights: list[str]
    meta: dict

    def to_digest(self) -> dict:
        """Compact, JSON-serializable view fed to the LLM summarizer."""
        return {
            "core_dilemma": self.core_dilemma,
            "key_insight": self.headline_seed,
            "narrative": self.narrative,
            "voices": [{"name": v["name"], "stance": v["stance"]} for v in self.voices],
            "tensions": [
                {
                    "name": t["name"],
                    "pole_a": {"label": t["pole_a_label"], "stance": t["pole_a_stance"]},
                    "pole_b": {"label": t["pole_b_label"], "stance": t["pole_b_stance"]},
                    "intensity": t["intensity"],
                }
                for t in self.tensions
            ],
            "consensus": [c["description"] for c in self.consensus],
            "protective_intents": [
                {"agent": p["agent_name"], "protects": p["what_it_protects"], "value": p["underlying_value"]}
                for p in self.protective_intents
            ],
            "highlights": self.highlights,
        }


def build_report_context(
    *,
    synthesis: dict | None,
    session_row: Any,
    generated_at: datetime | None = None,
) -> ReportContext:
    synthesis = synthesis or {}
    snapshot = getattr(session_row, "conflict_profile_snapshot", None) or {}
    core_dilemma = snapshot.get("core_dilemma") or synthesis.get("dilemma_text") or ""

    voices = [
        {"name": v.get("agent_name", ""), "stance": v.get("core_stance", "")}
        for v in (synthesis.get("voice_positions") or [])
    ]

    tensions: list[dict] = []
    for t in synthesis.get("core_tensions") or []:
        pole_a = t.get("pole_a") or {}
        pole_b = t.get("pole_b") or {}
        tensions.append(
            {
                "name": t.get("name", ""),
                "pole_a_label": pole_a.get("label", ""),
                "pole_a_stance": pole_a.get("stance", ""),
                "pole_b_label": pole_b.get("label", ""),
                "pole_b_stance": pole_b.get("stance", ""),
                "intensity": t.get("intensity", 0) or 0,
                "evidence_a": _top_evidence(pole_a),
                "evidence_b": _top_evidence(pole_b),
            }
        )

    consensus = [{"description": c.get("description", "")} for c in (synthesis.get("consensus_areas") or [])]
    protective_intents = [
        {
            "agent_name": p.get("agent_name", ""),
            "what_it_protects": p.get("what_it_protects", ""),
            "underlying_value": p.get("underlying_value", ""),
        }
        for p in (synthesis.get("protective_intents") or [])
    ]

    highlights = [h for h in (synthesis.get("highlight_moments") or []) if h]
    if not highlights:
        highlights = [s.get("label", "") for s in (synthesis.get("significant_turns") or []) if s.get("label")]

    meta_in = synthesis.get("meta") or {}
    gen = generated_at or datetime.now(timezone.utc)
    meta = {
        "debate_rounds": meta_in.get("debate_rounds") or getattr(session_row, "total_rounds", None) or 0,
        "agent_count": getattr(session_row, "agent_count", None) or len(voices),
        "convergence_score": meta_in.get("convergence_score"),
        "generated_at": gen.strftime("%Y-%m-%d %H:%M"),
    }

    return ReportContext(
        core_dilemma=core_dilemma,
        headline_seed=synthesis.get("key_insight", "") or "",
        narrative=synthesis.get("narrative", "") or "",
        voices=voices,
        tensions=tensions,
        consensus=consensus,
        protective_intents=protective_intents,
        highlights=highlights,
        meta=meta,
    )
