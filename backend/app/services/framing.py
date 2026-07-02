"""
Apply user framing preference to agent display name (COMPLETE_PROCESS_FLOW 0.2).
"""
from typing import Dict

FRAMING_MAP: Dict[str, Dict[str, str]] = {
    "inner_parts": {
        "Empathic Listener": "你的情感守护者",
        "Rational Analyst": "你的理性管理者",
        "Critical Examiner": "你的质疑声音",
        "Creative Explorer": "你的可能性探索者",
        "Synthesizer": "你的整合自我",
    },
    "perspective": {
        "Empathic Listener": "情感视角",
        "Rational Analyst": "理性视角",
        "Critical Examiner": "批判视角",
        "Creative Explorer": "创意视角",
        "Synthesizer": "综合视角",
    },
    "advisory": {
        "Empathic Listener": "共情顾问",
        "Rational Analyst": "战略顾问",
        "Critical Examiner": "风险顾问",
        "Creative Explorer": "创新顾问",
        "Synthesizer": "首席顾问",
    },
    "neutral": {},
}


def apply_framing(preference: str, agent_canonical_name: str) -> str:
    """Return display name for agent given user framing preference."""
    m = FRAMING_MAP.get(preference) or FRAMING_MAP.get("neutral") or {}
    return m.get(agent_canonical_name, agent_canonical_name)
