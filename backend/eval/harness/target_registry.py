from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TargetSpec:
    call_site: str
    runner: str
    phase: str
    default_rubric: str


_UNIT_TARGETS: dict[str, TargetSpec] = {
    "depth_evaluator.evaluate": TargetSpec(
        call_site="depth_evaluator.evaluate",
        runner="builtin:depth_evaluator.evaluate",
        phase="elicitation",
        default_rubric="depth_quality_v1",
    ),
    "outcome_extractor.extract": TargetSpec(
        call_site="outcome_extractor.extract",
        runner="builtin:outcome_extractor.extract",
        phase="elicitation",
        default_rubric="elicitation_structure_v1",
    ),
    "complexity_evaluator.evaluate": TargetSpec(
        call_site="complexity_evaluator.evaluate",
        runner="builtin:complexity_evaluator.evaluate",
        phase="complexity",
        default_rubric="complexity_quality_v1",
    ),
    "round_evaluator.extract_tension_map": TargetSpec(
        call_site="round_evaluator.extract_tension_map",
        runner="builtin:round_evaluator.extract_tension_map",
        phase="debate",
        default_rubric="debate_artifact_v1",
    ),
    "synthesis.generate_synthesis_enhanced": TargetSpec(
        call_site="synthesis.generate_synthesis_enhanced",
        runner="builtin:synthesis.generate_synthesis_enhanced",
        phase="synthesis",
        default_rubric="synthesis_quality_v1",
    ),
    "agent_mapper.map_voices": TargetSpec(
        call_site="agent_mapper.map_voices",
        runner="eval.targets.identity_reflection.run_agent_mapper_case",
        phase="debate_setup",
        default_rubric="identity_mapping_v1",
    ),
    "portrait_language_refiner.refine": TargetSpec(
        call_site="portrait_language_refiner.refine",
        runner="eval.targets.identity_reflection.run_portrait_language_refiner_case",
        phase="portrait",
        default_rubric="portrait_refinement_v1",
    ),
    "reflection_dialogue.respond_dialogue": TargetSpec(
        call_site="reflection_dialogue.respond_dialogue",
        runner="eval.targets.identity_reflection.run_reflection_dialogue_case",
        phase="reflection",
        default_rubric="reflection_followup_v1",
    ),
    "consistency_monitor.evaluate_async": TargetSpec(
        call_site="consistency_monitor.evaluate_async",
        runner="eval.targets.debate_quality.run_consistency_case",
        phase="debate",
        default_rubric="consistency_alignment_v1",
    ),
    "round_evaluator.extract_position_map": TargetSpec(
        call_site="round_evaluator.extract_position_map",
        runner="eval.targets.debate_quality.run_round_position_case",
        phase="debate",
        default_rubric="debate_artifact_v1",
    ),
    "round_evaluator.evaluate_engagement": TargetSpec(
        call_site="round_evaluator.evaluate_engagement",
        runner="eval.targets.debate_quality.run_round_engagement_case",
        phase="debate",
        default_rubric="debate_artifact_v1",
    ),
    "round_evaluator.extract_convergence_map": TargetSpec(
        call_site="round_evaluator.extract_convergence_map",
        runner="eval.targets.debate_quality.run_round_convergence_case",
        phase="debate",
        default_rubric="debate_artifact_v1",
    ),
}


def resolve_unit_target(call_site: str) -> TargetSpec:
    try:
        return _UNIT_TARGETS[call_site]
    except KeyError as exc:
        raise ValueError(f"Unsupported unit target: {call_site}") from exc
