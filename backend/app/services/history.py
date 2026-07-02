"""
Cross-session synthesis history: list, detail, compare, and pattern detection.
Operates on the session_syntheses table. Gracefully returns empty results when DB unavailable.
"""
import json
from typing import Any, Optional
from uuid import UUID


class SynthesisHistoryService:
    """Query and compare synthesis results across sessions."""

    def __init__(self, pool: Any = None):
        self._pool = pool

    async def list_user_syntheses(
        self, user_id: UUID, limit: int = 20, offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return compact synthesis cards for the user, newest first."""
        if not self._pool:
            return []

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT synthesis_id, session_id, created_at, synthesis_type,
                       core_dilemma, convergence_score, core_tensions, debate_rounds
                FROM session_syntheses
                WHERE user_id = $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                user_id, limit, offset,
            )

        results = []
        for r in rows:
            tensions = json.loads(r["core_tensions"]) if r["core_tensions"] else []
            results.append({
                "synthesis_id": str(r["synthesis_id"]),
                "session_id": str(r["session_id"]),
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "synthesis_type": r["synthesis_type"],
                "core_dilemma": r["core_dilemma"] or "",
                "convergence_score": r["convergence_score"],
                "tension_count": len(tensions),
                "debate_rounds": r["debate_rounds"],
            })
        return results

    async def get_synthesis_detail(self, synthesis_id: UUID) -> Optional[dict[str, Any]]:
        """Return full synthesis record with all JSONB fields."""
        if not self._pool:
            return None

        async with self._pool.acquire() as conn:
            r = await conn.fetchrow(
                "SELECT * FROM session_syntheses WHERE synthesis_id = $1",
                synthesis_id,
            )

        if not r:
            return None

        return {
            "synthesis_id": str(r["synthesis_id"]),
            "session_id": str(r["session_id"]),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "synthesis_type": r["synthesis_type"],
            "narrative": r["narrative"],
            "voice_positions": json.loads(r["voice_positions"]) if r["voice_positions"] else [],
            "core_tensions": json.loads(r["core_tensions"]) if r["core_tensions"] else [],
            "consensus_areas": json.loads(r["consensus_areas"]) if r["consensus_areas"] else [],
            "protective_intents": json.loads(r["protective_intents"]) if r["protective_intents"] else [],
            "meta": {
                "convergence_score": r["convergence_score"],
                "novelty_score": r["novelty_score"],
                "value_conflict_intensity": r["value_conflict_intensity"],
                "debate_rounds": r["debate_rounds"],
                "termination_mode": r["termination_mode"],
            },
            "core_dilemma": r["core_dilemma"] or "",
        }

    async def compare_syntheses(
        self, id_a: UUID, id_b: UUID,
    ) -> dict[str, Any]:
        """Compare two syntheses: shared tensions, value overlap, convergence delta."""
        a = await self.get_synthesis_detail(id_a)
        b = await self.get_synthesis_detail(id_b)

        if not a or not b:
            return {"error": "synthesis_not_found"}

        # Shared tension themes (fuzzy name matching)
        shared_tensions = self._find_shared_tensions(
            a.get("core_tensions", []), b.get("core_tensions", []),
        )

        # Value conflict overlap
        a_values = self._extract_value_pairs(a.get("core_tensions", []))
        b_values = self._extract_value_pairs(b.get("core_tensions", []))
        shared_values = a_values & b_values

        # Convergence delta
        a_conv = (a.get("meta") or {}).get("convergence_score") or 0
        b_conv = (b.get("meta") or {}).get("convergence_score") or 0

        return {
            "synthesis_a": {"id": str(id_a), "dilemma": a.get("core_dilemma", ""), "type": a.get("synthesis_type", "")},
            "synthesis_b": {"id": str(id_b), "dilemma": b.get("core_dilemma", ""), "type": b.get("synthesis_type", "")},
            "shared_tension_themes": shared_tensions,
            "shared_value_conflicts": list(shared_values),
            "convergence_delta": round(b_conv - a_conv, 2),
        }

    async def detect_patterns(self, user_id: UUID) -> list[dict[str, Any]]:
        """Detect cross-session patterns: recurring tensions, persistent values, convergence trends."""
        syntheses = await self.list_user_syntheses(user_id, limit=50)
        if len(syntheses) < 2:
            return []

        # Load full details for pattern analysis
        details = []
        for s in syntheses:
            detail = await self.get_synthesis_detail(UUID(s["synthesis_id"]))
            if detail:
                details.append(detail)

        patterns: list[dict[str, Any]] = []

        # Pattern 1: Recurring value conflicts
        value_counts: dict[str, list[str]] = {}
        for d in details:
            for t in d.get("core_tensions", []):
                vc = t.get("value_conflict", {})
                if vc.get("value_a") and vc.get("value_b"):
                    key = f"{vc['value_a']} vs {vc['value_b']}"
                    value_counts.setdefault(key, []).append(d["session_id"])
        for key, sessions in value_counts.items():
            if len(sessions) >= 2:
                patterns.append({
                    "pattern_type": "recurring_value_conflict",
                    "description": key,
                    "occurrence_count": len(sessions),
                    "session_ids": sessions,
                })

        # Pattern 2: Persistent protective intent values
        intent_counts: dict[str, list[str]] = {}
        for d in details:
            for pi in d.get("protective_intents", []):
                val = pi.get("underlying_value", "")
                if val:
                    intent_counts.setdefault(val, []).append(d["session_id"])
        for val, sessions in intent_counts.items():
            if len(sessions) >= 2:
                patterns.append({
                    "pattern_type": "persistent_protective_value",
                    "description": f"反复出现的保护价值：{val}",
                    "occurrence_count": len(sessions),
                    "session_ids": sessions,
                })

        # Pattern 3: Convergence trend
        if len(details) >= 3:
            scores = [
                (d.get("meta") or {}).get("convergence_score", 0)
                for d in reversed(details)  # chronological order
            ]
            if scores[-1] > scores[0] + 0.1:
                patterns.append({
                    "pattern_type": "convergence_trend",
                    "description": "收敛度呈上升趋势",
                    "occurrence_count": len(scores),
                    "session_ids": [d["session_id"] for d in details],
                })
            elif scores[-1] < scores[0] - 0.1:
                patterns.append({
                    "pattern_type": "convergence_trend",
                    "description": "收敛度呈下降趋势",
                    "occurrence_count": len(scores),
                    "session_ids": [d["session_id"] for d in details],
                })

        return patterns

    # -- Helpers --

    @staticmethod
    def _find_shared_tensions(
        tensions_a: list[dict], tensions_b: list[dict],
    ) -> list[dict[str, str]]:
        """Find tensions with similar names across two syntheses."""
        shared = []
        for ta in tensions_a:
            name_a = ta.get("name", "")
            for tb in tensions_b:
                name_b = tb.get("name", "")
                # Simple substring overlap
                if not name_a or not name_b:
                    continue
                words_a = set(name_a.replace(" vs ", " ").split())
                words_b = set(name_b.replace(" vs ", " ").split())
                overlap = len(words_a & words_b)
                if overlap >= 1 and (overlap / max(len(words_a), 1)) >= 0.3:
                    shared.append({"tension_a": name_a, "tension_b": name_b})
        return shared

    @staticmethod
    def _extract_value_pairs(tensions: list[dict]) -> set[str]:
        """Extract value conflict dimension strings from tensions."""
        pairs = set()
        for t in tensions:
            vc = t.get("value_conflict", {})
            if vc.get("value_a") and vc.get("value_b"):
                # Normalize order
                pair = tuple(sorted([vc["value_a"], vc["value_b"]]))
                pairs.add(f"{pair[0]} vs {pair[1]}")
        return pairs
