from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from eval.harness.target_registry import resolve_unit_target

SUPPORTED_SCENARIO_PIPELINES = {"artifact_chain_v1"}
SUPPORTED_SOURCE_KINDS = {
    "session_distilled",
    "handwritten_edge",
    "adversarial",
}


class FixtureValidationError(ValueError):
    pass


@dataclass(frozen=True)
class UnitCase:
    case_id: str
    target: str
    fixture_ref: str
    mode: str
    judge_enabled: bool
    source_kind: str
    coverage_tags: list[str]
    risk_tags: list[str]
    judge_rubric: str | None
    assertions: dict[str, Any]
    budgets: dict[str, Any]


@dataclass(frozen=True)
class ScenarioCase:
    scenario_id: str
    pipeline: str
    fixture_dir: str
    mode: str
    source_kind: str
    coverage_tags: list[str]
    risk_tags: list[str]
    gates: dict[str, Any]
    budgets: dict[str, Any]


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise FixtureValidationError(f"{path} must contain a mapping")
    return payload


def load_unit_case(path: Path) -> UnitCase:
    payload = _load_yaml(path)
    target = str(payload.get("target") or "")
    resolve_unit_target(target)
    source_kind = str(payload.get("source_kind") or "")
    if source_kind not in SUPPORTED_SOURCE_KINDS:
        raise FixtureValidationError(f"{path} missing supported source_kind")
    coverage_tags = [str(item) for item in (payload.get("coverage_tags") or []) if str(item)]
    risk_tags = [str(item) for item in (payload.get("risk_tags") or []) if str(item)]
    if not coverage_tags:
        raise FixtureValidationError(f"{path} must declare coverage_tags")
    return UnitCase(
        case_id=str(payload.get("case_id") or ""),
        target=target,
        fixture_ref=str(payload.get("fixture_ref") or ""),
        mode=str(payload.get("mode") or "live"),
        judge_enabled=bool(payload.get("judge_enabled", False)),
        source_kind=source_kind,
        coverage_tags=coverage_tags,
        risk_tags=risk_tags,
        judge_rubric=str(payload.get("judge_rubric") or "") or None,
        assertions=dict(payload.get("assertions") or {}),
        budgets=dict(payload.get("budgets") or {}),
    )


def load_scenario_case(path: Path) -> ScenarioCase:
    payload = _load_yaml(path)
    pipeline = str(payload.get("pipeline") or "")
    if pipeline not in SUPPORTED_SCENARIO_PIPELINES:
        raise FixtureValidationError(f"Unsupported scenario pipeline: {pipeline}")
    source_kind = str(payload.get("source_kind") or "")
    if source_kind not in SUPPORTED_SOURCE_KINDS:
        raise FixtureValidationError(f"{path} missing supported source_kind")
    coverage_tags = [str(item) for item in (payload.get("coverage_tags") or []) if str(item)]
    risk_tags = [str(item) for item in (payload.get("risk_tags") or []) if str(item)]
    if not coverage_tags:
        raise FixtureValidationError(f"{path} must declare coverage_tags")
    return ScenarioCase(
        scenario_id=str(payload.get("scenario_id") or ""),
        pipeline=pipeline,
        fixture_dir=str(payload.get("fixture_dir") or ""),
        mode=str(payload.get("mode") or "live"),
        source_kind=source_kind,
        coverage_tags=coverage_tags,
        risk_tags=risk_tags,
        gates=dict(payload.get("gates") or {}),
        budgets=dict(payload.get("budgets") or {}),
    )


def load_unit_fixture(case: UnitCase, path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("kind") != case.target:
        raise FixtureValidationError(
            f"Fixture kind {payload.get('kind')} does not match unit target {case.target}"
        )
    return payload
