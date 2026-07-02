"""
Phase 5: Synthesis - consensus map or polyphonic landscape from debate statements.
Provides both heuristic (sync) and LLM-enhanced (async) synthesis generation.
"""
import asyncio
import json
import logging
import uuid
from typing import Any, AsyncGenerator, Callable

from app import config
from app.services.language_guard import (
    chinese_system_prompt,
    find_low_chinese_fields,
    record_failure,
    record_retry,
)
from app.services.llm import generate as llm_generate, is_llm_error

logger = logging.getLogger(__name__)

_JSON_SYSTEM_PROMPT = chinese_system_prompt("Return JSON only.")
_NARRATIVE_SYSTEM_PROMPT = chinese_system_prompt(
    "Write the final user-facing synthesis in Chinese."
)
_RETRY_SUFFIX = (
    "\n\nThe previous answer included whole English passages. Regenerate the full "
    "response and ensure all user-facing text is Chinese, keeping only "
    "unavoidable English proper nouns."
)


def _strip_markdown_fences(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _parse_json_list(raw: str) -> list[dict[str, Any]] | None:
    text = _strip_markdown_fences(raw)
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    if not isinstance(parsed, list):
        return None
    return [item for item in parsed if isinstance(item, dict)]


def _count_cjk_characters(value: str) -> int:
    return sum(1 for ch in value if "\u4e00" <= ch <= "\u9fff")


def _better_text_candidate(initial: str, retry: str) -> str:
    initial_score = (_count_cjk_characters(initial), len(initial.strip()))
    retry_score = (_count_cjk_characters(retry), len(retry.strip()))
    return retry if retry_score > initial_score else initial


def _collect_core_tension_language_failures(items: list[dict[str, Any]]) -> list[str]:
    fields: list[tuple[str, str]] = []
    for index, item in enumerate(items):
        fields.extend(
            [
                (f"core_tensions[{index}].name", item.get("name", "")),
                (f"core_tensions[{index}].pole_a_label", item.get("pole_a_label", "")),
                (f"core_tensions[{index}].pole_a_stance", item.get("pole_a_stance", "")),
                (f"core_tensions[{index}].pole_b_label", item.get("pole_b_label", "")),
                (f"core_tensions[{index}].pole_b_stance", item.get("pole_b_stance", "")),
                (f"core_tensions[{index}].value_a", item.get("value_a", "")),
                (f"core_tensions[{index}].value_b", item.get("value_b", "")),
            ]
        )
    return find_low_chinese_fields(fields)


def _collect_protective_intent_language_failures(items: list[dict[str, Any]]) -> list[str]:
    fields: list[tuple[str, str]] = []
    for index, item in enumerate(items):
        fields.extend(
            [
                (f"protective_intents[{index}].intent", item.get("intent", "")),
                (
                    f"protective_intents[{index}].what_it_protects",
                    item.get("what_it_protects", ""),
                ),
                (
                    f"protective_intents[{index}].underlying_value",
                    item.get("underlying_value", ""),
                ),
            ]
        )
    return find_low_chinese_fields(fields)


def _collect_consensus_language_failures(items: list[dict[str, Any]]) -> list[str]:
    fields: list[tuple[str, str]] = []
    for index, item in enumerate(items):
        fields.append((f"consensus_areas[{index}].description", item.get("description", "")))
        for evidence_index, evidence in enumerate(item.get("evidence", [])):
            fields.append(
                (f"consensus_areas[{index}].evidence[{evidence_index}]", str(evidence or ""))
            )
    return find_low_chinese_fields(fields)


async def _generate_json_list_with_language_retry(
    *,
    stage_name: str,
    prompt: str,
    temperature: float,
    collect_failures: Callable[[list[dict[str, Any]]], list[str]],
) -> list[dict[str, Any]] | None:
    try:
        raw = await llm_generate(
            prompt,
            system=_JSON_SYSTEM_PROMPT,
            temperature=temperature,
        )
    except Exception as e:
        logger.warning("Failed to %s: %s", stage_name, e)
        return None

    if is_llm_error(raw):
        logger.warning("%s: LLM returned error sentinel", stage_name)
        return None

    parsed = _parse_json_list(raw)
    if parsed is None:
        logger.warning("%s: invalid JSON list", stage_name)
        return None

    initial_failures = collect_failures(parsed)
    if not initial_failures:
        return parsed

    record_retry()
    try:
        retry_raw = await llm_generate(
            prompt + _RETRY_SUFFIX,
            system=_JSON_SYSTEM_PROMPT,
            temperature=temperature,
        )
    except Exception:
        logger.warning(
            "language_guard_warning: %s retry failed for %s",
            stage_name,
            ", ".join(initial_failures),
        )
        record_failure()
        return parsed

    if is_llm_error(retry_raw):
        logger.warning(
            "language_guard_warning: %s retry returned error sentinel for %s",
            stage_name,
            ", ".join(initial_failures),
        )
        record_failure()
        return parsed

    retry_parsed = _parse_json_list(retry_raw)
    if retry_parsed is None:
        logger.warning(
            "language_guard_warning: %s retry returned invalid JSON for %s",
            stage_name,
            ", ".join(initial_failures),
        )
        record_failure()
        return parsed

    retry_failures = collect_failures(retry_parsed)
    if not retry_failures:
        return retry_parsed

    best = retry_parsed if len(retry_failures) < len(initial_failures) else parsed
    logger.warning(
        "language_guard_warning: %s fields still English-heavy after retry; keeping best parsed result (%s)",
        stage_name,
        ", ".join(retry_failures),
    )
    record_failure()
    return best


async def _generate_text_with_language_retry(
    *,
    stage_name: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    timeout: int | None = None,
) -> str:
    kwargs: dict[str, Any] = {
        "system": _NARRATIVE_SYSTEM_PROMPT,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if timeout is not None:
        kwargs["timeout"] = timeout

    try:
        raw = await llm_generate(prompt, **kwargs)
    except Exception as e:
        logger.warning("%s generation failed: %s", stage_name, e)
        return ""

    if not raw or is_llm_error(raw):
        return ""

    initial_text = raw.strip()
    initial_failures = find_low_chinese_fields([("narrative", initial_text)])
    if not initial_failures:
        return initial_text

    record_retry()
    try:
        retry_raw = await llm_generate(prompt + _RETRY_SUFFIX, **kwargs)
    except Exception:
        logger.warning(
            "language_guard_warning: %s retry failed for %s",
            stage_name,
            ", ".join(initial_failures),
        )
        record_failure()
        return initial_text

    if not retry_raw or is_llm_error(retry_raw):
        logger.warning(
            "language_guard_warning: %s retry returned empty/error output for %s",
            stage_name,
            ", ".join(initial_failures),
        )
        record_failure()
        return initial_text

    retry_text = retry_raw.strip()
    retry_failures = find_low_chinese_fields([("narrative", retry_text)])
    if not retry_failures:
        return retry_text

    logger.warning(
        "language_guard_warning: %s narrative still English-heavy after retry",
        stage_name,
    )
    record_failure()
    return _better_text_candidate(initial_text, retry_text)


async def _with_stage_retry(
    coro_factory,
    stage_name: str,
    max_retries: int = 1,
    fallback=None,
):
    """Retry a synthesis stage once on failure before falling back."""
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except Exception as e:
            logger.warning(
                "Synthesis stage '%s' failed (attempt %d/%d): %s",
                stage_name, attempt + 1, max_retries + 1, e,
            )
            if attempt < max_retries:
                await asyncio.sleep(1.0)
    return fallback if fallback is not None else []


# ---------------------------------------------------------------------------
# Heuristic helpers (used by both sync and async paths)
# ---------------------------------------------------------------------------

def _is_agent_statement(statement: dict) -> bool:
    return (
        statement.get("agent_id") != "__user__"
        and not statement.get("is_user_turn")
        and statement.get("type") != "user_turn"
        and statement.get("type") != "followup_questions"
        and not statement.get("is_intervention_response")
    )


def _agent_reference_allowlist(statements: list[dict]) -> set[str]:
    references: set[str] = set()
    for statement in statements:
        if not _is_agent_statement(statement):
            continue
        for key in ("agent_id", "agent_name"):
            value = statement.get(key)
            if isinstance(value, str) and value:
                references.add(value)
    return references


def _filter_agent_references(references: Any, allowlist: set[str]) -> list[str]:
    if not isinstance(references, list):
        return []
    return [
        item
        for item in references
        if isinstance(item, str) and item in allowlist
    ]


def _detect_convergence(statements: list[dict]) -> bool:
    """Heuristic: if last round statements share common words, consider converged."""
    agent_statements = [s for s in statements if _is_agent_statement(s)]
    if not agent_statements:
        return False
    max_round = max(s.get("round_number", 1) for s in agent_statements)
    last_stmts = [s for s in agent_statements if s.get("round_number") == max_round]
    if len(last_stmts) < 2:
        return False
    words_sets = [set(s.get("content", "").replace("。", " ").replace("，", " ").split()) for s in last_stmts]
    shared = words_sets[0]
    for ws in words_sets[1:]:
        shared = shared & ws
    overlap_ratio = len(shared) / max(len(words_sets[0]), 1)
    return overlap_ratio > 0.25


def _extract_consensus(statements: list[dict]) -> list[str]:
    """Extract points where agents agree (simple heuristic)."""
    agents: dict[str, list[str]] = {}
    for s in statements:
        if not _is_agent_statement(s):
            continue
        aid = s.get("agent_id", "")
        if aid not in agents:
            agents[aid] = []
        agents[aid].append(s.get("content", ""))
    if len(agents) < 2:
        return []
    all_content = " ".join(c for stmts in agents.values() for c in stmts)
    keywords = [w for w in all_content.replace("。", " ").replace("，", " ").split() if len(w) >= 2]
    from collections import Counter
    counts = Counter(keywords)
    return [w for w, c in counts.most_common(5) if c >= len(agents)]


def _distill_voice_positions(statements: list[dict]) -> list[dict]:
    """Group statements by agent, return last round stance."""
    agents: dict[str, dict] = {}
    for s in statements:
        if not _is_agent_statement(s):
            continue
        aid = s.get("agent_id", "")
        agents[aid] = {"agent_id": aid, "agent_name": s.get("agent_name", ""), "core_stance": s.get("content", "")[:150]}
    return list(agents.values())


def _core_tensions_from_tension_map(
    debate_artifacts: dict[str, Any] | None,
    statements: list[dict],
) -> list[dict[str, Any]]:
    """Convert the R2 tension_map artifact into synthesis core_tensions."""
    tension_map = (debate_artifacts or {}).get("tension_map") or {}
    if not isinstance(tension_map, dict):
        return []

    raw_tensions = tension_map.get("tensions") or []
    if not isinstance(raw_tensions, list):
        return []

    agent_names_by_id = {
        str(statement.get("agent_id")): str(
            statement.get("agent_name") or statement.get("agent_id")
        )
        for statement in statements
        if _is_agent_statement(statement) and statement.get("agent_id")
    }
    depth_to_intensity = {
        "surface": 0.45,
        "moderate": 0.65,
        "deep": 0.85,
    }

    core_tensions: list[dict[str, Any]] = []
    for raw in raw_tensions[:3]:
        if not isinstance(raw, dict):
            continue
        description = str(raw.get("description") or "").strip()
        sides = raw.get("sides") or {}
        if not description or not isinstance(sides, dict):
            continue

        side_items = [
            (str(agent_id), str(stance).strip())
            for agent_id, stance in sides.items()
            if str(agent_id).strip() and str(stance).strip()
        ]
        if len(side_items) < 2:
            continue

        (agent_a, stance_a), (agent_b, stance_b) = side_items[:2]
        name_a = agent_names_by_id.get(agent_a, agent_a)
        name_b = agent_names_by_id.get(agent_b, agent_b)
        depth = str(raw.get("depth") or "").lower()
        tension_id = raw.get("id")
        tension = {
            "tension_id": (
                str(tension_id) if tension_id is not None else str(uuid.uuid4())[:8]
            ),
            "name": description,
            "pole_a": {
                "label": name_a,
                "agents": [name_a],
                "stance": stance_a,
            },
            "pole_b": {
                "label": name_b,
                "agents": [name_b],
                "stance": stance_b,
            },
            "intensity": depth_to_intensity.get(depth, 0.65),
        }
        core_tensions.append(_attribute_tension_evidence(tension, statements))

    return core_tensions


# ---------------------------------------------------------------------------
# Enhanced synthesis helpers (Step 2-4: new algorithms)
# ---------------------------------------------------------------------------

async def _detect_convergence_semantic(statements: list[dict]) -> float:
    """Use LLM to detect semantic convergence between agent positions. Returns 0-1 score."""
    agent_statements = [s for s in statements if _is_agent_statement(s)]
    if not agent_statements:
        return 0.0

    max_round = max(s.get("round_number", 1) for s in agent_statements)
    last_stmts = [s for s in agent_statements if s.get("round_number") == max_round]

    if len(last_stmts) < 2:
        return 0.0

    # Build summary of last round positions
    positions = "\n".join(
        f"- {s.get('agent_name', 'Agent')}：{s.get('content', '')[:200]}"
        for s in last_stmts
    )

    prompt = (
        f"分析以下各方在辩论最后一轮的立场，判断它们在多大程度上趋于一致。\n\n"
        f"{positions}\n\n"
        f"请只返回一个0到1之间的数字（保留两位小数），表示收敛程度：\n"
        f"- 0.0 = 完全对立，立场截然不同\n"
        f"- 0.5 = 部分共识，但仍有显著分歧\n"
        f"- 1.0 = 高度一致，基本达成共识\n\n"
        f"只返回数字，不要解释。"
    )

    try:
        result = await llm_generate(prompt, temperature=0.3)
        score = float(result.strip())
        return max(0.0, min(1.0, score))  # Clamp to [0, 1]
    except Exception:
        # Fallback to heuristic
        return 0.5 if _detect_convergence(statements) else 0.2


def _detect_convergence_embedding(statements: list[dict]) -> float:
    """Fast embedding-based convergence: avg pairwise cosine of last-round statements."""
    agent_statements = [s for s in statements if _is_agent_statement(s)]
    if not agent_statements:
        return 0.0

    max_round = max(s.get("round_number", 1) for s in agent_statements)
    last_stmts = [s for s in agent_statements if s.get("round_number") == max_round]

    if len(last_stmts) < 2:
        return 0.0

    from app.services.embedding import EmbeddingService
    svc = EmbeddingService.get()
    texts = [s.get("content", "")[:300] for s in last_stmts]
    return svc.pairwise_similarity(texts)


async def _detect_convergence_combined(statements: list[dict]) -> float:
    """Combined convergence: embedding (0.4) + LLM (0.6) with short-circuit optimization.

    If embedding score is very low (<0.2) or very high (>0.8), skip expensive LLM call.
    """
    if not statements:
        return 0.0

    # Fast embedding check
    try:
        from app.services.embedding import EmbeddingService
        if EmbeddingService.get().is_real_model_loaded():
            emb_score = _detect_convergence_embedding(statements)

            # Short-circuit: confident embedding score
            if emb_score < 0.2:
                return emb_score
            if emb_score > 0.8:
                return emb_score

            # Mixed range: combine with LLM
            try:
                llm_score = await _detect_convergence_semantic(statements)
            except Exception:
                llm_score = emb_score  # Use embedding as sole signal

            return emb_score * 0.4 + llm_score * 0.6
    except Exception:
        pass

    # Fallback: LLM-only
    return await _detect_convergence_semantic(statements)


def _calculate_novelty_decay(statements: list[dict]) -> float:
    """Calculate information novelty in last round vs previous round. Returns 0-1 score.

    High score (close to 1.0) means lots of new information.
    Low score (close to 0.0) means debate is repeating itself.
    """
    agent_statements = [s for s in statements if _is_agent_statement(s)]
    if not agent_statements:
        return 0.0

    max_round = max(s.get("round_number", 1) for s in agent_statements)
    if max_round < 2:
        return 1.0  # First round always has novelty

    last_round = [s for s in agent_statements if s.get("round_number") == max_round]
    prev_round = [s for s in agent_statements if s.get("round_number") == max_round - 1]

    if not last_round or not prev_round:
        return 0.5

    # Build word sets for each round
    def _words(stmts: list[dict]) -> set[str]:
        text = " ".join(s.get("content", "") for s in stmts)
        return {w for w in text.replace("。", " ").replace("，", " ").replace("、", " ").split() if len(w) >= 2}

    prev_words = _words(prev_round)
    last_words = _words(last_round)

    if not last_words:
        return 0.0
    new_words = last_words - prev_words
    return len(new_words) / len(last_words)


def _analyze_value_conflict_intensity(
    statements: list[dict], profile: dict[str, Any],
) -> float:
    """Analyze how intense value conflicts are in the debate. Returns 0-1 score.

    High score means irreconcilable value conflicts; favors POLYPHONIC_LANDSCAPE.
    """
    value_conflicts = profile.get("value_conflicts") or []
    if not value_conflicts:
        return 0.5  # Unknown, neutral

    # Count how many distinct agents take strong positions
    agents: dict[str, str] = {}
    for s in statements:
        if not _is_agent_statement(s):
            continue
        aid = s.get("agent_id", "")
        # Keep last statement per agent (most refined position)
        agents[aid] = s.get("content", "")

    if len(agents) < 2:
        return 0.3

    # Check if value conflict keywords appear across different agents
    conflict_keywords: list[tuple[str, str]] = []
    for vc in value_conflicts[:3]:
        a = vc.get("value_a", "")
        b = vc.get("value_b", "")
        if a and b:
            conflict_keywords.append((a, b))

    if not conflict_keywords:
        return 0.5

    polarization_count = 0
    for val_a, val_b in conflict_keywords:
        agents_for_a = sum(1 for content in agents.values() if val_a in content)
        agents_for_b = sum(1 for content in agents.values() if val_b in content)
        if agents_for_a > 0 and agents_for_b > 0:
            polarization_count += 1

    return min(1.0, polarization_count / max(len(conflict_keywords), 1))


def _compute_round_progression(statements: list[dict]) -> list[dict[str, Any]]:
    """Compute per-round summary for timeline visualization."""
    debate_stmts = [s for s in statements if not s.get("is_intervention_response")]
    rounds: dict[int, list[dict]] = {}
    for s in debate_stmts:
        rn = s.get("round_number", 1)
        rounds.setdefault(rn, []).append(s)

    progression = []
    for rn in sorted(rounds.keys()):
        stmts = rounds[rn]
        agents = sorted({
            s.get("agent_name", "")
            for s in stmts
            if _is_agent_statement(s) and s.get("agent_name")
        })
        progression.append({
            "round": rn,
            "agent_count": len(agents),
            "statement_count": len(stmts),
            "agents": agents,
        })
    return progression


def _build_full_debate_text(statements: list[dict]) -> str:
    """Build a formatted text of all debate statements for LLM analysis."""
    debate_stmts = [s for s in statements if not s.get("is_intervention_response")]
    # Group by round
    rounds: dict[int, list[dict]] = {}
    for s in debate_stmts:
        rn = s.get("round_number", 1)
        rounds.setdefault(rn, []).append(s)

    parts: list[str] = []
    for rn in sorted(rounds.keys()):
        parts.append(f"【第{rn}轮】")
        for s in rounds[rn]:
            name = "我" if (
                s.get("agent_id") == "__user__"
                or s.get("is_user_turn")
                or s.get("type") == "user_turn"
            ) else s.get("agent_name", "Agent")
            content = s.get("content", "")[:300]
            parts.append(f"  {name}：{content}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Chinese-output enforcement overrides
# ---------------------------------------------------------------------------


async def _extract_core_tensions(
    statements: list[dict], profile: dict[str, Any],
) -> list[dict[str, Any]]:
    """Use LLM to identify core tensions (opposing dimensions) in the debate."""
    if not statements:
        return []

    debate_text = _build_full_debate_text(statements)
    value_conflicts = profile.get("value_conflicts") or []
    conflict_hint = ", ".join(
        f"{item.get('value_a', '')} vs {item.get('value_b', '')}"
        for item in value_conflicts[:3]
        if item.get("value_a") or item.get("value_b")
    )
    prompt = (
        "Analyze this debate and extract 2-3 core tensions.\n\n"
        f"Known value conflicts: {conflict_hint or '(none)'}\n\n"
        f"Debate:\n{debate_text}\n\n"
        "Return a JSON array. Each item must include:\n"
        '- "name" in the form "X vs Y"\n'
        '- "pole_a_label"\n'
        '- "pole_a_agents"\n'
        '- "pole_a_stance"\n'
        '- "pole_b_label"\n'
        '- "pole_b_agents"\n'
        '- "pole_b_stance"\n'
        '- "intensity" from 0 to 1\n'
        '- optional "value_a" and "value_b"\n'
    )

    tensions_raw = await _generate_json_list_with_language_retry(
        stage_name="core_tensions",
        prompt=prompt,
        temperature=0.4,
        collect_failures=_collect_core_tension_language_failures,
    )
    if tensions_raw is None:
        return []

    allowed_agents = _agent_reference_allowlist(statements)
    tensions: list[dict[str, Any]] = []
    for item in tensions_raw[:3]:
        tension: dict[str, Any] = {
            "tension_id": str(uuid.uuid4())[:8],
            "name": item.get("name", ""),
            "pole_a": {
                "label": item.get("pole_a_label", ""),
                "agents": _filter_agent_references(item.get("pole_a_agents", []), allowed_agents),
                "stance": item.get("pole_a_stance", ""),
            },
            "pole_b": {
                "label": item.get("pole_b_label", ""),
                "agents": _filter_agent_references(item.get("pole_b_agents", []), allowed_agents),
                "stance": item.get("pole_b_stance", ""),
            },
            "intensity": max(0.0, min(1.0, float(item.get("intensity", 0.5)))),
        }
        value_a = item.get("value_a", "")
        value_b = item.get("value_b", "")
        if value_a or value_b:
            tension["value_conflict"] = {"value_a": value_a, "value_b": value_b}
        tensions.append(_attribute_tension_evidence(tension, statements))
    return tensions


async def _generate_protective_intents(
    statements: list[dict], profile: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate IFS-style protective intent for each agent voice."""
    voice_positions = _distill_voice_positions(statements)
    if not voice_positions:
        return []

    dilemma = profile.get("core_dilemma") or "内心困境"
    voices_text = "\n".join(
        f"- {voice['agent_name']}: {voice['core_stance']}"
        for voice in voice_positions
    )
    prompt = (
        "You are an IFS-informed therapist.\n"
        f"User dilemma: {dilemma}\n\n"
        f"Voices:\n{voices_text}\n\n"
        "Return a JSON array. Each item must include:\n"
        '- "agent_name"\n'
        '- "intent" describing what this voice protects\n'
        '- "what_it_protects" describing the feared loss or injury\n'
        '- "underlying_value" as a short value label\n'
        "Keep the tone warm and non-judgmental.\n"
    )

    intents_raw = await _generate_json_list_with_language_retry(
        stage_name="protective_intents",
        prompt=prompt,
        temperature=0.5,
        collect_failures=_collect_protective_intent_language_failures,
    )
    if intents_raw is None:
        return [
            {
                "agent_id": voice["agent_id"],
                "agent_name": voice["agent_name"],
                "intent": "\u8fd9\u4e2a\u58f0\u97f3\u5728\u7528\u81ea\u5df1\u7684\u65b9\u5f0f\u5173\u5fc3\u4f60\u3002",
                "what_it_protects": "\u5b83\u4e0d\u5e0c\u671b\u4f60\u5ffd\u89c6\u8fd9\u4e2a\u65b9\u9762\u7684\u9700\u8981\u3002",
                "underlying_value": "\u5173\u6000",
            }
            for voice in voice_positions
        ]

    name_to_id = {voice["agent_name"]: voice["agent_id"] for voice in voice_positions}
    return [
        {
            "agent_id": name_to_id.get(item.get("agent_name", ""), ""),
            "agent_name": item.get("agent_name", ""),
            "intent": item.get("intent", ""),
            "what_it_protects": item.get("what_it_protects", ""),
            "underlying_value": item.get("underlying_value", ""),
        }
        for item in intents_raw
    ]


async def _extract_consensus_areas(
    statements: list[dict],
) -> list[dict[str, Any]]:
    """Use LLM to extract structured consensus areas from debate."""
    if not statements:
        return []

    debate_text = _build_full_debate_text(statements)
    prompt = (
        "Analyze the debate and extract up to four consensus areas.\n\n"
        f"Debate:\n{debate_text}\n\n"
        "Return a JSON array. Each item must include:\n"
        '- "description"\n'
        '- "supporting_agents"\n'
        '- "evidence" as short supporting quotes or paraphrases\n'
        "If there is no consensus, return [].\n"
    )

    areas_raw = await _generate_json_list_with_language_retry(
        stage_name="consensus_areas",
        prompt=prompt,
        temperature=0.3,
        collect_failures=_collect_consensus_language_failures,
    )
    if areas_raw is None:
        return []

    allowed_agents = _agent_reference_allowlist(statements)
    return [
        {
            "area_id": str(uuid.uuid4())[:8],
            "description": item.get("description", ""),
            "supporting_agents": _filter_agent_references(item.get("supporting_agents", []), allowed_agents),
            "evidence": item.get("evidence", []),
        }
        for item in areas_raw[:4]
    ]


async def _generate_narrative_enhanced(
    synthesis_type: str,
    dilemma: str,
    voice_positions: list[dict],
    core_tensions: list[dict[str, Any]],
    protective_intents: list[dict[str, Any]],
    consensus_areas: list[dict[str, Any]],
    profile: dict[str, Any],
) -> str:
    """Generate a rich narrative incorporating tensions and protective intents."""
    voice_summary = "\n".join(
        f"- {voice['agent_name']}: {voice['core_stance'][:100]}"
        for voice in voice_positions
    )

    tension_summary = ""
    if core_tensions:
        tension_summary = "\nCore tensions:\n" + "\n".join(
            f"- {item['name']} (intensity {item.get('intensity', 0.5):.1f})"
            for item in core_tensions
        )

    intent_summary = ""
    if protective_intents:
        intent_summary = "\nProtective intents:\n" + "\n".join(
            f"- {item['agent_name']}: {item.get('intent', '')}"
            for item in protective_intents
        )

    consensus_summary = ""
    if consensus_areas:
        consensus_summary = "\nConsensus areas:\n" + "\n".join(
            f"- {item['description']}"
            for item in consensus_areas
        )

    value_conflicts = profile.get("value_conflicts") or []
    value_text = ", ".join(
        f"{item.get('value_a', '')} vs {item.get('value_b', '')}"
        for item in value_conflicts[:3]
    ) or "(unknown)"

    if synthesis_type == "CONSENSUS_MAP":
        prompt = (
            "Write an enhanced consensus-map synthesis in one paragraph.\n"
            f"Dilemma: {dilemma}\n"
            f"Value conflicts: {value_text}\n\n"
            f"Voices:\n{voice_summary}\n{tension_summary}\n{intent_summary}\n{consensus_summary}\n\n"
            "Describe the shared ground, acknowledge the remaining tension honestly, "
            "reframe each voice as protective, and end with a warm invitation to reflect.\n"
        )
    else:
        prompt = (
            "Write an enhanced polyphonic synthesis in one paragraph.\n"
            f"Dilemma: {dilemma}\n"
            f"Value conflicts: {value_text}\n\n"
            f"Voices:\n{voice_summary}\n{tension_summary}\n{intent_summary}\n\n"
            "Honor each voice, describe the living tension between them, reframe each as "
            "protective intent, and invite the user to stay curious without picking a side.\n"
        )

    narrative = await _generate_text_with_language_retry(
        stage_name="enhanced_narrative",
        prompt=prompt,
        temperature=0.6,
        max_tokens=2048,
        timeout=config.LLM_TIMEOUT_SYNTHESIS_SEC,
    )
    if narrative:
        return narrative

    parts = [
        f"\u5173\u4e8e\u300c{dilemma}\u300d\uff0c\u4f60\u5185\u5fc3\u7684\u5404\u4e2a\u58f0\u97f3\u8868\u8fbe\u4e86\u4e0d\u540c\u7684\u5173\u5207\uff1a\n"
    ]
    for voice in voice_positions:
        parts.append(f"\u00b7 {voice['agent_name']}\uff1a{voice['core_stance'][:80]}")
    if core_tensions:
        parts.append("\n\u8fd9\u4e9b\u58f0\u97f3\u4e4b\u95f4\u5b58\u5728\u4ee5\u4e0b\u6838\u5fc3\u5f20\u529b\uff1a")
        for item in core_tensions:
            parts.append(f"\u00b7 {item['name']}")
    if protective_intents:
        parts.append("\n\u6bcf\u4e2a\u58f0\u97f3\u90fd\u5728\u4ee5\u81ea\u5df1\u7684\u65b9\u5f0f\u4fdd\u62a4\u4f60\uff1a")
        for item in protective_intents:
            parts.append(f"\u00b7 {item['agent_name']}\u2014{item.get('intent', '')}")
    parts.append(
        "\n\u8fd9\u4e9b\u58f0\u97f3\u7684\u5171\u5b58\u672c\u8eab\u5c31\u6709\u4ef7\u503c\u3002"
        "\u4f60\u53ef\u4ee5\u5e26\u7740\u8fd9\u4efd\u89c9\u5bdf\u8fdb\u5165\u53cd\u601d\u3002"
    )
    return "\n".join(parts)



def _attribute_tension_evidence(
    tension: dict[str, Any], statements: list[dict],
) -> dict[str, Any]:
    """Match debate statements to each pole of a tension using embedding similarity.

    For each pole, finds the top-3 most relevant statements from that pole's agents.
    Falls back to keyword substring matching if embeddings unavailable.
    """
    debate_stmts = [s for s in statements if _is_agent_statement(s)]

    for pole_key in ("pole_a", "pole_b"):
        pole = tension.get(pole_key, {})
        pole_agents = [a.lower() for a in pole.get("agents", [])]
        pole_stance = pole.get("stance", "")

        if not pole_stance or not pole_agents:
            pole["evidence_statements"] = []
            continue

        # Filter statements by agents in this pole (fuzzy name matching)
        candidates = []
        for s in debate_stmts:
            agent_name = s.get("agent_name", "").lower()
            if any(pa in agent_name or agent_name in pa for pa in pole_agents):
                candidates.append(s)

        if not candidates:
            pole["evidence_statements"] = []
            continue

        # Score each candidate
        scored: list[tuple[float, dict]] = []
        try:
            from app.services.embedding import EmbeddingService, cosine_similarity as _cos
            svc = EmbeddingService.get()
            if svc.is_real_model_loaded():
                # Embedding mode
                texts = [pole_stance] + [c.get("content", "")[:300] for c in candidates]
                vectors = svc.encode(texts)
                stance_vec = vectors[0]
                for j, c in enumerate(candidates):
                    sim = _cos(stance_vec, vectors[j + 1])
                    scored.append((sim, c))
            else:
                raise RuntimeError("fallback")
        except Exception:
            # Keyword fallback: count stance words in statement
            stance_words = set(pole_stance.replace("。", " ").replace("，", " ").split())
            stance_words = {w for w in stance_words if len(w) >= 2}
            for c in candidates:
                content_words = set(c.get("content", "").replace("。", " ").replace("，", " ").split())
                overlap = len(stance_words & content_words) / max(len(stance_words), 1)
                scored.append((overlap, c))

        # Top-3 by relevance
        scored.sort(key=lambda x: x[0], reverse=True)
        evidence = []
        for score, c in scored[:3]:
            if score <= 0:
                continue
            evidence.append({
                "statement_id": c.get("statement_id", ""),
                "agent_name": c.get("agent_name", ""),
                "round_number": c.get("round_number", 0),
                "content": c.get("content", "")[:300],
                "relevance_score": round(score, 2),
            })
        pole["evidence_statements"] = evidence

    return tension


# ---------------------------------------------------------------------------
# Sync synthesis (heuristic only, backward compatible)
# ---------------------------------------------------------------------------

def generate_synthesis(statements: list[dict], profile: dict[str, Any]) -> dict[str, Any]:
    """Produce CONSENSUS_MAP or POLYPHONIC_LANDSCAPE from debate statements (sync, heuristic)."""
    dilemma = profile.get("core_dilemma") or "内心困境"
    if not statements:
        return {"synthesis_type": "NONE", "narrative": "暂无辩论内容。", "voice_positions": []}

    converged = _detect_convergence(statements)
    consensus = _extract_consensus(statements)
    voice_positions = _distill_voice_positions(statements)

    if converged and consensus:
        narrative = f"关于「{dilemma}」，各声音经过多轮讨论逐渐趋于共识。\n\n"
        narrative += "共识要点：" + "、".join(consensus[:3]) + "。\n\n"
        for vp in voice_positions:
            narrative += f"· {vp['agent_name']}：{vp['core_stance'][:80]}…\n"
        narrative += "\n这些声音虽然出发点不同，但在某些方面找到了共同点。"
        return {
            "synthesis_type": "CONSENSUS_MAP",
            "narrative": narrative,
            "consensus_points": consensus,
            "voice_positions": voice_positions,
        }
    else:
        narrative = f"关于「{dilemma}」，不同声音表达了各自独特的立场：\n\n"
        for vp in voice_positions:
            narrative += f"· {vp['agent_name']}：{vp['core_stance'][:80]}…\n\n"
        narrative += "这些声音都在以各自的方式关注你的困境。它们的共存本身就有价值——你不需要选边站。你可以带着这份多声音图景进入反思。"
        return {
            "synthesis_type": "POLYPHONIC_LANDSCAPE",
            "narrative": narrative,
            "voice_positions": voice_positions,
        }


# ---------------------------------------------------------------------------
# Async LLM-enhanced synthesis
# ---------------------------------------------------------------------------

def _derive_skipped_r4_fields(
    engagement_record: dict[str, Any],
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    highlight_moments = [
        str(item)
        for item in engagement_record.get("highlight_moments", [])
        if item
    ]
    acknowledgements = [
        item
        for item in engagement_record.get("r3_5_acknowledgements", [])
        if isinstance(item, dict)
    ]
    divergence_map = str(engagement_record.get("divergence_map", "") or "")
    unresolved = [
        str(item)
        for item in engagement_record.get("unresolved_disagreements", [])
        if item
    ]

    key_insight = ""
    if highlight_moments:
        key_insight = highlight_moments[0]
    elif acknowledgements:
        key_insight = str(acknowledgements[0].get("legitimacy_reason", "") or "")
    elif divergence_map:
        key_insight = divergence_map
    elif unresolved:
        key_insight = unresolved[0]

    if acknowledgements:
        productive_tensions = [
            {
                "description": str(item.get("disagreement", "") or ""),
                "understanding": str(item.get("legitimacy_reason", "") or ""),
            }
            for item in acknowledgements
            if item.get("disagreement") or item.get("legitimacy_reason")
        ]
        irreducible_differences = [
            {
                "description": str(item.get("disagreement", "") or ""),
                "why_irreducible": str(item.get("stake", "") or ""),
            }
            for item in acknowledgements
            if item.get("disagreement") or item.get("stake")
        ]
    else:
        productive_tensions = [
            {"description": item, "understanding": item}
            for item in unresolved
        ]
        irreducible_differences = [
            {"description": item, "why_irreducible": item}
            for item in unresolved
        ]

    return key_insight, productive_tensions, irreducible_differences


def _user_verdicts_from_tension_map(
    debate_artifacts: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Flatten the user's follow-up answers (user_verdict) from the R2 tension map."""
    tension_map = (debate_artifacts or {}).get("tension_map") or {}
    if not isinstance(tension_map, dict):
        return []
    tensions = tension_map.get("tensions") or []
    if not isinstance(tensions, list):
        return []

    verdicts: list[dict[str, Any]] = []
    for tension in tensions:
        if not isinstance(tension, dict):
            continue
        verdict = tension.get("user_verdict")
        if not isinstance(verdict, dict) or not verdict.get("text"):
            continue
        tension_id = tension.get("id")
        verdicts.append(
            {
                "tension_id": str(tension_id) if tension_id is not None else "",
                "tension_name": str(tension.get("description") or ""),
                "status": str(verdict.get("status") or ""),
                "text": str(verdict.get("text") or ""),
            }
        )
    return verdicts


def _extract_enhanced_artifact_fields(
    debate_artifacts: dict[str, Any] | None,
    profile: dict[str, Any],
) -> dict[str, Any]:
    artifacts = debate_artifacts or {}
    dilemma_text = artifacts.get("dilemma_text") or profile.get("core_dilemma") or "内心困境"
    engagement_record = artifacts.get("engagement_record") or {}
    r4_present = artifacts.get("r4_present")

    if r4_present is False:
        key_insight, productive_tensions, irreducible_differences = _derive_skipped_r4_fields(
            engagement_record
        )
    else:
        convergence_map = artifacts.get("convergence_map") or {}
        key_insight = convergence_map.get("key_insight", "")
        productive_tensions = convergence_map.get("productive_tensions", [])
        irreducible_differences = convergence_map.get("irreducible_differences", [])

    return {
        "agent_evolutions": artifacts.get("agent_evolutions", []),
        "key_insight": key_insight,
        "productive_tensions": productive_tensions,
        "irreducible_differences": irreducible_differences,
        "highlight_moments": engagement_record.get("highlight_moments", []),
        "concessions": engagement_record.get("concessions_made", []),
        "dilemma_text": dilemma_text,
        "significant_turns": engagement_record.get("significant_turns", []),
        "divergence_map": str(engagement_record.get("divergence_map") or ""),
        "agent_voice_similarity_matrix": (
            (artifacts.get("convergence_map") or {}).get(
                "agent_voice_similarity_matrix", {}
            )
        ),
        "user_verdicts": _user_verdicts_from_tension_map(artifacts),
    }

async def generate_synthesis_enhanced(
    statements: list[dict], profile: dict[str, Any],
    debate_artifacts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Full-featured synthesis with core tensions, protective intents, consensus areas,
    and semantic convergence detection. Individual stages degrade gracefully via
    per-stage retry/fallback (_with_stage_retry).
    """
    if not statements:
        artifact_fields = _extract_enhanced_artifact_fields(debate_artifacts, profile)
        return {
            "synthesis_type": "NONE", "narrative": "暂无辩论内容。",
            "voice_positions": [], "core_tensions": [], "consensus_areas": [],
            "protective_intents": [], "meta": {},
            "agent_evolutions": artifact_fields["agent_evolutions"],
            "key_insight": artifact_fields["key_insight"],
            "productive_tensions": artifact_fields["productive_tensions"],
            "irreducible_differences": artifact_fields["irreducible_differences"],
            "highlight_moments": artifact_fields["highlight_moments"],
            "concessions": artifact_fields["concessions"],
            "dilemma_text": artifact_fields["dilemma_text"],
            "significant_turns": artifact_fields["significant_turns"],
            "divergence_map": artifact_fields["divergence_map"],
            "agent_voice_similarity_matrix": artifact_fields["agent_voice_similarity_matrix"],
            "user_verdicts": artifact_fields["user_verdicts"],
        }

    dilemma = profile.get("core_dilemma") or "内心困境"
    voice_positions = _distill_voice_positions(statements)
    max_round = max(s.get("round_number", 1) for s in statements)

    # --- Step 1: Detect convergence / divergence ---
    try:
        convergence_score = await _detect_convergence_combined(statements)
    except Exception:
        convergence_score = 0.5 if _detect_convergence(statements) else 0.2

    novelty_score = _calculate_novelty_decay(statements)
    value_intensity = _analyze_value_conflict_intensity(statements, profile)

    # --- Step 2: Decide synthesis type ---
    if convergence_score >= 0.7 and novelty_score < 0.3:
        synthesis_type = "CONSENSUS_MAP"
        termination_mode = "ORGANIC_CONVERGENCE"
    elif value_intensity >= 0.7 or convergence_score < 0.3:
        synthesis_type = "POLYPHONIC_LANDSCAPE"
        termination_mode = "STRATEGIC_DIVERGENCE"
    else:
        # Mixed: lean toward polyphonic (preserve complexity per design goal)
        synthesis_type = "POLYPHONIC_LANDSCAPE"
        termination_mode = "MIXED"

    # --- Step 3: Extract structured content (parallel-safe, with stage retry) ---
    stages_completed: list[str] = ["convergence"]
    stages_failed: list[str] = []
    artifact_core_tensions = _core_tensions_from_tension_map(
        debate_artifacts,
        statements,
    )

    tension_task = None
    if not artifact_core_tensions:
        tension_task = asyncio.create_task(
            _with_stage_retry(lambda: _extract_core_tensions(statements, profile), "core_tensions")
        )
    intent_task = asyncio.create_task(
        _with_stage_retry(lambda: _generate_protective_intents(statements, profile), "protective_intents")
    )

    if synthesis_type == "CONSENSUS_MAP":
        consensus_task = asyncio.create_task(
            _with_stage_retry(lambda: _extract_consensus_areas(statements), "consensus_areas")
        )
    else:
        consensus_task = None

    core_tensions = (
        artifact_core_tensions
        if artifact_core_tensions
        else await tension_task
    )
    if core_tensions:
        stages_completed.append("tensions")
    else:
        stages_failed.append("tensions")

    protective_intents = await intent_task
    if protective_intents:
        stages_completed.append("intents")
    else:
        stages_failed.append("intents")

    consensus_areas: list[dict[str, Any]] = []
    if consensus_task:
        consensus_areas = await consensus_task
        if consensus_areas:
            stages_completed.append("consensus")
        else:
            stages_failed.append("consensus")

    # --- Step 4: Generate narrative with enhanced context ---
    narrative = await _generate_narrative_enhanced(
        synthesis_type, dilemma, voice_positions,
        core_tensions, protective_intents, consensus_areas, profile,
    )
    if narrative:
        stages_completed.append("narrative")
    else:
        stages_failed.append("narrative")

    artifact_fields = _extract_enhanced_artifact_fields(debate_artifacts, profile)

    return {
        "synthesis_type": synthesis_type,
        "narrative": narrative,
        "voice_positions": voice_positions,
        "core_tensions": core_tensions,
        "consensus_areas": consensus_areas,
        "protective_intents": protective_intents,
        "agent_evolutions": artifact_fields["agent_evolutions"],
        "key_insight": artifact_fields["key_insight"],
        "productive_tensions": artifact_fields["productive_tensions"],
        "irreducible_differences": artifact_fields["irreducible_differences"],
        "highlight_moments": artifact_fields["highlight_moments"],
        "concessions": artifact_fields["concessions"],
        "dilemma_text": artifact_fields["dilemma_text"],
        "significant_turns": artifact_fields["significant_turns"],
        "divergence_map": artifact_fields["divergence_map"],
        "agent_voice_similarity_matrix": artifact_fields["agent_voice_similarity_matrix"],
        "user_verdicts": artifact_fields["user_verdicts"],
        "meta": {
            "convergence_score": round(convergence_score, 2),
            "novelty_score": round(novelty_score, 2),
            "value_conflict_intensity": round(value_intensity, 2),
            "debate_rounds": max_round,
            "termination_mode": termination_mode,
            "round_progression": _compute_round_progression(statements),
            "stages_completed": stages_completed,
            "stages_failed": stages_failed,
        },
    }


async def generate_synthesis_enhanced_streaming(
    statements: list[dict], profile: dict[str, Any],
    debate_artifacts: dict[str, Any] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Same pipeline as generate_synthesis_enhanced but yields SSE-compatible
    progress dicts so the frontend can show stage-by-stage loading.
    Final yield has event='synthesis_complete' with the full result.
    """
    total_stages = 4  # convergence, tensions+intents, consensus(conditional), narrative
    stages_completed: list[str] = []
    stages_failed: list[str] = []

    if not statements:
        _ea = debate_artifacts or {}
        artifact_fields = _extract_enhanced_artifact_fields(debate_artifacts, profile)
        empty = {
            "synthesis_type": "NONE", "narrative": "暂无辩论内容。",
            "voice_positions": [], "core_tensions": [], "consensus_areas": [],
            "protective_intents": [], "meta": {},
            "agent_evolutions": artifact_fields["agent_evolutions"],
            "key_insight": artifact_fields["key_insight"],
            "productive_tensions": artifact_fields["productive_tensions"],
            "irreducible_differences": artifact_fields["irreducible_differences"],
            "highlight_moments": artifact_fields["highlight_moments"],
            "concessions": artifact_fields["concessions"],
            "dilemma_text": _ea.get("dilemma_text") or profile.get("core_dilemma") or "内心困境",
            "significant_turns": artifact_fields["significant_turns"],
            "divergence_map": artifact_fields["divergence_map"],
            "agent_voice_similarity_matrix": artifact_fields["agent_voice_similarity_matrix"],
            "user_verdicts": artifact_fields["user_verdicts"],
        }
        yield {"event": "synthesis_complete", "data": empty}
        return

    dilemma = profile.get("core_dilemma") or "内心困境"
    voice_positions = _distill_voice_positions(statements)
    max_round = max(s.get("round_number", 1) for s in statements)

    # --- Stage 1: Convergence ---
    yield {"event": "synthesis_stage_start", "data": {
        "stage": "convergence", "label": "分析收敛趋势", "index": 0, "total": total_stages,
    }}
    try:
        convergence_score = await _detect_convergence_combined(statements)
        stages_completed.append("convergence")
    except Exception:
        convergence_score = 0.5 if _detect_convergence(statements) else 0.2
        stages_completed.append("convergence")  # heuristic fallback still works
    novelty_score = _calculate_novelty_decay(statements)
    value_intensity = _analyze_value_conflict_intensity(statements, profile)

    if convergence_score >= 0.7 and novelty_score < 0.3:
        synthesis_type = "CONSENSUS_MAP"
        termination_mode = "ORGANIC_CONVERGENCE"
    elif value_intensity >= 0.7 or convergence_score < 0.3:
        synthesis_type = "POLYPHONIC_LANDSCAPE"
        termination_mode = "STRATEGIC_DIVERGENCE"
    else:
        synthesis_type = "POLYPHONIC_LANDSCAPE"
        termination_mode = "MIXED"

    yield {"event": "synthesis_stage_end", "data": {"stage": "convergence", "index": 0}}

    # --- Stage 2: Tensions + Intents (parallel, with stage retry) ---
    yield {"event": "synthesis_stage_start", "data": {
        "stage": "tensions", "label": "识别核心张力", "index": 1, "total": total_stages,
    }}
    yield {"event": "synthesis_stage_start", "data": {
        "stage": "intents", "label": "解读保护意图", "index": 1, "total": total_stages,
    }}

    artifact_core_tensions = _core_tensions_from_tension_map(
        debate_artifacts,
        statements,
    )
    tension_task = None
    if not artifact_core_tensions:
        tension_task = asyncio.create_task(
            _with_stage_retry(lambda: _extract_core_tensions(statements, profile), "core_tensions")
        )
    intent_task = asyncio.create_task(
        _with_stage_retry(lambda: _generate_protective_intents(statements, profile), "protective_intents")
    )

    if synthesis_type == "CONSENSUS_MAP":
        consensus_task = asyncio.create_task(
            _with_stage_retry(lambda: _extract_consensus_areas(statements), "consensus_areas")
        )
    else:
        consensus_task = None

    core_tensions = (
        artifact_core_tensions
        if artifact_core_tensions
        else await tension_task
    )
    if core_tensions:
        stages_completed.append("tensions")
    else:
        stages_failed.append("tensions")
        yield {"event": "synthesis_stage_error", "data": {
            "stage": "tensions", "message": "核心张力识别暂时失败",
        }}
    yield {"event": "synthesis_stage_end", "data": {"stage": "tensions", "index": 1}}

    protective_intents = await intent_task
    if protective_intents:
        stages_completed.append("intents")
    else:
        stages_failed.append("intents")
        yield {"event": "synthesis_stage_error", "data": {
            "stage": "intents", "message": "保护意图解读暂时失败",
        }}
    yield {"event": "synthesis_stage_end", "data": {"stage": "intents", "index": 1}}

    # --- Stage 3: Consensus (conditional) ---
    consensus_areas: list[dict[str, Any]] = []
    if consensus_task:
        yield {"event": "synthesis_stage_start", "data": {
            "stage": "consensus", "label": "提炼共识区域", "index": 2, "total": total_stages,
        }}
        consensus_areas = await consensus_task
        if consensus_areas:
            stages_completed.append("consensus")
        else:
            stages_failed.append("consensus")
            yield {"event": "synthesis_stage_error", "data": {
                "stage": "consensus", "message": "共识区域提炼暂时失败",
            }}
        yield {"event": "synthesis_stage_end", "data": {"stage": "consensus", "index": 2}}

    # --- Stage 4: Narrative ---
    yield {"event": "synthesis_stage_start", "data": {
        "stage": "narrative", "label": "编织综合叙述", "index": 3, "total": total_stages,
    }}
    narrative = await _generate_narrative_enhanced(
        synthesis_type, dilemma, voice_positions,
        core_tensions, protective_intents, consensus_areas, profile,
    )
    if narrative:
        stages_completed.append("narrative")
    else:
        stages_failed.append("narrative")
    yield {"event": "synthesis_stage_end", "data": {"stage": "narrative", "index": 3}}

    artifact_fields = _extract_enhanced_artifact_fields(debate_artifacts, profile)

    result = {
        "synthesis_type": synthesis_type,
        "narrative": narrative,
        "voice_positions": voice_positions,
        "core_tensions": core_tensions,
        "consensus_areas": consensus_areas,
        "protective_intents": protective_intents,
        "agent_evolutions": artifact_fields["agent_evolutions"],
        "key_insight": artifact_fields["key_insight"],
        "productive_tensions": artifact_fields["productive_tensions"],
        "irreducible_differences": artifact_fields["irreducible_differences"],
        "highlight_moments": artifact_fields["highlight_moments"],
        "concessions": artifact_fields["concessions"],
        "dilemma_text": artifact_fields["dilemma_text"],
        "significant_turns": artifact_fields["significant_turns"],
        "divergence_map": artifact_fields["divergence_map"],
        "agent_voice_similarity_matrix": artifact_fields["agent_voice_similarity_matrix"],
        "user_verdicts": artifact_fields["user_verdicts"],
        "meta": {
            "convergence_score": round(convergence_score, 2),
            "novelty_score": round(novelty_score, 2),
            "value_conflict_intensity": round(value_intensity, 2),
            "debate_rounds": max_round,
            "termination_mode": termination_mode,
            "round_progression": _compute_round_progression(statements),
            "stages_completed": stages_completed,
            "stages_failed": stages_failed,
        },
    }
    yield {"event": "synthesis_complete", "data": result}
