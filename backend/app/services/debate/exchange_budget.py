"""Dynamic exchange budgets for debate discussion phases."""
from __future__ import annotations

from app.services.debate.discussion_engine import ExchangeLimits


def compute_exchange_budget(
    complexity: str,
    tension_count: int,
) -> tuple[ExchangeLimits, ExchangeLimits]:
    """Return (R2 limits, R3 limits) for the given session complexity."""
    normalized = (complexity or "L2").upper()
    tension_count = max(0, tension_count)

    if normalized == "L1":
        if tension_count < 3:
            return (
                ExchangeLimits(6, 8, 1, 3),
                ExchangeLimits(5, 7, 1, 3),
            )
        return (
            ExchangeLimits(7, 10, 1, 3),
            ExchangeLimits(6, 8, 1, 3),
        )

    if normalized == "L3":
        if tension_count < 5:
            return (
                ExchangeLimits(10, 14, 1, 3),
                ExchangeLimits(8, 11, 1, 3),
            )
        return (
            ExchangeLimits(12, 16, 1, 3),
            ExchangeLimits(10, 14, 1, 3),
        )

    if tension_count < 3:
        return (
            ExchangeLimits(8, 10, 1, 3),
            ExchangeLimits(6, 8, 1, 3),
        )
    if tension_count <= 5:
        return (
            ExchangeLimits(9, 13, 1, 3),
            ExchangeLimits(7, 10, 1, 3),
        )
    return (
        ExchangeLimits(11, 15, 1, 3),
        ExchangeLimits(8, 12, 1, 3),
    )
