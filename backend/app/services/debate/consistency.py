"""
ConsistencyMonitor: identity fidelity checking for debate agents.

Two evaluation modes:
1. **LLM-based** (``evaluate_async``): asks an LLM to score style, values,
   and shift-justification on a 1-5 scale.  Preferred for R2+ rounds.
2. **Heuristic** (``evaluate``): 3-layer keyword/character-Jaccard scoring
   (P2L 0.4 + L2L 0.3 + CAD 0.3).  Always available as fallback.

If overall score < 0.75, retries with correction feedback (max 2 retries).

Supports embedding mode when EmbeddingService has a real model loaded.
"""
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from app.services.debate.language_rules import (
    build_language_correction_text,
    detect_boundary_attack,
    detect_jargon_stack,
    detect_person_drift,
    detect_report_tone,
)
from app.services.llm import generate  # noqa: F401 — used in evaluate_async, patched in tests

logger = logging.getLogger(__name__)


@dataclass
class ConsistencyScore:
    """3-layer consistency evaluation result (heuristic mode)."""
    prompt_to_line: float    # P2L: identity card → response [0..1]
    line_to_line: float      # L2L: own history → response [0..1]
    cross_agent_diff: float  # CAD: peer differentiation [0..1]
    overall: float           # weighted combination


@dataclass
class ConsistencyResult:
    """LLM-based consistency evaluation result."""
    passed: bool
    style_score: int         # 1-5
    values_score: int        # 1-5
    shift_justified: int     # 1-5 or -1 if not applicable
    correction: Optional[str]  # None if passed, correction text if failed


@dataclass
class LanguageAlignmentResult:
    """Lightweight spoken-language alignment result."""
    passed: bool
    issues: list[str]
    correction: Optional[str]


def _parse_json(text: str) -> Optional[dict]:
    """Try to extract a JSON dict from *text*.

    Strategy:
      1. Direct ``json.loads()`` on the full text.
      2. Extract content inside markdown code blocks.
      3. Return ``None`` on failure.
    """
    if not text or not text.strip():
        return None

    # 1. Try direct parse
    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, dict):
            return parsed
        return None
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. Try extracting from markdown code blocks
    pattern = r"```(?:json)?\s*\n?(.*?)\n?\s*```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(1).strip())
            if isinstance(parsed, dict):
                return parsed
            return None
        except (json.JSONDecodeError, ValueError):
            pass

    return None


# Conservative fallback when LLM evaluation fails
_FALLBACK_RESULT = ConsistencyResult(
    passed=True,
    style_score=3,
    values_score=3,
    shift_justified=-1,
    correction=None,
)


# Weights
P2L_WEIGHT = 0.4
L2L_WEIGHT = 0.3
CAD_WEIGHT = 0.3

# Thresholds
PASSING_THRESHOLD = 0.75
MAX_RETRIES = 2

# CAD: below this peer-differentiation score, an agent's voice is too similar to
# its same-round peers (homogenization / 和稀泥) and earns a correction nudge.
CAD_DIFF_THRESHOLD = 0.6
_CROSS_AGENT_CORRECTION = "请展现你独特的视角，不要与其他声音的表达过于相似。"


class ConsistencyMonitor:
    """
    3-indicator consistency evaluation for debate agents.
    Uses embedding cosine similarity when available, falls back to keyword heuristics.
    """

    PASSING_THRESHOLD = PASSING_THRESHOLD
    MAX_RETRIES = MAX_RETRIES

    def check_language_alignment(
        self,
        response: str,
        agent_name: str,
        user_display_name: str = "",
    ) -> LanguageAlignmentResult:
        """Catch report tone, jargon stacks, boundary attacks, and person drift."""
        text = response.strip()
        if not text:
            return LanguageAlignmentResult(
                passed=False,
                issues=["report_tone"],
                correction=build_language_correction_text(
                    ["report_tone"],
                    agent_name=agent_name,
                ),
            )

        issues: list[str] = []

        if detect_report_tone(text):
            issues.append("report_tone")
        if detect_jargon_stack(text):
            issues.append("jargon_stack")
        if detect_boundary_attack(text):
            issues.append("boundary_attack")
        if user_display_name and detect_person_drift(text, user_display_name):
            issues.append("person_drift")

        return LanguageAlignmentResult(
            passed=not issues,
            issues=issues,
            correction=(
                build_language_correction_text(
                    issues, agent_name=agent_name, user_name=user_display_name
                )
                if issues
                else None
            ),
        )

    def _use_embeddings(self) -> bool:
        """Check if embedding mode is available."""
        try:
            from app.services.embedding import EmbeddingService
            return EmbeddingService.get().is_real_model_loaded()
        except Exception:
            return False

    def evaluate(
        self,
        agent_id: str,
        response: str,
        identity_card: dict[str, Any],
        own_previous_statements: dict[int, str],
        same_round_other_responses: list[str],
    ) -> ConsistencyScore:
        """Compute 3-layer consistency score. Uses embeddings if available."""
        use_emb = self._use_embeddings()

        if use_emb:
            p2l = self._prompt_to_line_embedding(identity_card, response)
            l2l = self._line_to_line_embedding(own_previous_statements, response)
            cad = self._cross_agent_diff_embedding(response, same_round_other_responses)
        else:
            p2l = self._prompt_to_line(identity_card, response)
            l2l = self._line_to_line(own_previous_statements, response)
            cad = self._cross_agent_diff(response, same_round_other_responses)

        overall = p2l * P2L_WEIGHT + l2l * L2L_WEIGHT + cad * CAD_WEIGHT
        return ConsistencyScore(
            prompt_to_line=round(p2l, 4),
            line_to_line=round(l2l, 4),
            cross_agent_diff=round(cad, 4),
            overall=round(overall, 4),
        )

    def score_cross_agent_diff(
        self, response: str, other_responses: list[str],
    ) -> float:
        """Public CAD score [0..1]; 1.0 = fully distinct from peers. Embedding-aware."""
        if self._use_embeddings():
            return round(self._cross_agent_diff_embedding(response, other_responses), 4)
        return round(self._cross_agent_diff(response, other_responses), 4)

    def build_cross_agent_correction(
        self, response: str, other_responses: list[str],
    ) -> Optional[str]:
        """Nudge text when an agent's voice is too similar to its same-round peers."""
        if not response.strip() or not other_responses:
            return None
        if self.score_cross_agent_diff(response, other_responses) < CAD_DIFF_THRESHOLD:
            return _CROSS_AGENT_CORRECTION
        return None

    # ------------------------------------------------------------------
    # LLM-based evaluation (preferred for R2+)
    # ------------------------------------------------------------------

    async def evaluate_async(
        self,
        agent_id: str,
        response: str,
        identity_card: dict,
        recent_statements: list[str],
    ) -> ConsistencyResult:
        """Evaluate identity consistency via LLM scoring.

        Returns a ``ConsistencyResult`` with style/values/shift scores on a
        1-5 scale.  If *any* score <= 2, the result is ``passed=False`` with
        a correction suggestion.

        On LLM failure or malformed output, conservatively returns
        ``passed=True`` so as not to block the debate.
        """
        role_name = identity_card.get(
            "display_name", identity_card.get("role", agent_id),
        )
        core_values_list = [
            v.get("value", "") for v in identity_card.get("core_values", [])
        ]
        core_values_str = ", ".join(v for v in core_values_list if v)

        recent_joined = "\n".join(recent_statements[-3:]) if recent_statements else "(无)"

        prompt = (
            "你是辩论一致性审核员。判断这段发言是否符合角色身份。\n\n"
            f"【角色定义】{role_name}: {core_values_str}\n"
            f"【该角色之前的发言】{recent_joined}\n"
            f"【本次发言】{response}\n\n"
            "请严格输出JSON：\n"
            '{"style_score": 1-5, "values_score": 1-5, "shift_justified": 1-5或-1, '
            '"correction": "修正建议或null"}\n\n'
            "评分标准：\n"
            "- style_score: 用词习惯、表达方式、思维模式是否一致\n"
            "- values_score: 角色的基本信念和关注点是否一致\n"
            "- shift_justified: 如果立场有变化，变化是否有讨论中的依据（-1表示无变化）\n"
            "- correction: 如果任何一项<=2，给出修正建议："
            '"你的发言听起来像[某角色]的语气，而你是[实际角色]。'
            '请用你的方式——[该角色的特征]——来表达同样的意思。"'
        )

        try:
            raw = await generate(
                prompt,
                system="你是一个JSON审核员，只输出合法JSON。",
                temperature=0.3,
                max_tokens=512,
            )
        except Exception:
            logger.warning(
                "evaluate_async: LLM call failed for agent %s, using fallback",
                agent_id,
                exc_info=True,
            )
            return ConsistencyResult(
                passed=True, style_score=3, values_score=3,
                shift_justified=-1, correction=None,
            )

        data = _parse_json(raw)
        if not data:
            logger.warning(
                "evaluate_async: malformed LLM response for agent %s, using fallback",
                agent_id,
            )
            return ConsistencyResult(
                passed=True, style_score=3, values_score=3,
                shift_justified=-1, correction=None,
            )

        try:
            style = int(data.get("style_score", 3))
            values = int(data.get("values_score", 3))
            shift = int(data.get("shift_justified", -1))
            correction_raw = data.get("correction")
            correction = str(correction_raw) if correction_raw and correction_raw != "null" else None
        except (ValueError, TypeError):
            logger.warning(
                "evaluate_async: score parsing failed for agent %s, using fallback",
                agent_id,
            )
            return ConsistencyResult(
                passed=True, style_score=3, values_score=3,
                shift_justified=-1, correction=None,
            )

        # Any score <= 2 triggers failure
        failed = (
            style <= 2
            or values <= 2
            or (shift != -1 and shift <= 2)
        )

        return ConsistencyResult(
            passed=not failed,
            style_score=style,
            values_score=values,
            shift_justified=shift,
            correction=correction if failed else None,
        )

    def _prompt_to_line(
        self, identity_card: dict[str, Any], response: str,
    ) -> float:
        """
        P2L: Check keyword overlap between identity card and response.
        Extracts keywords from core_values, role name, system_prompt terms.
        Floor: 0.3 for non-empty responses (keyword matching is lossy).
        """
        if not response.strip():
            return 0.0

        keywords = self._extract_identity_keywords(identity_card)
        if not keywords:
            return 1.0  # no keywords to check against

        hits = sum(1 for kw in keywords if kw in response)
        raw_score = hits / len(keywords)

        # Floor of 0.3 for non-empty responses
        return max(0.3, min(1.0, raw_score + 0.3))

    def _line_to_line(
        self, previous_statements: dict[int, str], response: str,
    ) -> float:
        """
        L2L: Character-set Jaccard between own prior statements and response.
        Returns 1.0 if no previous statements (R1 has no history).
        Floor: 0.5 if only one previous statement.
        """
        if not previous_statements:
            return 1.0

        prev_text = "".join(previous_statements.values())
        prev_chars = set(prev_text)
        resp_chars = set(response)

        if not prev_chars or not resp_chars:
            return 1.0

        intersection = prev_chars & resp_chars
        union = prev_chars | resp_chars
        jaccard = len(intersection) / len(union) if union else 0.0

        # Floor of 0.5 if only one previous statement
        if len(previous_statements) <= 1:
            return max(0.5, jaccard)

        return jaccard

    def _cross_agent_diff(
        self, response: str, other_responses: list[str],
    ) -> float:
        """
        CAD: Penalize high similarity with other agents' same-round responses.
        Score = 1.0 - avg_similarity. Returns 1.0 if no other responses.
        """
        if not other_responses:
            return 1.0

        resp_chars = set(response)
        if not resp_chars:
            return 0.0

        similarities: list[float] = []
        for other in other_responses:
            other_chars = set(other)
            if not other_chars:
                continue
            intersection = resp_chars & other_chars
            union = resp_chars | other_chars
            sim = len(intersection) / len(union) if union else 0.0
            similarities.append(sim)

        if not similarities:
            return 1.0

        avg_sim = sum(similarities) / len(similarities)
        return max(0.0, 1.0 - avg_sim)

    @staticmethod
    def _extract_identity_keywords(identity_card: dict[str, Any]) -> list[str]:
        """Extract meaningful keywords from identity card for P2L check."""
        keywords: list[str] = []

        # Core values
        for v in identity_card.get("core_values", []):
            val = v.get("value", "")
            if val and len(val) >= 2:
                keywords.append(val)

        # Role name (Chinese chars from display_name)
        role = identity_card.get("display_name", identity_card.get("role", ""))
        if role:
            # Extract Chinese segments of 2+ chars
            cn_parts = re.findall(r'[\u4e00-\u9fff]{2,}', role)
            keywords.extend(cn_parts)

        # System prompt key terms (first 200 chars, 2+ char Chinese segments)
        sys_prompt = identity_card.get("system_prompt", "")[:200]
        cn_terms = re.findall(r'[\u4e00-\u9fff]{2,4}', sys_prompt)
        # Take up to 10 most distinctive terms (skip very common ones)
        common = {"我们", "可以", "需要", "应该", "能够", "已经", "这个", "那个", "什么", "如何"}
        for t in cn_terms[:15]:
            if t not in common and t not in keywords:
                keywords.append(t)

        # Specific concern
        concern = identity_card.get("specific_concern", "")
        if concern and len(concern) >= 2:
            cn_concern = re.findall(r'[\u4e00-\u9fff]{2,}', concern)
            keywords.extend(cn_concern[:3])

        return keywords

    # ------------------------------------------------------------------
    # Embedding-based scoring (used when real model is loaded)
    # ------------------------------------------------------------------

    def _build_identity_text(self, identity_card: dict[str, Any]) -> str:
        """Build a text representation of the identity card for embedding."""
        parts: list[str] = []
        role = identity_card.get("display_name", identity_card.get("role", ""))
        if role:
            parts.append(role)
        values = ", ".join(v.get("value", "") for v in identity_card.get("core_values", []) if v.get("value"))
        if values:
            parts.append(f"核心价值: {values}")
        concern = identity_card.get("specific_concern", "")
        if concern:
            parts.append(concern)
        sys_prompt = identity_card.get("system_prompt", "")[:300]
        if sys_prompt:
            parts.append(sys_prompt)
        return "。".join(parts)

    def _prompt_to_line_embedding(
        self, identity_card: dict[str, Any], response: str,
    ) -> float:
        """P2L via embedding: cosine similarity between identity text and response.
        Rescales from empirical range [0.3, 0.9] to [0, 1]."""
        if not response.strip():
            return 0.0

        from app.services.embedding import EmbeddingService
        svc = EmbeddingService.get()

        identity_text = self._build_identity_text(identity_card)
        if not identity_text:
            return 1.0

        vectors = svc.encode([identity_text, response])
        from app.services.embedding import cosine_similarity as _cos
        raw = _cos(vectors[0], vectors[1])

        # Rescale from [0.3, 0.9] to [0, 1]
        scaled = (raw - 0.3) / 0.6
        return max(0.0, min(1.0, scaled))

    def _line_to_line_embedding(
        self, previous_statements: dict[int, str], response: str,
    ) -> float:
        """L2L via embedding: cosine similarity between own history and response."""
        if not previous_statements:
            return 1.0

        from app.services.embedding import EmbeddingService, cosine_similarity as _cos
        svc = EmbeddingService.get()

        prev_text = " ".join(previous_statements.values())[:2000]
        vectors = svc.encode([prev_text, response])
        raw = _cos(vectors[0], vectors[1])

        # Embedding L2L tends to be higher; rescale from [0.4, 0.95] to [0, 1]
        scaled = (raw - 0.4) / 0.55
        return max(0.0, min(1.0, scaled))

    def _cross_agent_diff_embedding(
        self, response: str, other_responses: list[str],
    ) -> float:
        """CAD via embedding: 1 - avg cosine similarity with other agents."""
        if not other_responses:
            return 1.0
        if not response.strip():
            return 0.0

        from app.services.embedding import EmbeddingService, cosine_similarity as _cos
        svc = EmbeddingService.get()

        all_texts = [response] + other_responses
        vectors = svc.encode(all_texts)
        resp_vec = vectors[0]

        similarities = [_cos(resp_vec, vectors[i]) for i in range(1, len(vectors))]
        avg_sim = sum(similarities) / len(similarities) if similarities else 0.0
        return max(0.0, 1.0 - avg_sim)
