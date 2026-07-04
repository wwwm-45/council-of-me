"""Safety screening models (COMPLETE_PROCESS_FLOW Phase 0.3)."""
from enum import Enum
from dataclasses import dataclass


class SafetyLevel(str, Enum):
    SAFE = "SAFE"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass
class SafetyResult:
    level: SafetyLevel
    matched_keywords: list[str]

    def __post_init__(self) -> None:
        if not self.matched_keywords:
            self.matched_keywords = []
