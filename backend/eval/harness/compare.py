from __future__ import annotations

import json
from pathlib import Path


def build_compare_summary(baseline_path: Path, candidate_path: Path) -> dict:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    baseline_cases = {row["case_id"]: row for row in baseline.get("cases", [])}
    candidate_cases = {row["case_id"]: row for row in candidate.get("cases", [])}

    new_failures: list[str] = []
    score_regressions: list[str] = []
    latency_regressions: list[str] = []

    for case_id, candidate_row in candidate_cases.items():
        baseline_row = baseline_cases.get(case_id)
        if baseline_row is None:
            continue

        if baseline_row.get("status") == "passed" and candidate_row.get("status") != "passed":
            new_failures.append(case_id)

        baseline_score = baseline_row.get("judge_score")
        candidate_score = candidate_row.get("judge_score")
        if baseline_score is not None and candidate_score is not None and candidate_score < baseline_score - 0.05:
            score_regressions.append(f"{case_id}: {baseline_score:.2f} -> {candidate_score:.2f}")

        baseline_latency = baseline_row.get("latency_ms")
        candidate_latency = candidate_row.get("latency_ms")
        if (
            isinstance(baseline_latency, (int, float))
            and isinstance(candidate_latency, (int, float))
            and candidate_latency > max(baseline_latency * 1.25, baseline_latency + 200)
        ):
            latency_regressions.append(f"{case_id}: {baseline_latency}ms -> {candidate_latency}ms")

    return {
        "new_failures": new_failures,
        "score_regressions": score_regressions,
        "latency_regressions": latency_regressions,
        "exit_code": 1 if new_failures or score_regressions else 0,
    }


def compare_baseline_files(baseline_path: Path, candidate_path: Path) -> tuple[str, int]:
    summary = build_compare_summary(baseline_path, candidate_path)
    lines = ["# Compare Report", "", "## New failures"]
    lines.extend(summary["new_failures"] or ["- none"])
    lines.extend(["", "## Score regressions"])
    lines.extend(summary["score_regressions"] or ["- none"])
    lines.extend(["", "## Latency regressions"])
    lines.extend(summary["latency_regressions"] or ["- none"])
    return "\n".join(lines), summary["exit_code"]
