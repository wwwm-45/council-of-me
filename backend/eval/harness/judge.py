from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from eval.harness.evidence_validation import validate_evidence_quotes


JudgeLlm = Callable[..., Awaitable[str]]


@dataclass(frozen=True)
class JudgeResult:
    status: str
    score: float | None
    summary: str
    evidence: list[str]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "score": self.score,
            "summary": self.summary,
            "evidence": self.evidence,
            "error": self.error,
        }


@dataclass(frozen=True)
class JudgeSample:
    score: float
    dimension_scores: dict[str, float]
    evidence_quotes: list[str]
    blocker_flags: list[str]
    reasoning: str
    accepted: bool
    rejection_reason: str | None = None


def parse_judge_response(raw: str, *, output_text: str) -> JudgeSample:
    payload = json.loads(raw)
    score = float(payload.get("score", 0.0))
    evidence_quotes = [
        str(item).strip()
        for item in (payload.get("evidence_quotes") or [])
        if str(item).strip()
    ]
    blocker_flags = [str(item) for item in (payload.get("blocker_flags") or []) if str(item)]
    dimension_scores = {
        str(key): float(value)
        for key, value in dict(payload.get("dimension_scores") or {}).items()
    }
    reasoning = str(payload.get("reasoning") or "")

    if score < 0.6 and not evidence_quotes:
        return JudgeSample(
            score=score,
            dimension_scores=dimension_scores,
            evidence_quotes=[],
            blocker_flags=blocker_flags,
            reasoning=reasoning,
            accepted=False,
            rejection_reason="missing_evidence",
        )

    valid, missing = validate_evidence_quotes(output_text, evidence_quotes)
    if not valid:
        return JudgeSample(
            score=score,
            dimension_scores=dimension_scores,
            evidence_quotes=evidence_quotes,
            blocker_flags=blocker_flags,
            reasoning=f"{reasoning} | missing={missing}",
            accepted=False,
            rejection_reason="evidence_not_in_output",
        )

    return JudgeSample(
        score=score,
        dimension_scores=dimension_scores,
        evidence_quotes=evidence_quotes,
        blocker_flags=blocker_flags,
        reasoning=reasoning,
        accepted=True,
    )


def aggregate_judge_samples(samples: list[JudgeSample]) -> dict[str, Any]:
    accepted = [sample for sample in samples if sample.accepted]
    chosen = accepted or samples
    if not chosen:
        return {
            "score": 0.0,
            "sample_count": 0,
            "dimension_scores": {},
            "blocker_flags": [],
            "evidence_quotes": [],
            "accepted_samples": 0,
        }
    return {
        "score": round(statistics.median(sample.score for sample in chosen), 4),
        "sample_count": len(samples),
        "dimension_scores": {
            key: round(
                statistics.median(
                    sample.dimension_scores.get(key, 0.0)
                    for sample in chosen
                ),
                4,
            )
            for key in sorted({k for sample in chosen for k in sample.dimension_scores})
        },
        "blocker_flags": sorted({flag for sample in chosen for flag in sample.blocker_flags}),
        "evidence_quotes": chosen[0].evidence_quotes if chosen else [],
        "accepted_samples": len(accepted),
    }


async def run_minimal_judge(
    *,
    target: str,
    output: dict[str, Any],
    assertions: dict[str, Any],
    llm_fn: JudgeLlm,
) -> JudgeResult:
    serialized = json.dumps(output, ensure_ascii=False, sort_keys=True)
    prompt = (
        "Score this structured prompt-eval output from 0.0 to 1.0.\n"
        f"Target: {target}\n"
        f"Assertions: {json.dumps(assertions, ensure_ascii=False, sort_keys=True)}\n"
        f"Output: {serialized}\n\n"
        "Return JSON with keys: score, summary, evidence.\n"
        "Evidence must be exact snippets copied from the output.\n"
    )

    try:
        raw = await llm_fn(
            prompt,
            system="Return JSON only.",
            temperature=0.0,
            max_tokens=500,
        )
        payload = json.loads(raw)
        evidence = [
            str(item).strip()
            for item in ((payload.get("evidence") or payload.get("evidence_quotes")) or [])
            if str(item).strip()
        ]
        valid, _missing = validate_evidence_quotes(serialized, evidence)
        if evidence and not valid:
            return JudgeResult(
                status="invalid_evidence",
                score=None,
                summary=str(payload.get("summary") or ""),
                evidence=evidence,
                error="Judge evidence was not found in serialized output",
            )

        score = payload.get("score")
        return JudgeResult(
            status="scored",
            score=float(score) if score is not None else None,
            summary=str(payload.get("summary") or ""),
            evidence=evidence,
        )
    except Exception as exc:
        return JudgeResult(
            status="error",
            score=None,
            summary="",
            evidence=[],
            error=str(exc),
        )
