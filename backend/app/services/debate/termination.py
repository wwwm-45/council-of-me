"""
Debate termination helpers.

Character-set Jaccard convergence functions have been removed.
Convergence gating is now handled by the LLM-based Round Evaluator.
"""

# Chinese keywords indicating a concession or stance adjustment
_CONCESSION_KEYWORDS = [
    "你说得对", "有道理", "我承认", "我同意",
    "确实如此", "我调整", "你提醒了我",
    "我之前没考虑到", "我需要修正", "合理的",
    "你的观点让我", "我认同", "这一点我接受",
    "我的立场有所", "我需要承认",
]


def detect_concession(text: str) -> bool:
    """Simple keyword-based concession detection."""
    return any(kw in text for kw in _CONCESSION_KEYWORDS)
