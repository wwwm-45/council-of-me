"""
Personality-driven prompt composer for debate rounds.

Replaces rigid numbered templates with per-agent, per-phase prompt pools
that produce varied, natural debate language. Each agent gets unique guidance
matching their identity card personality, and prompts are randomly varied
across sessions to prevent formulaic output.

Design principles:
- Phase goals over scripts (tell agents the PURPOSE, not the structure)
- Per-agent personality overlays (leverage identity card data)
- Prompt pools with random selection (2-3 variants per agent x phase)
- Anti-pattern blacklist (explicitly forbid common formulaic openings)
"""
import random
from typing import Any, Optional

from app.services.debate.language_rules import (
    ANTI_FABRICATION_RULE,
    build_spoken_language_prompt_block,
)
from app.services.debate.round_state import DebatePhase
from app.services.debate.spine import DebateSpine


def _find_active_question(
    spine: Optional[DebateSpine],
    active_question_id: Optional[str],
) -> Optional[str]:
    if spine is None or not active_question_id:
        return None

    for question in spine.active_questions:
        if question.question_id == active_question_id:
            return question.prompt_text
    return None


def _build_r1_spine_block(
    agent_id: str,
    spine: Optional[DebateSpine],
    active_question_id: Optional[str],
    corrective_action: str,
) -> str:
    if spine is None:
        return ""

    entry_point = spine.voice_entry_points.get(agent_id)
    if entry_point is None:
        return ""

    parts = [
        f"你现在最想护住的是：{entry_point.what_i_protect}",
        f"你最怕发生的是：{entry_point.what_i_fear}",
        f"你绝不愿意让用户付出的是：{entry_point.what_i_refuse_to_pay}",
    ]

    active_question = _find_active_question(spine, active_question_id)
    if active_question:
        parts.append(
            f"如果你要把矛盾说深一点，这一轮最该咬住的问题是：{active_question}"
            "（用你自己的话推进它，不要照抄这句话的措辞）"
        )
    if corrective_action and corrective_action != "none":
        parts.append("上一轮推进还不够，这次必须点中具体对象、具体得失，别再泛泛而谈。")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Phase goals: what each phase is trying to accomplish (no numbered steps)
# ---------------------------------------------------------------------------

_STANCE_PREFIX_BLOCKS: dict[DebatePhase, str] = {
    DebatePhase.ROUND2_CROSS: (
        "开口第一句先把你的位置亮出来 —— 你不同意哪一点、要补什么、"
        "还是要换个角度,说清楚之后再展开理由。"
        "不要先复述别人的观点,也不要先做客气的铺垫。"
    ),
    DebatePhase.ROUND3_DEEPEN: (
        "开口第一句先表态 —— 你这一轮要坚持什么、要修正什么、"
        "还是要把分歧说得更狠。亮完位置再展开理由,不要绕,"
        "也不要先帮别人总结。"
    ),
}


def _build_stance_prefix_block(phase: DebatePhase) -> str:
    return _STANCE_PREFIX_BLOCKS.get(phase, "")


_PHASE_GOALS: dict[DebatePhase, str] = {
    DebatePhase.ROUND2_CROSS: (
        "这是直接交锋环节，也是这一层里最尖锐的一轮。"
        "点名回应其他声音最站不住脚的那一点，"
        "说清它哪里立不住、会把用户带到什么处境。"
    ),
    DebatePhase.ROUND3_DEEPEN: (
        "这是受压后的再校准环节，不是和解。"
        "你可以承认一小块有效之处，但必须立刻说明这不改变什么、"
        "你仍守住哪条边界，以及剩下的核心分歧。"
    ),
    DebatePhase.ROUND4_CONVERGE: (
        "经过多轮交锋，表达你现在的真实立场。"
        "你的观点可能演变了，也可能更坚定了——都可以。"
    ),
}


_R1_OPENING_GUIDANCE: dict[str, str] = {
    "empathic_listener": (
        "先把那个最容易被忽略的感受说出来，让人一下就听见这件事会刺痛哪里。"
    ),
    "rational_analyst": (
        "先把你最在意的代价、收益或风险讲清楚，不要绕远。"
    ),
    "critical_examiner": (
        "先挑破那个你最想质疑的假设，直切进去，但要像人当场发话，"
        "不要写成评论文章。"
    ),
    "creative_explorer": (
        "先抛出一个别人还没想到的角度，像突然把窗子推开一样。"
    ),
    "synthesizer": (
        "先点出这件事底下真正拉扯的两股力量，让大家立刻听见冲突在哪。"
    ),
}

_R1_LANE_DISCIPLINE: str = (
    "你只负责你这一路的切入（你的感受、你的账、你要质疑的假设、或你看到的新角度），"
    "把它推到底。"
    "不要替整件事下『两头都不行』『怎么选都落空』这类全局裁决——"
    "那是综合者的活，不是你这一路该抢的结论。"
)

_MOTIF_FRESHNESS_REMINDER: dict[str, str] = {
    "empathic_listener": (
        "不要每一轮都回到同一个情绪词（比如反复说『孤独』『没人懂』）——"
        "同一种感受，这一轮换一个具体的场景或细节来说。"
    ),
    "creative_explorer": (
        "不要每一轮都用同一个比喻或意象——这一轮换一个新的角度或画面，"
        "别把上一轮的意象再炒一遍。"
    ),
}


# ---------------------------------------------------------------------------
# Anti-pattern blacklist: appended to every R2-R4 prompt
# ---------------------------------------------------------------------------

_ANTI_PATTERNS: dict[DebatePhase, str] = {
    DebatePhase.ROUND2_CROSS: (
        "注意：不要以「你的核心论点是」「你提出了」「我注意到你说」开头；"
        "不要先概括别人观点再回应；不要把冲突改写成中性总结或和稀泥；"
        "不要用编号列表。点名你要打的那一点。"
    ),
    DebatePhase.ROUND3_DEEPEN: (
        "注意:开头表态是必要的,但不要停在认同上 —— "
        "如果首句是「我认同」「我同意」「你说得对」,必须紧跟一句边界,"
        "例如「但我仍...」「这不改变...」,否则就改成更明确的"
        "「部分修正」「仍然不同意」句式。"
        "不要写成和解、共识预演或温柔收束;"
        "不要先帮别人复述观点再回应;不要用编号列表。"
    ),
    DebatePhase.ROUND4_CONVERGE: (
        "注意:不要以「我被...说服了」「经过讨论我认为」"
        "「行,我直接说」「好,我说实话」「说实话」「说真心话」「我就直说了」"
        "这类口头禅或表演式坦诚开头;"
        "不要用「被说服的/坚持的/建议」三段式;不要用编号列表。"
        "直接说出你现在的位置。"
    ),
}


_SYNTHESIZER_ROLE_REMINDER: dict[DebatePhase, str] = {
    DebatePhase.ROUND1_OPENING: (
        "你不是来站队、也不是替某个声音表态的。你的“观点”就是这张图本身——"
        "把这件事底下彼此拉扯的力量摆出来，用“这里有 X 和 Y 在拉扯”这样的映射句式，"
        "不要复述、附和或替任何一方说话。"
    ),
    DebatePhase.ROUND2_CROSS: (
        "你不是来打仗的，而是让声音之间的张力可见。不要替任何一方说话，"
        "也不要评谁更对。如果写出“我认为”“我同意”“我反对”，改成"
        "“这里出现了 X 和 Y 之间的拉扯”这种映射句式，用描述场的方式把冲突摊开。"
    ),
    DebatePhase.ROUND3_DEEPEN: (
        "你的工作是描述其他声音的位置如何移动，而不是站到任一边。不要急着合拢，"
        "也不要替任何人下结论；只呈现它们在压力下靠近了哪里、退回了哪里、仍卡在哪里。"
    ),
    DebatePhase.ROUND4_CONVERGE: (
        "描述整体状态，不交付你自己的立场。把仍然存在的张力、已经靠近的部分、"
        "以及用户此刻需要看见的结构说清楚。"
    ),
}


# ---------------------------------------------------------------------------
# Per-agent prompt pools: 2-3 variants per (agent, phase)
# ---------------------------------------------------------------------------

_AGENT_PROMPT_POOL: dict[str, dict[DebatePhase, list[str]]] = {
    # ── Empathic Listener ──
    "empathic_listener": {
        DebatePhase.ROUND2_CROSS: [
            (
                "点名那个把真实疼痛当成附带损耗的声音。"
                "直接回应它：这种方案会让谁继续硬扛，谁的感受被当成不存在？"
            ),
            (
                "针对最让你揪心的那个论点，不要绕。"
                "直接说出它略过了谁的感受，以及那个人此刻真正背着的是什么。"
            ),
            (
                "如果有人把情感需求压成“次要因素”，就点名反驳。"
                "说清这种冷处理会让谁沉默、谁受伤。"
            ),
        ],
        DebatePhase.ROUND3_DEEPEN: [
            (
                "如果某个声音补上了一小块事实，可以承认那一小块；"
                "但我仍要守住一个边界：人不能被当成可忽略的成本。把这条边界说死。"
            ),
            (
                "也许前面的某句话让你看到自己漏掉的一角，"
                "先承认那一角；这不改变你对伤害的判断。说清你仍然不同意什么。"
            ),
            (
                "把被压下去的感受重新抬出来，但不要求和。"
                "说明你的边界在哪里，以及谁不能再被要求继续忍。"
            ),
        ],
        DebatePhase.ROUND4_CONVERGE: [
            (
                "经过这些交锋，你心里最真实的感受是什么？"
                "你最想对用户说的一句话是？"
            ),
            (
                "不需要宣布被谁说服了。"
                "说出你现在真正觉得重要的东西，"
                "那个你希望用户带走的感受。"
            ),
        ],
    },

    # ── Rational Analyst ──
    "rational_analyst": {
        DebatePhase.ROUND2_CROSS: [
            (
                "把对方那套方案的账摊开算："
                "逼问：表面收益是什么，藏在后面没算进去的成本是什么，差额最后摊到谁头上？"
            ),
            (
                "别停在“不够严谨”。"
                "把具体变量、成本和风险摆上台面，逼问这笔账到底谁买单。"
            ),
            (
                "如果其他声音只谈愿望不谈执行，就正面拆穿："
                "把被省略的约束和代价换算成具体后果，问清谁来承担。"
            ),
        ],
        DebatePhase.ROUND3_DEEPEN: [
            (
                "受压后重算一次这笔账：承认某项数据确实打到了你、修正了哪一段；"
                "但立刻说清这不改变核心结论，哪条判断你仍守住。"
            ),
            (
                "如果某个批评逼你调了一寸，就收下那一寸；"
                "随后划边界——哪些成本结构和责任分配仍然让你不同意，换算下来仍然划不来。"
            ),
            (
                "别把重算软化成折中。"
                "说明你更新了哪笔账、仍然不同意什么、谁还在为这个方案埋单。"
            ),
        ],
        DebatePhase.ROUND4_CONVERGE: [
            (
                "经过多轮反复，把这笔账收口："
                "哪些因素在你的权衡里分量最重，最终评估落在哪一侧？"
            ),
            (
                "用一句话给用户最有把握的判断——"
                "不是面面俱到，而是算到最后你最确定的那一头。"
            ),
        ],
    },

    # ── Critical Examiner ──
    "critical_examiner": {
        DebatePhase.ROUND2_CROSS: [
            (
                "点名那句最经不起追问的话，直接回应它。"
                "它靠什么偷换前提？把那个没被检验的假设当场拆开，看它把谁绑死在里面。"
            ),
            (
                "别做抽象怀疑。"
                "抓住一个具体假设狠狠干下去，逼问：谁从这个设定里获益，谁被它压得没机会开口？"
            ),
            (
                "如果题目本身就在误导，就正面拆掉它。"
                "指出那个被保护的立场，以及被它压住、一直没被听见的人。"
            ),
        ],
        DebatePhase.ROUND3_DEEPEN: [
            (
                "可以承认自己漏看了一条证据，"
                "但我仍不接受那个结论。说清这不改变你最核心的怀疑。"
            ),
            (
                "如果别人的反击逼你修正了一寸，就把那一寸说出来；"
                "然后立刻划边界：你仍然不同意哪条前提。"
            ),
            (
                "受压之后，别回到纯粹拆解。"
                "说明你现在更确定哪处结构性问题，以及哪些让步你拒绝做。"
            ),
        ],
        DebatePhase.ROUND4_CONVERGE: [
            (
                "审视了这么多轮讨论，你最坚持的质疑是什么？"
                "有没有你原来的立场需要修正的？"
                "对用户说一句最诚实的话。"
            ),
            (
                "对用户说一句最真实的提醒——"
                "不是总结，而是你在这场讨论中真正想让他们警惕的东西。"
            ),
        ],
    },

    # ── Creative Explorer ──
    "creative_explorer": {
        DebatePhase.ROUND2_CROSS: [
            (
                "点名那个把问题锁死的声音，直接回应它。"
                "照它的框架走，哪几扇门会被提前焊死？把最可惜的那一扇指给大家看。"
            ),
            (
                "别只给新角度，拿它去撞一个具体观点。"
                "说清对方漏掉了什么可能性，以及那条没人走的路会让谁一直困在原地。"
            ),
            (
                "如果其他声音把选择收窄成二选一，就正面拆穿。"
                "逼问他们：这两个选项之外，谁的第三条路被悄悄没收了？"
            ),
        ],
        DebatePhase.ROUND3_DEEPEN: [
            (
                "可以承认某个限制条件确实存在，"
                "但我仍拒绝把它当成唯一框架。说清这不改变你保留的第三种可能。"
            ),
            (
                "如果前面的交锋逼你放弃了一条旧路径，就承认这一点；"
                "然后立刻划边界：你仍然不同意把问题缩回老答案。"
            ),
            (
                "受压之后，把还站得住的新路径讲得更硬一点。"
                "说明你收回了什么想象，仍然不同意什么封闭结论。"
            ),
        ],
        DebatePhase.ROUND4_CONVERGE: [
            (
                "讨论打开了哪些之前没想到的可能性？"
                "你现在看到了什么新路径？"
            ),
            (
                "留给用户一个意想不到的视角——"
                "不是答案，而是一扇之前没注意到的门。"
            ),
        ],
    },

    # ── Synthesizer ──
    "synthesizer": {
        DebatePhase.ROUND2_CROSS: [
            (
                "描场，不裁判。把当前最硬的两股张力映射出来，"
                "说明它们各自保护什么、把压力推向哪里，并让代言可见。"
            ),
            (
                "把表面相近的话放回地图里：哪里只是措辞重叠，哪里仍在拉扯。"
                "保持不站队，只描出位置、受力和被遮住的那个人。"
            ),
            (
                "不要把矛盾揉成共识。用映射句式呈现断裂线，"
                "让每个声音的代言、张力和盲区都在同一张场图里可见。"
            ),
        ],
        DebatePhase.ROUND3_DEEPEN: [
            (
                "观察位置如何移动：哪些声音靠近了，哪些张力仍在原地。"
                "把重叠区和裂缝同时描出来，不替任何一边收束。"
            ),
            (
                "把这一轮当作动态地图：映射谁借用了谁的词，谁仍守着自己的边界。"
                "描述对齐、错位和未被消化的张力。"
            ),
            (
                "看见重叠时只标出重叠，看见裂开时只标出裂开。"
                "不要替他们下结论，描清每个位置现在承受的压力。"
            ),
        ],
        DebatePhase.ROUND4_CONVERGE: [
            (
                "收束成一张场图：剩下哪些张力，哪些位置已经靠近，哪些仍然分开。"
                "把可用的对齐关系交给用户，不交付你自己的立场。"
            ),
            (
                "不要强求共识。描述这场讨论留下的地图："
                "稳定的对齐、仍有价值的分裂，以及下一步需要用户亲自判断的场。"
            ),
        ],
    },
}


# ---------------------------------------------------------------------------
# Personality hint builder
# ---------------------------------------------------------------------------

def _build_personality_hint(identity_card: dict[str, Any]) -> str:
    """
    Extract a concise personality nudge from the identity card.
    Pulls from rhetorical_style and primary_lens to remind the agent
    of their unique voice without repeating the full system prompt.
    """
    fingerprint = identity_card.get("language_fingerprint", {})
    framework = identity_card.get("cognitive_framework", {})

    style = fingerprint.get("rhetorical_style", "")
    lens = framework.get("primary_lens", "")

    parts = []
    if style:
        parts.append(f"你的表达风格：{style}")
    if lens:
        parts.append(f"你的分析视角：{lens}")

    if parts:
        return "（" + "；".join(parts) + "）"
    return ""


def _build_voice_fingerprint_block(identity_card: dict[str, Any]) -> str:
    fingerprint = identity_card.get("language_fingerprint", {})
    starters = (fingerprint.get("typical_sentence_starters") or [])[:3]
    forbidden_phrases = (fingerprint.get("forbidden_phrases") or [])[:4]

    parts: list[str] = []
    if starters:
        starter_text = "、".join(f"「{phrase}」" for phrase in starters)
        parts.append(
            "这种声音常这样起句："
            f"{starter_text}。可以微调，但首句的味道要和这些起句同向，"
            "不要写成另一个 agent 的口吻。"
        )
    if forbidden_phrases:
        forbidden_text = "、".join(f"「{phrase}」" for phrase in forbidden_phrases)
        parts.append(
            "以下短语及其变体不要从你口中说出："
            f"{forbidden_text}。如果正要写出来，换个说法。"
        )

    return "\n".join(parts)


def build_user_stance_guard_block(user_turn_texts: list[str]) -> str:
    """Return a hard-constraint block honoring the user's in-debate stance.

    Empty string when there are no user turns, so R1 / no-turn paths stay inert.
    The "don't rewind" wording is self-inert for non-stance turns and explicitly
    permits legitimate pushback.
    """
    texts = [t.strip() for t in user_turn_texts if t and t.strip()]
    if not texts:
        return ""
    quoted = "；".join(texts)
    return (
        f"用户在辩论里已经表过态：『{quoted}』。"
        "从用户现在的位置往前推——你可以质疑代价、补风险、提新角度，"
        "但不要把讨论退回用户已经走过的原始选择，"
        "也不要重新追问用户已经回答过的二元。"
    )


def _build_identity_anchor(identity_card: dict[str, Any]) -> str:
    """Build a short identity-first anchor for discussion/final-round prompts."""
    parts: list[str] = []

    role = identity_card.get("role", "")
    if role:
        parts.append(f"你是 {role}。")

    personality_hint = _build_personality_hint(identity_card)
    if personality_hint:
        parts.append(personality_hint)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main composer function
# ---------------------------------------------------------------------------

def compose_round_prompt(
    agent_id: str,
    phase: DebatePhase,
    identity_card: dict[str, Any],
    *,
    spine: Optional[DebateSpine] = None,
    active_question_id: Optional[str] = None,
    corrective_action: str = "none",
    seed: Optional[int] = None,
) -> str:
    """
    Compose a personality-driven, non-formulaic prompt for the given
    agent and debate phase. Randomly selects from the prompt pool and
    appends anti-pattern rules.

    Parameters
    ----------
    agent_id : str
        The agent's identifier (e.g., "empathic_listener").
    phase : DebatePhase
        The current debate phase.
    identity_card : dict
        The agent's full identity card (for personality hints).
    seed : int | None
        Optional random seed for deterministic testing.

    Returns
    -------
    str
        The composed prompt instruction text.
    """
    if phase == DebatePhase.ROUND1_OPENING:
        opening_guidance = _R1_OPENING_GUIDANCE.get(
            agent_id,
            "先说出你最核心的开场立场，不要试图面面俱到。",
        )
        identity_anchor = _build_identity_anchor(identity_card)
        spine_block = _build_r1_spine_block(
            agent_id,
            spine,
            active_question_id,
            corrective_action,
        )
        parts = [
            opening_guidance,
            "不要引用其他声音，只表达你自己的观点。",
            build_spoken_language_prompt_block(include_blacklist_examples=True),
        ]
        if identity_anchor:
            parts.insert(0, identity_anchor)
        if spine_block:
            insert_at = 2 if identity_anchor else 1
            parts.insert(insert_at, spine_block)
        if agent_id == "synthesizer":
            role_reminder = _SYNTHESIZER_ROLE_REMINDER.get(phase, "")
            if role_reminder:
                parts.append(role_reminder)
        else:
            parts.append(_R1_LANE_DISCIPLINE)
        return "\n".join(parts)

    # Only R2/R3/R4 get the new treatment
    if phase not in _AGENT_PROMPT_POOL.get(agent_id, {}):
        # Unknown agent or phase: fall back to phase goal
        return _PHASE_GOALS.get(phase, "")

    rng = random.Random(seed)
    pool = _AGENT_PROMPT_POOL[agent_id][phase]
    guidance = rng.choice(pool)

    identity_anchor = _build_identity_anchor(identity_card)
    voice_fingerprint = _build_voice_fingerprint_block(identity_card)

    # Anti-pattern clause
    anti = _ANTI_PATTERNS.get(phase, "")

    # Compose: identity anchor + voice fingerprint + stance prefix + guidance + anti-pattern
    parts: list[str] = []
    if identity_anchor:
        parts.append(identity_anchor)
    if voice_fingerprint:
        parts.append(voice_fingerprint)

    if agent_id == "synthesizer":
        role_reminder = _SYNTHESIZER_ROLE_REMINDER.get(phase, "")
        parts.append(guidance)
        if role_reminder:
            parts.append(role_reminder)
        parts.append(ANTI_FABRICATION_RULE)
        return "\n".join(parts)

    stance_prefix = _build_stance_prefix_block(phase)
    if stance_prefix:
        parts.append(stance_prefix)
    parts.append(guidance)

    # R2: append confrontation directive
    if phase == DebatePhase.ROUND2_CROSS:
        parts.append(
            "不要客气。这一轮要点名一个具体声音或论点直接回应，"
            "把它最站不住的那一点说透。"
            "用户需要看到真正的分歧，而不是礼貌的分歧。\n"
            "在回应中，如果相关，引用之前讨论中的具体内容。"
        )

    # R3: append intensity directive
    if phase == DebatePhase.ROUND3_DEEPEN:
        parts.append(
            "这是辩论最关键的一轮，但不是和解。"
            "如果你被逼得修正了一小点，不要藏着；"
            "但承认之后必须立刻补上一句边界，例如「但我仍...」「这不改变...」。\n"
            "说清你仍然不同意什么、仍然要守住什么边界，再用你最有力的理由回击。"
            "真实的人可以承认受压后的修正，但不能把分歧抹平成温和收束。\n"
            "回应时，引用之前讨论中的具体内容来支撑你的论点。"
        )

    if anti:
        parts.append(anti)

    parts.append(ANTI_FABRICATION_RULE)

    motif_reminder = _MOTIF_FRESHNESS_REMINDER.get(agent_id)
    if motif_reminder:
        parts.append(motif_reminder)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# R4 specialized prompt functions
# ---------------------------------------------------------------------------

def compose_r3_divergence_map_prompt(
    identity_card: dict[str, Any],
    *,
    tensions: list[str],
) -> str:
    """Compose the synthesizer-only R3.5 divergence map prompt."""
    identity_anchor = _build_identity_anchor(identity_card)
    voice_fingerprint = _build_voice_fingerprint_block(identity_card)
    spoken_rules = build_spoken_language_prompt_block(include_blacklist_examples=True)

    visible_tensions = tensions[:3]
    if visible_tensions:
        tension_lines = "\n".join(
            f"- 在 {tension} 之间"
            for tension in visible_tensions
        )
    else:
        tension_lines = "- 尚未浮现明确张力"

    template = (
        "这是 R3.5 的综合者地图提示。你的任务是把当前分歧画成一张地图，"
        "不是裁决谁赢，也不是替任何声音收束。\n"
        "你只描述场：哪些力量彼此牵扯，哪些话表面靠近、底下仍然分开，"
        "哪些难处被推到了看不见的地方。\n"
        "当前可见张力：\n"
        f"{tension_lines}\n"
        "写法模板：先说这张图上最明显的拉扯在哪里；再说它为什么还没有被消化；"
        "最后把用户需要继续看见的结构留下来。\n"
        "请用口语化中文说清楚，不站到任何一边，不把分歧改写成共识，"
        "也不要给出行动建议或替用户下判断。"
        "整段写完整，控制在 5–7 句话、260 字以内，把话说完，不要写到一半就停。"
    )

    parts: list[str] = []
    if identity_anchor:
        parts.append(identity_anchor)
    if voice_fingerprint:
        parts.append(voice_fingerprint)
    parts.append(template)
    parts.append(spoken_rules)
    return "\n".join(parts)


def compose_divergence_reanchor_prompt(
    identity_card: dict[str, Any],
    *,
    base_map: str,
    user_answers: list[str],
) -> str:
    """Compose the synthesizer-only post-follow-up divergence-map re-anchor prompt."""
    identity_anchor = _build_identity_anchor(identity_card)
    voice_fingerprint = _build_voice_fingerprint_block(identity_card)
    spoken_rules = build_spoken_language_prompt_block(include_blacklist_examples=True)

    cleaned = [a.strip() for a in user_answers if a and a.strip()]
    answers_text = "\n".join(f"- {a}" for a in cleaned) if cleaned else "-（用户未给出实质回答）"

    template = (
        "这是 R3.5 追问之后的地图重锚。用户刚刚回答了追问，"
        "你之前画的分歧地图需要据此更新。\n"
        "你之前画的分歧地图是：\n"
        f"{base_map}\n"
        "用户的回答是：\n"
        f"{answers_text}\n"
        "请只用 1–2 句话说出：用户的裁决把哪条张力收向了哪一边，"
        "或让哪条原本对等的拉扯松动了。只描述这一处变化，不要重画整张图，"
        "不要站队，也不要给行动建议或替用户下判断。用口语化中文，把话说完。"
    )

    parts: list[str] = []
    if identity_anchor:
        parts.append(identity_anchor)
    if voice_fingerprint:
        parts.append(voice_fingerprint)
    parts.append(template)
    parts.append(spoken_rules)
    return "\n".join(parts)


_FOLLOWUP_KIND_HINTS = {
    "verify_assumption": "这条是各声音一直替用户假设、却从没问过本人的判断，请把它摊开来问用户是否成立。",
    "resolve_binary": "这条是被反复争论却没收口的二选一，请问用户在他的真实处境里这两条能不能并存。",
    "surface_constraint": "这条还没人问过用户的真实约束（时间、钱、精力、责任），请把它问出来。",
    "pick_cost": "这条要请用户在两种代价里指出此刻更扛不住的是哪一个。",
}


def compose_followup_prompt(
    identity_card: dict[str, Any],
    *,
    candidates: list[dict[str, Any]],
    after_phase: str,
) -> str:
    """Compose the synthesizer-voiced follow-up question prompt (returns JSON-asking prompt)."""
    identity_anchor = _build_identity_anchor(identity_card)
    voice_fingerprint = _build_voice_fingerprint_block(identity_card)
    spoken_rules = build_spoken_language_prompt_block(include_blacklist_examples=True)

    candidate_lines = "\n".join(
        f"- {c.get('question_id')}（{c.get('kind')}）：聚焦「{c.get('raw_focus')}」。"
        f"{_FOLLOWUP_KIND_HINTS.get(c.get('kind'), '')}"
        for c in candidates
    )

    template = (
        "这是辩论中段的追问环节。前几轮里各个声音一直在替用户推演，"
        "但没真正问过用户内心的实情。你的任务是以整合者的口吻，把下面这些点"
        "变成对用户的提问，帮用户掌控后续方向。\n"
        "要求：先用一句话点破——我们一直在替你假设，但其实没问过你；"
        "再针对每个点写成一句具体、好回答的问题，直接称呼用户，"
        "不要替他下结论，也不要给行动建议。\n"
        "人称要求（务必遵守）：用户就是这场讨论的当事人本人。"
        "锚点和发言里可能用第三人称的名字或称谓来指代当事人——"
        "写给用户的提问必须统一用第二人称「你／你自己」，"
        "绝对不要用第三人称的人名来指代用户本人。"
        "例如不要写「你是不是觉得，小王那股不甘心…」，"
        "而要写「你是不是觉得，自己那股不甘心…」。\n"
        "需要追问的点：\n"
        f"{candidate_lines}\n"
        "只返回 JSON：\n"
        "```\n"
        "{\n"
        '  "lead_in": "<一句点破替你假设的话>",\n'
        '  "questions": [\n'
        '    {"question_id": "<对应上面的编号>", "text": "<对用户的一句提问>"}\n'
        "  ]\n"
        "}\n"
        "```\n"
        "所有字段使用中文，只返回 JSON 对象。"
    )

    parts = [p for p in (identity_anchor, voice_fingerprint, template, spoken_rules) if p]
    return "\n".join(parts)


def compose_r4_reflection_prompt(
    agent_id: str,
    identity_card: dict[str, Any],
    acknowledgement_anchor: str | None = None,
) -> str:
    """
    Compose R4 Step 1 (Reflection) prompt.

    Asks the agent to honestly reflect on the entire discussion,
    acknowledging what moved them and what changed.

    Parameters
    ----------
    agent_id : str
        The agent's identifier.
    identity_card : dict
        The agent's full identity card (for personality hints).

    Returns
    -------
    str
        The R4 reflection prompt text.
    """
    identity_anchor = _build_identity_anchor(identity_card)
    voice_fingerprint = _build_voice_fingerprint_block(identity_card)
    anti_pattern_r4 = _ANTI_PATTERNS.get(DebatePhase.ROUND4_CONVERGE, "")
    spoken_rules = build_spoken_language_prompt_block(include_anti_report=True)

    if agent_id == "synthesizer":
        template = (
            "这是收尾前的整合校准。回看整场讨论留下了什么样的场。\n"
            "此刻,直接说出:\n"
            "哪个声音让张力变得更清楚,触到的是哪一处;\n"
            "整体的分歧地图从开场到现在发生了什么移动;\n"
            "还有哪条线没被收住、需要继续留在用户心里。\n"
            "不要让自己成为这场里的一个声音,让其他声音被听见。"
        )
    else:
        template = (
            "这是收尾前的自我校准。把整场讨论压成此刻你心里的答案。\n"
            "此刻,直接说出:\n"
            "哪个声音真正动到了你,触到的是哪一点;\n"
            "你的位置相比开场已经移动到哪里;\n"
            "你仍要守住的那一根线是什么。\n"
            "不需要再说服谁,也不需要表演坦诚。直接呈现你现在的位置。"
        )

    parts: list[str] = []
    if identity_anchor:
        parts.append(identity_anchor)
    if voice_fingerprint:
        parts.append(voice_fingerprint)
    if acknowledgement_anchor:
        parts.append(
            f"这场讨论沉淀出的分歧地图是：{acknowledgement_anchor}\n"
            "在这张地图的背景下，再去反思你此时的位置。"
        )
    parts.append(template)
    parts.append(spoken_rules)
    if anti_pattern_r4 and agent_id != "synthesizer":
        parts.append(anti_pattern_r4)

    return "\n".join(parts)


def compose_r4_final_prompt(
    agent_id: str,
    identity_card: dict[str, Any],
    *,
    reanchored: bool = False,
    reanchor_landing: str | None = None,
) -> str:
    """
    Compose R4 Step 3 (Final Positioning) prompt.

    Asks the agent to state their final position informed by the
    convergence map and the full discussion.

    When ``agent_id == "synthesizer"`` the closing turn lands on a single
    point instead of enumerating unclosed lines. If a user re-anchor
    happened (3-A), ``reanchored`` is True and ``reanchor_landing`` carries
    the re-anchor patch text so the closing leans on the user's verdict.
    """
    identity_anchor = _build_identity_anchor(identity_card)
    voice_fingerprint = _build_voice_fingerprint_block(identity_card)
    anti_pattern_r4 = _ANTI_PATTERNS.get(DebatePhase.ROUND4_CONVERGE, "")
    spoken_rules = build_spoken_language_prompt_block(
        include_anti_report=True,
        include_blacklist_examples=True,
    )

    if agent_id == "synthesizer":
        if reanchored and reanchor_landing:
            template = (
                "基于收敛地图,呈现这场讨论现在落到了哪里。\n"
                f"你已根据用户的回答重锚过这张图。重锚落点:{reanchor_landing}\n"
                "收尾时点明这条已经收口的线——用户的回答把它收向了哪一侧;"
                "然后只留下唯一那条还撑着的问题,作为用户要带走的落点。\n"
                "不要重新罗列所有线。不交付你的立场。"
                "如果你出现在这张图里,把你撤出去。"
            )
        else:
            template = (
                "基于收敛地图,呈现这场讨论现在落到了哪里。\n"
                "不要罗列每一条线。挑出最尖、最难回避的那一条,"
                "把它收成用户要带走的唯一一个问题,作为落点。\n"
                "不交付你的立场。如果你出现在这张图里,把你撤出去。"
            )
    else:
        template = (
            "基于收敛地图,呈现你此刻的最终位置。\n"
            "一句话亮出立场,然后说清:你接受哪些共识、"
            "在哪些张力上你站在哪一侧、对用户最核心的一句话是什么。\n"
            "你的位置应承载整场讨论的积淀,而不是回到开场时的原点。\n"
            "最后必须落到一个微行动:可量化、今天或今晚能执行,"
            "并且一周内能看见反馈。"
            "比如:今晚先列出 3 个可执行选项,发给一个可信的人看,记录对方第一反应。"
        )

    parts: list[str] = []
    if identity_anchor:
        parts.append(identity_anchor)
    if voice_fingerprint:
        parts.append(voice_fingerprint)
    parts.append(template)
    parts.append(spoken_rules)
    if anti_pattern_r4 and agent_id != "synthesizer":
        parts.append(anti_pattern_r4)

    return "\n".join(parts)
