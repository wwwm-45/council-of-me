from __future__ import annotations

import json
from pathlib import Path


def freeze_baseline(*, reports_root: Path, run_id: str) -> Path:
    run_dir = reports_root / "runs" / run_id
    results = [
        json.loads(line)
        for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    baseline = {
        "run_id": run_id,
        "cases": [
            {
                "case_id": row.get("case_id") or row.get("scenario_id"),
                "status": row["status"],
                "judge_score": row.get("judge_score", (row.get("judge_result") or {}).get("score")),
                "latency_ms": row.get("latency_ms", (row.get("latency_summary") or {}).get("total_latency_ms")),
            }
            for row in results
        ],
    }
    output_path = reports_root / "baselines" / f"{run_id}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
