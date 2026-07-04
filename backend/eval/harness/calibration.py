from __future__ import annotations

import json
from pathlib import Path


def _load_rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def cohens_kappa(left: list[str], right: list[str]) -> float:
    labels = sorted(set(left) | set(right))
    total = len(left)
    if total == 0:
        return 0.0
    observed = sum(1 for a, b in zip(left, right) if a == b) / total
    expected = 0.0
    for label in labels:
        left_rate = sum(1 for item in left if item == label) / total
        right_rate = sum(1 for item in right if item == label) / total
        expected += left_rate * right_rate
    if expected == 1.0:
        return 1.0
    return round((observed - expected) / (1 - expected), 4)


def build_calibration_report(path: Path, *, threshold: float = 0.6) -> dict:
    rows = _load_rows(path)
    by_case: dict[str, dict[str, str]] = {}
    for row in rows:
        by_case.setdefault(row["case_id"], {})[row["rater"]] = row["label"]

    paired = [
        raters
        for raters in by_case.values()
        if len(raters) >= 2
    ]
    left = [sorted(item.items())[0][1] for item in paired]
    right = [sorted(item.items())[1][1] for item in paired]
    kappa = cohens_kappa(left, right)

    lines = [
        "# Judge Calibration Report",
        "",
        f"- case_count: {len(by_case)}",
        f"- paired_case_count: {len(paired)}",
        f"- kappa: {kappa:.4f}",
        f"- threshold: {threshold:.2f}",
        f"- threshold_met: {'yes' if kappa >= threshold else 'no'}",
    ]
    return {
        "summary": {
            "case_count": len(by_case),
            "paired_case_count": len(paired),
            "kappa": kappa,
            "threshold_met": kappa >= threshold,
        },
        "markdown": "\n".join(lines) + "\n",
    }
