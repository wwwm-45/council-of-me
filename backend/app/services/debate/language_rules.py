"""Shared language rules for debate prompts and post-generation repairs."""

import re
from collections.abc import Sequence

REPORT_TONE_BLACKLIST_PHRASES: tuple[str, ...] = (
    "本质上",
    "核心在于",
    "从更高维度看",
    "这反映了更深层的问题",
    "归根结底",
    "值得注意的是",
    "某种意义上",
    "致命的问题在于",
    # R4 口头禅 / 表演式坦诚
    "我直接说",
    "我就直说了",
    "说实话",
    "说真心话",
    "说真话",
)

ABSTRACT_JARGON_TERMS: tuple[str, ...] = (
    "系统性",
    "结构性",
    "叙事",
    "范式",
    "维度",
    "认知框架",
    "框架",
    "失衡",
)

BOUNDARY_ATTACK_TERMS: tuple[str, ...] = (
    "闭嘴",
    "太蠢",
    "蠢",
    "白痴",
    "废物",
    "垃圾",
)

ANTI_FABRICATION_RULE: str = (
    "不要凭空给出百分比、概率或统计数字当事实（如『成功率不到 20%』『80% 会失败』）；"
    "除非是用户原话里出现过的数字，否则改成定性说法（『多数』『风险不小』『见过不少翻车』），"
    "或者明确说这是你的猜测。"
)


def detect_report_tone(text: str) -> list[str]:
    """Return report-tone phrases found in *text*."""
    return [phrase for phrase in REPORT_TONE_BLACKLIST_PHRASES if phrase in text]


def detect_jargon_stack(text: str) -> list[str]:
    """Return jargon matches when the language sounds overly abstract."""
    matches = [term for term in ABSTRACT_JARGON_TERMS if term in text]
    if len(matches) >= 3:
        return matches
    if len(matches) >= 2 and any(term in text for term in ("维度", "框架", "范式")):
        return matches
    return []


def detect_boundary_attack(text: str) -> list[str]:
    """Return abusive or boundary-crossing terms found in *text*."""
    return [term for term in BOUNDARY_ATTACK_TERMS if term in text]


_THIRD_PERSON_PRONOUNS: tuple[str, ...] = ("她", "他", "TA", "ta")
_REFERENTIAL_VERBS: tuple[str, ...] = (
    "觉得", "应该", "担心", "需要", "后悔",
)


def detect_person_drift(text: str, user_name: str | None) -> list[str]:
    """Return ``[user_name]`` when the user is referred to in third person by name.

    Only name-anchored referential use fires:
    - name immediately followed by a third-person pronoun (no punctuation between);
    - name directly followed by a "talking-about" verb (not vocative).
    Vocative address ("小满，你…" / "小满你…") and bare pronouns are left alone.
    """
    name = (user_name or "").strip()
    body = (text or "").strip()
    if not name or not body:
        return []

    escaped = re.escape(name)
    pronoun_group = "|".join(re.escape(p) for p in _THIRD_PERSON_PRONOUNS)
    verb_group = "|".join(re.escape(v) for v in _REFERENTIAL_VERBS)

    matched = bool(re.search(rf"{escaped}\s*(?:{pronoun_group})", body))
    matched = matched or bool(re.search(rf"{escaped}(?:{verb_group})", body))

    return [name] if matched else []


def build_spoken_language_prompt_block(
    *,
    include_anti_report: bool = False,
    include_blacklist_examples: bool = False,
) -> str:
    """Return the shared spoken-language guidance block."""
    parts = [
        "像桌边当场说话，像真的在跟人争论，不要端成报告腔。",
        "句子尽量短一点、直接一点，让人能听见你的态度，不要绕成空话。",
        "可以直接顶回去，但不要粗口，不要辱骂，也不要攻击对方这个人。",
        "除了开头呼语叫名字，谈到用户本人一律用第二人称“你”，不要用“她/他”或名字来指称对方。",
    ]

    if include_anti_report:
        parts.append("不要写成总结报告，不要一上来就抽象概括整场讨论。")

    if include_blacklist_examples:
        examples = "、".join(f"“{phrase}”" for phrase in REPORT_TONE_BLACKLIST_PHRASES)
        parts.append(f"少用这些报告腔开头：{examples}。")

    parts.append(ANTI_FABRICATION_RULE)

    return "\n".join(parts)


def build_language_correction_text(
    issues: Sequence[str],
    *,
    agent_name: str | None = None,
    user_name: str | None = None,
) -> str:
    """Build a reusable rewrite instruction from language issues."""
    prefix = f"{agent_name}，" if agent_name else ""
    parts: list[str] = []

    if "report_tone" in issues:
        parts.append("别写成总结报告。保留原立场，用更短、更直接的话重说一遍。")
    if "jargon_stack" in issues:
        parts.append("少一点抽象词和概念堆叠，换成你会当面说出口的话。")
    if "boundary_attack" in issues:
        parts.append("可以直接反驳观点，但不要辱骂、羞辱或命令别人闭嘴。")
    if "person_drift" in issues:
        target = user_name.strip() if user_name and user_name.strip() else "对方"
        parts.append(
            f"你是在直接对{target}说话，别用第三人称称呼TA——"
            f"把“{target}她/{target}”这类说法改成“你”。"
        )

    if not parts:
        parts.append("保留原立场，但把话说得更像当场开口，而不是在写总结。")

    return prefix + "".join(parts)
