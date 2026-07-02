"""Follow-up gate: select and voice user-facing questions from conflict anchors."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from app.services.debate.round_evaluator import _parse_json
from app.services.language_guard import chinese_system_prompt
from app.services.llm import generate, is_llm_error

logger = logging.getLogger(__name__)

_KIND_PRIORITY = {
    "verify_assumption": 0,
    "surface_constraint": 1,
    "resolve_binary": 2,
    "pick_cost": 3,
}
_VALID_KINDS = set(_KIND_PRIORITY)

_CONFIRM_MARKERS = ("没错", "对的", "是的", "确实", "成立", "就是", "承认", "会的")
_DENY_MARKERS = ("不", "没有", "并非", "谈不上", "未必", "不成立", "不是", "不会")
_CONFIRM_PREFIXES = ("是", "对", "会", "能", "可以")

_FOLLOWUP_SYSTEM_PROMPT = chinese_system_prompt("只返回 JSON。")
_FALLBACK_LEAD_IN = "我们一直在替你推演，但其实没问过你心里真实的样子。"


class _DefaultRouter:
    async def generate(self, *, task, prompt, system=None, temperature=0.7, max_tokens=1024):
        return await generate(prompt=prompt, system=system, temperature=temperature, max_tokens=max_tokens)


@dataclass
class FollowupCandidate:
    question_id: str
    target_tension_id: str
    kind: str
    raw_focus: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "target_tension_id": self.target_tension_id,
            "kind": self.kind,
            "raw_focus": self.raw_focus,
        }


@dataclass
class FollowupQuestion:
    question_id: str
    target_tension_id: str
    kind: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "target_tension_id": self.target_tension_id,
            "kind": self.kind,
            "text": self.text,
        }


class FollowupComposer:
    """Two-step follow-up generation: structured selection then synthesizer voicing.

    Heuristic-only verdict classification: an answer is confirmed/denied when the
    first clause clearly affirms/negates, otherwise refined (the spec's "default
    to refined when unclear").
    """

    def __init__(self, router=None, *, min_questions: int = 1, max_questions: int = 3):
        self._router = router or _DefaultRouter()
        self._min = min_questions
        self._max = max_questions

    @staticmethod
    def _anchors_text(tension_map, engagement_record, spine) -> str:
        parts: list[str] = []
        if tension_map is not None:
            parts.append(tension_map.to_prompt_text())
        if engagement_record is not None and engagement_record.unresolved_disagreements:
            parts.append("未解决的分歧：" + "；".join(engagement_record.unresolved_disagreements))
        if engagement_record is not None and engagement_record.divergence_map:
            parts.append("分歧图：" + engagement_record.divergence_map)
        if spine is not None and getattr(spine, "active_questions", None):
            parts.append("活跃问题：" + "；".join(q.prompt_text for q in spine.active_questions))
        return "\n".join(parts) if parts else "（暂无明确锚点）"

    async def _select_candidates(
        self, tension_map, engagement_record, spine, recent_statements, *, asked_texts=None
    ) -> list[FollowupCandidate]:
        anchors_text = self._anchors_text(tension_map, engagement_record, spine)
        recent_text = "\n".join(
            f"- {s.get('agent_name', s.get('agent_id', '?'))}: {s.get('content', '')}"
            for s in (recent_statements or [])[-8:]
        )
        asked_block = ""
        if asked_texts:
            asked_block = (
                "## Already asked user questions (do not repeat; choose a fresh angle)\n"
                + "\n".join(f"- {t}" for t in asked_texts)
                + "\n\n"
            )
        prompt = (
            "你在辅助一场多智能体辩论。现在到了一个追问环节：要从当前冲突锚点里，"
            "挑出最该直接问用户本人的几个点。\n\n"
            "## 当前冲突锚点\n"
            f"{anchors_text}\n\n"
            "## 最近的发言\n"
            f"{recent_text}\n\n"
            f"{asked_block}"
            "## 任务\n"
            "挑出 1-3 个最该问用户的点。优先级：\n"
            "1. verify_assumption：各声音一直替用户假设、却没验证过的强判断；\n"
            "2. surface_constraint：还没问过用户真实约束（时间/钱/精力/责任）的角度；\n"
            "3. resolve_binary：被反复争论却没收口的二选一；\n"
            "4. pick_cost：需要用户在两种代价里选此刻更扛不住的那个。\n"
            "每个点必须绑定一个 target_tension_id（来自上面锚点里的张力编号）。\n"
            "只返回 JSON：\n"
            "```\n"
            "{\n"
            '  "candidates": [\n'
            '    {"target_tension_id": "<张力编号>", "kind": "verify_assumption|surface_constraint|resolve_binary|pick_cost", "raw_focus": "<这个点聚焦的具体内容>"}\n'
            "  ]\n"
            "}\n"
            "```\n"
            "只返回 JSON 对象。"
        )
        try:
            raw = await self._router.generate(
                task="followup_selection",
                prompt=prompt,
                system=_FOLLOWUP_SYSTEM_PROMPT,
                temperature=0.4,
            )
        except Exception:
            logger.exception("followup selection LLM call failed")
            return []
        data = _parse_json(raw) if raw else None
        if not data or "candidates" not in data:
            return []
        return self._build_candidates(data.get("candidates", []))

    @staticmethod
    def _build_candidates(raw_list) -> list[FollowupCandidate]:
        out: list[FollowupCandidate] = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            raw_focus = str(item.get("raw_focus") or "").strip()
            if not raw_focus:
                continue
            target = str(item.get("target_tension_id") or "").strip() or "0"
            kind = item.get("kind") if item.get("kind") in _VALID_KINDS else "pick_cost"
            out.append(FollowupCandidate(question_id="", target_tension_id=target, kind=kind, raw_focus=raw_focus))
        return out

    @staticmethod
    def _normalize_tid(tid) -> str:
        """Collapse LLM tension-id variants ("张力 #1" / "tension:1" / "1") to one key."""
        s = str(tid or "").strip().lower()
        digits = re.findall(r"\d+", s)
        if digits:
            return digits[-1]
        return re.sub(r"[^\w一-鿿]+", "", s) or "0"

    @staticmethod
    def _char_bigrams(text) -> set[str]:
        s = re.sub(r"[^\w一-鿿]+", "", str(text or ""))
        if len(s) < 2:
            return {s} if s else set()
        return {s[i:i + 2] for i in range(len(s) - 1)}

    @classmethod
    def _is_duplicate_focus(cls, focus, asked_texts) -> bool:
        """True when the candidate focus mostly restates an already-asked question."""
        focus_bigrams = cls._char_bigrams(focus)
        if not focus_bigrams:
            return False
        for text in asked_texts or ():
            asked_bigrams = cls._char_bigrams(text)
            if not asked_bigrams:
                continue
            if len(focus_bigrams & asked_bigrams) / len(focus_bigrams) >= 0.5:
                return True
        return False

    def _rank_and_cap(
        self, candidates, asked_tension_ids=frozenset(), asked_texts=(),
    ) -> list[FollowupCandidate]:
        ranked = sorted(candidates, key=lambda c: _KIND_PRIORITY.get(c.kind, 99))
        # card 2-C: pre-exclude already-asked tensions (ids normalized — the
        # LLM labels the same tension "张力 #1" in one gate and "1" in the next)
        seen_tensions: set[str] = {self._normalize_tid(t) for t in asked_tension_ids}
        seen_focus: set[str] = set()
        result: list[FollowupCandidate] = []
        for c in ranked:
            tid = self._normalize_tid(c.target_tension_id)
            if tid in seen_tensions or c.raw_focus in seen_focus:
                continue
            if self._is_duplicate_focus(c.raw_focus, asked_texts):
                continue
            seen_tensions.add(tid)
            seen_focus.add(c.raw_focus)
            result.append(c)
            if len(result) >= self._max:
                break
        return result

    @staticmethod
    def _dominant_tid(tension_map) -> str:
        if tension_map is None:
            return "0"
        if tension_map.dominant_tension_id is not None:
            return str(tension_map.dominant_tension_id)
        if tension_map.tensions:
            return str(tension_map.tensions[0].id)
        return "0"

    def _fallback_candidates(self, tension_map, engagement_record, spine) -> list[FollowupCandidate]:
        cands: list[FollowupCandidate] = []
        dom = self._dominant_tid(tension_map)
        if engagement_record is not None:
            for angle in (engagement_record.unresolved_disagreements or [])[:2]:
                cands.append(FollowupCandidate("", dom, "surface_constraint", angle))
        if tension_map is not None:
            for angle in (tension_map.unaddressed_angles or [])[:2]:
                cands.append(FollowupCandidate("", dom, "surface_constraint", angle))
            if tension_map.tensions:
                top = tension_map.tensions[0]
                cands.append(FollowupCandidate("", str(top.id), "resolve_binary", top.description))
        if spine is not None and getattr(spine, "active_questions", None):
            q = spine.active_questions[0]
            tid = q.tension_ids[0] if getattr(q, "tension_ids", None) else dom
            cands.append(FollowupCandidate("", str(tid), "pick_cost", q.prompt_text))
        if not cands:
            core = getattr(spine, "core_contradiction", None) or "核心矛盾"
            cands.append(FollowupCandidate("", "0", "pick_cost", core))
        return cands

    async def compose(
        self,
        *,
        after_phase,
        tension_map,
        engagement_record,
        spine,
        recent_statements,
        synthesizer_card,
        prior_questions=None,
    ):
        """Return (lead_in, [FollowupQuestion, ...]); never raises for empty LLM output."""
        asked_ids = {
            str(q.get("target_tension_id"))
            for q in (prior_questions or [])
            if q.get("target_tension_id") is not None
        }
        asked_texts = [q.get("text") for q in (prior_questions or []) if q.get("text")]
        candidates = self._rank_and_cap(
            await self._select_candidates(
                tension_map,
                engagement_record,
                spine,
                recent_statements,
                asked_texts=asked_texts,
            ),
            asked_tension_ids=asked_ids,
            asked_texts=asked_texts,
        )
        if len(candidates) < self._min:
            fallback = self._rank_and_cap(
                self._fallback_candidates(tension_map, engagement_record, spine),
                asked_tension_ids=asked_ids,
                asked_texts=asked_texts,
            )
            existing = {self._normalize_tid(c.target_tension_id) for c in candidates}
            candidates = (
                candidates
                + [c for c in fallback if self._normalize_tid(c.target_tension_id) not in existing]
            )[: self._max]
        if not candidates:
            return "", []
        for index, candidate in enumerate(candidates, start=1):
            candidate.question_id = f"fq{index}"
        return await self._voice(candidates, synthesizer_card, after_phase)

    async def _voice(self, candidates, synthesizer_card, after_phase):
        from app.services.debate import prompt_composer

        prompt = prompt_composer.compose_followup_prompt(
            synthesizer_card or {},
            candidates=[c.to_dict() for c in candidates],
            after_phase=getattr(after_phase, "value", str(after_phase)),
        )
        text_by_id: dict[str, str] = {}
        lead_in = ""
        try:
            raw = await self._router.generate(
                task="followup_voicing",
                prompt=prompt,
                system=_FOLLOWUP_SYSTEM_PROMPT,
                temperature=0.5,
            )
        except Exception:
            logger.exception("followup voicing LLM call failed; using template text")
            raw = ""
        data = _parse_json(raw) if raw and not is_llm_error(raw) else None
        if data:
            lead_in = str(data.get("lead_in") or "").strip()
            for item in data.get("questions", []) or []:
                if not isinstance(item, dict):
                    continue
                qid = item.get("question_id")
                text = str(item.get("text") or "").strip()
                if qid and text:
                    text_by_id[qid] = text
        if not lead_in:
            lead_in = _FALLBACK_LEAD_IN
        questions = [
            FollowupQuestion(
                question_id=c.question_id,
                target_tension_id=c.target_tension_id,
                kind=c.kind,
                text=text_by_id.get(c.question_id) or self._fallback_question_text(c),
            )
            for c in candidates
        ]
        return lead_in, questions

    @staticmethod
    def _fallback_question_text(candidate: FollowupCandidate) -> str:
        focus = candidate.raw_focus
        if candidate.kind == "verify_assumption":
            return f"关于「{focus}」，我们一直默认它对你成立——它真的成立吗？"
        if candidate.kind == "resolve_binary":
            return f"「{focus}」这两头，在你的现实里能不能同时来，还是只能选一个？"
        if candidate.kind == "surface_constraint":
            return f"「{focus}」上，你真实的时间、精力或底气到底允许什么？"
        return f"「{focus}」里，此刻更扛不住的是哪一个代价？"

    @staticmethod
    def classify_verdict(answer: str) -> str:
        text = (answer or "").strip()
        if not text:
            return "refined"
        head = re.split(r"[，。！？,!?\s]", text, maxsplit=1)[0]
        if any(marker in head for marker in _CONFIRM_MARKERS):
            return "confirmed"
        if any(marker in head for marker in _DENY_MARKERS):
            return "denied"
        if head.startswith(_CONFIRM_PREFIXES):
            return "confirmed"
        return "refined"
