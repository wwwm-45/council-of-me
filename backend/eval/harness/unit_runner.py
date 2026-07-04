from __future__ import annotations

from importlib import import_module
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.models.elicitation import DepthEvaluation, ElicitationOutcome
from app.services.complexity_evaluator import ComplexityEvaluator
from app.services.debate.model_router import ModelRouter
from app.services.debate.round_evaluator import RoundEvaluator
from app.services.depth_evaluator import DepthEvaluator
from app.services.outcome_extractor import OutcomeExtractor
from app.services.synthesis import generate_synthesis_enhanced
from eval.harness.fixture_loader import load_unit_case, load_unit_fixture
from eval.harness.judge import JudgeLlm, run_minimal_judge
from eval.harness.observer import capture_llm_records
from eval.harness.replay_backend import ReplayBackend
from eval.harness.target_registry import resolve_unit_target
from eval.harness.trace_context import EvalTraceContext, bind_eval_trace


class _PassthroughRefiner:
    async def refine(self, outcome, **kwargs):
        class _Result:
            def __init__(self, value):
                self.outcome = value

        return _Result(outcome)


class _ImmediateTask:
    def __init__(self, coro):
        self._coro = coro

    def __await__(self):
        return self._coro.__await__()


class UnitRunner:
    def __init__(
        self,
        *,
        case_root: Path,
        replay_root: Path,
        reports_root: Path,
        judge_llm: JudgeLlm | None = None,
    ) -> None:
        self._case_root = case_root
        self._replay_root = replay_root
        self._reports_root = reports_root
        self._judge_llm = judge_llm

    @asynccontextmanager
    async def _replay_backend(self, responses: list[dict]):
        yield ReplayBackend(responses)

    @staticmethod
    def _coerce_depth_evaluations(items: list[object]) -> list[DepthEvaluation]:
        evaluations: list[DepthEvaluation] = []
        for item in items:
            if isinstance(item, DepthEvaluation):
                evaluations.append(item)
            elif isinstance(item, dict):
                evaluations.append(DepthEvaluation.from_dict(item))
        return evaluations

    @staticmethod
    def _load_adapter(runner_path: str):
        module_name, func_name = runner_path.rsplit(".", 1)
        module = import_module(module_name)
        return getattr(module, func_name)

    async def _execute_target(self, target: str, fixture: dict, replay: ReplayBackend) -> dict[str, Any]:
        spec = resolve_unit_target(target)
        if not spec.runner.startswith("builtin:"):
            adapter = self._load_adapter(spec.runner)
            return await adapter(fixture)

        kwargs = dict(fixture.get("kwargs") or {})

        if target == "depth_evaluator.evaluate":
            kwargs["previous_evaluations"] = self._coerce_depth_evaluations(
                list(kwargs.get("previous_evaluations") or [])
            )
            return (await DepthEvaluator(llm_fn=replay.generate).evaluate(**kwargs)).to_dict()

        if target == "outcome_extractor.extract":
            kwargs["depth_evaluations"] = self._coerce_depth_evaluations(
                list(kwargs.get("depth_evaluations") or [])
            )
            return (
                await OutcomeExtractor(
                    llm_fn=replay.generate,
                    refiner=_PassthroughRefiner(),
                ).extract(**kwargs)
            ).to_dict()

        if target == "complexity_evaluator.evaluate":
            with patch("app.services.complexity_evaluator.generate", replay.generate):
                outcome = ElicitationOutcome.from_dict(dict(kwargs["outcome"]))
                return (await ComplexityEvaluator().evaluate(outcome)).to_dict()

        if target == "round_evaluator.extract_tension_map":
            router = ModelRouter("replay-primary", "replay-aux", generate_fn=replay.generate)
            return (await RoundEvaluator(router=router).extract_tension_map(**kwargs)).to_dict()

        if target == "synthesis.generate_synthesis_enhanced":
            with patch("app.services.synthesis.llm_generate", replay.generate):
                with patch("app.services.synthesis.asyncio.create_task", lambda coro: _ImmediateTask(coro)):
                    return await generate_synthesis_enhanced(**kwargs)

        raise ValueError(f"Unsupported target: {target}")

    def _resolve_value(self, output: dict[str, Any], path: str) -> Any:
        current: Any = output
        for part in path.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current

    def _evaluate_assertions(self, *, case_assertions: dict[str, Any], output: dict[str, Any]) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        for key, expected in case_assertions.items():
            if key.endswith("_eq"):
                field = key[: -len("_eq")]
                actual = self._resolve_value(output, field)
                passed = actual == expected
            elif key.endswith("_in"):
                field = key[: -len("_in")]
                actual = self._resolve_value(output, field)
                passed = actual in list(expected)
            elif key.endswith("_contains"):
                field = key[: -len("_contains")]
                actual = str(self._resolve_value(output, field) or "")
                passed = str(expected) in actual
            elif key.endswith("_nonempty"):
                field = key[: -len("_nonempty")]
                actual = self._resolve_value(output, field)
                passed = bool(str(actual or "").strip())
            elif key.endswith("_min"):
                field = key[: -len("_min")]
                actual = self._resolve_value(output, field)
                if isinstance(actual, list):
                    actual_value = len(actual)
                elif actual is None:
                    actual_value = 0
                else:
                    actual_value = actual
                passed = actual_value >= expected
                actual = actual_value
            else:
                raise ValueError(f"Unsupported assertion key: {key}")

            results[key] = {
                "passed": passed,
                "expected": expected,
                "actual": actual,
            }
        return results

    def _write_manifest(
        self,
        run_dir: Path,
        *,
        run_id: str,
        kind: str,
        case_names: list[str],
        mock_llm: bool,
        judge_config: dict | None = None,
    ) -> None:
        payload = {
            "run_id": run_id,
            "kind": kind,
            "case_names": case_names,
            "mock_llm": mock_llm,
        }
        if judge_config:
            payload["judge"] = {
                "judge_model": judge_config.get("judge_model"),
                "sample_count": judge_config.get("sample_count", 1),
            }
        (run_dir / "manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _write_summary(self, run_dir: Path, rows: list[dict[str, Any]]) -> None:
        passed = sum(1 for row in rows if row["status"] == "passed")
        total = len(rows)
        lines = [
            "# Eval Summary",
            "",
            f"- passed: {passed}/{total}",
        ]
        for row in rows:
            case_id = row.get("case_id") or row.get("scenario_id")
            lines.append(f"- {case_id}: {row['status']}")
        (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    async def run_case(self, case_path: Path, *, mock_llm: bool, judge_config: dict | None = None) -> dict[str, Any]:
        case = load_unit_case(case_path)
        fixture = load_unit_fixture(case, self._replay_root / case.fixture_ref)
        run_id = f"unit_{uuid.uuid4().hex[:8]}"
        run_dir = self._reports_root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        async with self._replay_backend(fixture.get("replay", [])) as replay:
            records = []
            with capture_llm_records(records):
                with bind_eval_trace(
                    EvalTraceContext(
                        run_id=run_id,
                        case_id=case.case_id,
                        scenario_id=None,
                        call_site=case.target,
                        stage=None,
                        is_judge=False,
                        replay_mode=mock_llm,
                    )
                ):
                    output = await self._execute_target(case.target, fixture, replay)

                judge_result = None
                if case.judge_enabled and self._judge_llm is not None:
                    with bind_eval_trace(
                        EvalTraceContext(
                            run_id=run_id,
                            case_id=case.case_id,
                            scenario_id=None,
                            call_site=case.target,
                            stage="judge",
                            is_judge=True,
                            replay_mode=mock_llm,
                        )
                    ):
                        judge_result = (
                            await run_minimal_judge(
                                target=case.target,
                                output=output,
                                assertions=case.assertions,
                                llm_fn=self._judge_llm,
                            )
                        ).to_dict()

        assertion_results = self._evaluate_assertions(case_assertions=case.assertions, output=output)
        status = "passed" if all(item["passed"] for item in assertion_results.values()) else "failed"
        total_latency_ms = sum(record.latency_ms for record in records)
        total_cost_usd = round(sum(float(record.cost_usd or 0.0) for record in records), 4)
        (run_dir / "raw.jsonl").write_text(
            "".join(json.dumps(record.__dict__, ensure_ascii=False) + "\n" for record in records),
            encoding="utf-8",
        )
        result_row = {
            "run_id": run_id,
            "case_id": case.case_id,
            "call_site": case.target,
            "target": case.target,
            "source_kind": case.source_kind,
            "coverage_tags": case.coverage_tags,
            "status": status,
            "judge_score": (judge_result or {}).get("score") if judge_result is not None else None,
            "latency_ms": total_latency_ms,
            "cost_usd": total_cost_usd,
            "assertion_results": assertion_results,
            "output": output,
        }
        if judge_result is not None:
            result_row["judge_result"] = judge_result
        (run_dir / "results.jsonl").write_text(
            json.dumps(result_row, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return result_row

    def _select_case_paths(self, *, case_names: list[str] | None, tags: list[str] | None) -> list[Path]:
        wanted = {name if name.endswith(".yaml") else f"{name}.yaml" for name in (case_names or [])}
        selected: list[Path] = []
        for path in sorted(self._case_root.glob("*.yaml")):
            case = load_unit_case(path)
            if wanted and path.name not in wanted:
                continue
            if tags and not set(tags).issubset(set(case.coverage_tags)):
                continue
            selected.append(path)
        return selected

    async def run_suite(
        self,
        *,
        case_names: list[str] | None,
        tags: list[str] | None = None,
        mock_llm: bool,
        judge_config: dict | None = None,
    ) -> dict[str, Any]:
        run_id = f"unit_suite_{uuid.uuid4().hex[:8]}"
        run_dir = self._reports_root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        case_paths = self._select_case_paths(case_names=case_names, tags=tags)

        rows: list[dict[str, Any]] = []
        raw_lines: list[str] = []
        for case_path in case_paths:
            row = await self.run_case(case_path, mock_llm=mock_llm, judge_config=judge_config)
            rows.append(row)
            child_dir = self._reports_root / "runs" / row["run_id"]
            raw_path = child_dir / "raw.jsonl"
            if raw_path.exists():
                raw_lines.extend(
                    line
                    for line in raw_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                )

        (run_dir / "results.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        (run_dir / "raw.jsonl").write_text(
            "".join(line + "\n" for line in raw_lines),
            encoding="utf-8",
        )

        self._write_manifest(
            run_dir,
            run_id=run_id,
            kind="unit",
            case_names=[path.stem for path in case_paths],
            mock_llm=mock_llm,
            judge_config=judge_config,
        )
        self._write_summary(run_dir, rows)
        return {
            "run_id": run_id,
            "status": "passed" if all(row["status"] == "passed" for row in rows) else "failed",
            "selected_case_names": [path.stem for path in case_paths],
        }
