"""Single LLM call that summarizes a ReportContext into per-block prose.

The LLM ONLY summarizes plain text. All structure/HTML lives in the template.
Every field has a deterministic fallback so the report is always producible.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.services import llm
from app.services.report.builder import ReportContext

_SYSTEM = (
    "你是一名温和、克制的记录者。你只做总结与改写，"
    "禁止编造任何事实，禁止输出任何 HTML 或 Markdown 标签、列表符号或代码块，只输出纯文本。"
    "请用第二人称、温和、非评判、贴近来访者的语气。"
)

_MAX_TOKENS = 2000  # well under the DeepSeek 8192 ceiling


@dataclass
class ReportSummaries:
    headline: str
    dilemma_summary: str
    tension_summary: str
    consensus_summary: str
    closing_blessing: str


def _deterministic(ctx: ReportContext) -> ReportSummaries:
    voice_names = "、".join(v["name"] for v in ctx.voices if v.get("name")) or "你的内心声音"
    tension_names = "、".join(t["name"] for t in ctx.tensions if t.get("name"))
    dilemma = ctx.core_dilemma or "你的内心议题"
    return ReportSummaries(
        headline=ctx.headline_seed or dilemma,
        dilemma_summary=ctx.narrative or f"你在这次对话里探索了围绕「{dilemma}」的内心声音。",
        tension_summary=(
            f"这次对话浮现的核心张力包括：{tension_names}。"
            if tension_names
            else "这次对话中，你的不同声音各自表达了在意的东西。"
        ),
        consensus_summary=f"在分歧之下，{voice_names}仍守护着对你而言重要的东西。",
        closing_blessing="谢谢你愿意倾听自己内心的不同声音。照顾好自己。",
    )


def _parse(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text[3:]
        if text[:4].lower() == "json":
            text = text[4:]
        if "```" in text:
            text = text[: text.index("```")]
        text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(raw[start : end + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def _build_prompt(ctx: ReportContext) -> str:
    return (
        "请阅读以下这次「内心议会」对话的结构化要点（JSON），为每个区块各写一段简短、温和的中文总结。\n"
        "其中 narrative 是已写好的综合叙述，请以它为基础精修 dilemma_summary，保留其核心意象，不要另起炉灶；\n"
        "headline 请以 key_insight 为种子提炼，不超过20字。\n"
        "严格只输出一个 JSON 对象，键为：\n"
        "headline（一句话主题，不超过20字）、"
        "dilemma_summary（核心困境与内心声音）、"
        "tension_summary（核心张力与冲突）、"
        "consensus_summary（共识与保护性意图）、"
        "closing_blessing（给来访者的温和寄语，2-3句）。\n"
        "请自然概括，不要逐字复制原文；不得编造未提供的信息。\n\n"
        f"结构化要点：\n{json.dumps(ctx.to_digest(), ensure_ascii=False, indent=2)}"
    )


async def summarize(ctx: ReportContext) -> ReportSummaries:
    fallback = _deterministic(ctx)
    raw = await llm.generate(
        _build_prompt(ctx), system=_SYSTEM, temperature=0.4, max_tokens=_MAX_TOKENS
    )
    if llm.is_llm_error(raw):
        return fallback
    data = _parse(raw)
    if not data:
        return fallback

    def pick(key: str, fb: str) -> str:
        val = data.get(key)
        return val.strip() if isinstance(val, str) and val.strip() else fb

    return ReportSummaries(
        headline=pick("headline", fallback.headline),
        dilemma_summary=pick("dilemma_summary", fallback.dilemma_summary),
        tension_summary=pick("tension_summary", fallback.tension_summary),
        consensus_summary=pick("consensus_summary", fallback.consensus_summary),
        closing_blessing=pick("closing_blessing", fallback.closing_blessing),
    )
