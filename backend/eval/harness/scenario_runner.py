from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.models.elicitation import ElicitationOutcome
from app.services.complexity_evaluator import ComplexityEvaluator
from app.services.conflict_profile import ConflictProfileGenerator
from app.services.debate.model_router import ModelRouter
from app.services.debate.round_evaluator import RoundEvaluator
from app.services.synthesis import generate_synthesis_enhanced
from eval.harness.fixture_loader import load_scenario_case
from eval.harness.observer import capture_llm_records
from eval.harness.replay_backend import ReplayBackend
from eval.harness.trace_context import EvalTraceContext, bind_eval_trace
from eval.harness.unit_runner import _ImmediateTask


class ScenarioRunner:
    def __init__(self, *, case_root: Path, fixture_root: Path, reports_root: Path) -> None:
        self._case_root = case_root
        self._fixture_root = fixture_root
        self._reports_root = reports_root

    @staticmethod
    def _load_json(path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    def _evaluate_gates(
        self,
        gates: dict[str, Any],
        *,
        tension_map: dict[str, Any],
        synthesis: dict[str, Any],
        convergence: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}

        debate_cfg = dict(gates.get("debate") or {})
        if "tensions_min" in debate_cfg:
            actual = len(tension_map.get("tensions") or [])
            expected = debate_cfg["tensions_min"]
            results["debate.tensions_min"] = {
                "passed": actual >= expected,
                "expected": expected,
                "actual": actual,
            }

        synthesis_cfg = dict(gates.get("synthesis") or {})
        if synthesis_cfg.get("narrative_nonempty"):
            actual = bool(str(synthesis.get("narrative") or "").strip())
            results["synthesis.narrative_nonempty"] = {
                "passed": actual,
                "expected": True,
                "actual": actual,
            }

        convergence_cfg = dict(gates.get("convergence") or {})
        if "irreducible_differences_min" in convergence_cfg:
            actual = len(convergence.get("irreducible_differences") or [])
            expected = convergence_cfg["irreducible_differences_min"]
            results["convergence.irreducible_differences_min"] = {
                "passed": actual >= expected,
                "expected": expected,
                "actual": actual,
            }

        return results

    async def run_case(self, case_path: Path, *, mock_llm: bool, judge_config: dict | None = None) -> dict[str, Any]:
        case = load_scenario_case(case_path)
        fixture_dir = self._fixture_root / case.fixture_dir
        run_id = f"scenario_{uuid.uuid4().hex[:8]}"
        run_dir = self._reports_root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        outcome = ElicitationOutcome.from_dict(self._load_json(fixture_dir / "elicitation_outcome.json"))
        profile = self._load_json(fixture_dir / "conflict_profile.json") or ConflictProfileGenerator().generate_from_outcome(outcome)
        statements = self._load_json(fixture_dir / "debate_statements.json")
        debate_artifacts_seed = dict(self._load_json(fixture_dir / "debate_artifacts.json") or {})
        reflection_statements = list(debate_artifacts_seed.pop("reflection_statements", []) or statements)
        full_history = list(debate_artifacts_seed.pop("full_history", []) or statements)
        replay_items = self._load_json(fixture_dir / "replay.json") if mock_llm else []

        records = []
        replay = ReplayBackend(replay_items)
        with capture_llm_records(records):
            with bind_eval_trace(
                EvalTraceContext(
                    run_id=run_id,
                    case_id=None,
                    scenario_id=case.scenario_id,
                    call_site="complexity_evaluator.evaluate",
                    stage=None,
                    is_judge=False,
                    replay_mode=mock_llm,
                )
            ):
                with patch("app.services.complexity_evaluator.generate", replay.generate):
                    complexity = await ComplexityEvaluator().evaluate(outcome)

            profile = {
                **profile,
                "debate_level": complexity.level,
                "agent_count": complexity.agent_count,
                "max_rounds": complexity.max_rounds,
                "complexity_narrative": complexity.narrative,
            }

            with bind_eval_trace(
                EvalTraceContext(
                    run_id=run_id,
                    case_id=None,
                    scenario_id=case.scenario_id,
                    call_site="round_evaluator.extract_tension_map",
                    stage=None,
                    is_judge=False,
                    replay_mode=mock_llm,
                )
            ):
                router = ModelRouter("replay-primary", "replay-aux", generate_fn=replay.generate)
                tension_map = await RoundEvaluator(router=router).extract_tension_map(statements)

            enriched_artifacts = {
                **debate_artifacts_seed,
                "tension_map": tension_map.to_dict(),
                "complexity": complexity.to_dict(),
            }

            with bind_eval_trace(
                EvalTraceContext(
                    run_id=run_id,
                    case_id=None,
                    scenario_id=case.scenario_id,
                    call_site="synthesis.generate_synthesis_enhanced",
                    stage=None,
                    is_judge=False,
                    replay_mode=mock_llm,
                )
            ):
                with patch("app.services.synthesis.llm_generate", replay.generate):
                    with patch("app.services.synthesis.asyncio.create_task", lambda coro: _ImmediateTask(coro)):
                        synthesis = await generate_synthesis_enhanced(
                            statements=statements,
                            profile=profile,
                            debate_artifacts=enriched_artifacts,
                        )

            convergence = None
            if case.gates.get("convergence"):
                with bind_eval_trace(
                    EvalTraceContext(
                        run_id=run_id,
                        case_id=None,
                        scenario_id=case.scenario_id,
                        call_site="round_evaluator.extract_convergence_map",
                        stage=None,
                        is_judge=False,
                        replay_mode=mock_llm,
                    )
                ):
                    router = ModelRouter("replay-primary", "replay-aux", generate_fn=replay.generate)
                    convergence = await RoundEvaluator(router=router).extract_convergence_map(
                        reflections=reflection_statements,
                        full_history=full_history,
                    )

        gate_results = self._evaluate_gates(
            case.gates,
            tension_map=tension_map.to_dict(),
            synthesis=synthesis,
            convergence=convergence.to_dict() if convergence is not None else {},
        )
        total_latency_ms = sum(record.latency_ms for record in records)
        total_cost_usd = round(sum(float(record.cost_usd or 0.0) for record in records), 4)
        artifacts = {
            "complexity": complexity.to_dict(),
            "tension_map": tension_map.to_dict(),
            "synthesis": synthesis,
        }
        if convergence is not None:
            artifacts["convergence"] = convergence.to_dict()

        result_row = {
            "run_id": run_id,
            "scenario_id": case.scenario_id,
            "pipeline": case.pipeline,
            "source_kind": case.source_kind,
            "coverage_tags": case.coverage_tags,
            "status": "passed" if all(item["passed"] for item in gate_results.values()) else "failed",
            "latency_ms": total_latency_ms,
            "cost_usd": total_cost_usd,
            "gate_results": gate_results,
            "artifacts": artifacts,
        }
        (run_dir / "raw.jsonl").write_text(
            "".join(json.dumps(record.__dict__, ensure_ascii=False) + "\n" for record in records),
            encoding="utf-8",
        )
        (run_dir / "results.jsonl").write_text(
            json.dumps(result_row, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (run_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "kind": "scenario",
                    "case_names": [case.scenario_id],
                    "mock_llm": mock_llm,
                    "judge": judge_config or {},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (run_dir / "summary.md").write_text(
            "\n".join(
                [
                    "# Eval Summary",
                    "",
                    f"- {case.scenario_id}: {result_row['status']}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return result_row
