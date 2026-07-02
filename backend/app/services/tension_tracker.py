"""Deterministic tension-card reducer for Phase 1 elicitation."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from app.models.elicitation import (
    CardLayer,
    TensionCard,
    normalize_comparable_text,
    voice_name_is_label,
)


ACTIVE_STATUSES = {"surfaced", "probed", "layered"}
GENERIC_LABELS = {
    "问题",
    "困境",
    "矛盾",
    "拉扯",
    "一方面",
    "另一方面",
    "这件事",
    "那件事",
    "职业",
    "关系",
    "情绪",
    "声音",
    "部分",
}


class TensionTracker:
    """Validate LLM deltas and reduce them into durable tension cards."""

    def ingest(
        self,
        cards: list[TensionCard],
        user_text: str,
        round_index: int,
        llm_extracted: dict[str, Any],
    ) -> list[TensionCard]:
        next_cards = [TensionCard.from_dict(card.to_dict()) for card in cards]
        next_cards = self._fold_legacy_duplicates(next_cards)
        deltas = llm_extracted.get("deltas") if isinstance(llm_extracted, dict) else []
        if not isinstance(deltas, list):
            self._age_layered_cards(next_cards, round_index, stale_gap=2)
            return next_cards

        for delta in deltas:
            if not isinstance(delta, dict):
                continue

            raw_quote = self._grounded_text(delta.get("raw_quote") or delta.get("quote"), user_text)
            if raw_quote is None:
                continue

            card_id = self._card_id(delta, raw_quote, next_cards)
            card = self._find_card(next_cards, card_id)
            is_new_card = card is None
            if card is None:
                card = TensionCard(
                    id=card_id,
                    raw_quote=raw_quote,
                    source_round=round_index,
                    last_evidence_round=round_index,
                )
                next_cards.append(card)
            else:
                card.last_evidence_round = round_index

            self._apply_grounded_fields(card, delta, user_text, round_index, is_new_card=is_new_card)
            self._advance_status(card)

        self._age_layered_cards(next_cards, round_index, stale_gap=2)
        return next_cards

    def select_focus(
        self,
        cards: list[TensionCard],
        round_index: int,
        current_focus_id: str | None = None,
    ) -> TensionCard | None:
        active = [card for card in cards if card.status in ACTIVE_STATUSES]
        if not active:
            return None
        # Never let a structurally incomplete card (e.g. a bipolar missing a
        # pole, surfaced only because of that gap) win focus over a well-formed
        # one — the surfaced +0.15 bonus would otherwise favor it (A3 / RC-3).
        eligible = [card for card in active if self._is_well_formed(card)] or active

        def score(card: TensionCard) -> float:
            value = float(card.intensity_hint or 0.5)
            if card.status == "surfaced":
                value += 0.15
            if card.last_focus_round is None:
                value += 0.35
            else:
                gap = max(0, round_index - card.last_focus_round)
                value += min(gap, 5) * 0.05
            if current_focus_id and card.id == current_focus_id:
                value += 0.15
            return value

        ranked = sorted(eligible, key=lambda card: (score(card), -card.source_round, card.id), reverse=True)
        return ranked[0]

    def select_sampling_focus(
        self,
        cards: list[TensionCard],
        round_index: int,
        focus_trace: list[dict[str, Any]] | None = None,
        current_focus_id: str | None = None,
    ) -> TensionCard | None:
        active = [card for card in cards if card.status in ACTIVE_STATUSES]
        if not active:
            return None
        # Same well-formed guard as select_focus: a malformed bipolar must not be
        # re-sampled (even as the current focus) when a complete card exists (RC-3).
        eligible = [card for card in active if self._is_well_formed(card)] or active

        trace = focus_trace if isinstance(focus_trace, list) else []

        def useful_count(card_id: str) -> int:
            return sum(
                1
                for item in trace
                if isinstance(item, dict)
                and item.get("card_id") == card_id
                and bool(item.get("useful", True))
            )

        def total_count(card_id: str) -> int:
            return sum(
                1
                for item in trace
                if isinstance(item, dict) and item.get("card_id") == card_id
            )

        def latest_for(card_id: str) -> dict[str, Any] | None:
            matches = [
                item
                for item in trace
                if isinstance(item, dict) and item.get("card_id") == card_id
            ]
            return matches[-1] if matches else None

        if current_focus_id:
            current = self._find_card(eligible, current_focus_id)
            if current is not None:
                count = useful_count(current.id)
                total = total_count(current.id)
                latest = latest_for(current.id)
                latest_was_thin = latest is not None and latest.get("useful") is False
                if total < 3 and (count < 2 or (count < 3 and latest_was_thin)):
                    return current

        under_sampled_opening = [
            card
            for card in eligible
            if card.source_round == 1 and useful_count(card.id) < 2
        ]
        if under_sampled_opening:
            return max(
                under_sampled_opening,
                key=lambda card: (
                    -useful_count(card.id),
                    float(card.intensity_hint or 0.5),
                    -(card.last_focus_round or 0),
                    card.id,
                ),
            )

        return self.select_focus(eligible, round_index, current_focus_id=current_focus_id)

    def record_focus(
        self,
        cards: list[TensionCard],
        card_id: str | None,
        round_index: int,
    ) -> list[TensionCard]:
        next_cards = [TensionCard.from_dict(card.to_dict()) for card in cards]
        if not card_id:
            return next_cards

        for card in next_cards:
            if card.id == card_id:
                card.last_focus_round = round_index
                break
        return next_cards

    def backfill_poles(
        self,
        cards: list[TensionCard],
        pole_a: str,
        pole_b: str,
        round_index: int,
    ) -> list[TensionCard]:
        """Deterministically fill poles from the user's self-statement answer.

        Applies to the strongest card that still lacks both poles; converts it to
        bipolar so the portrait stage receives named poles instead of raw tangles.
        """
        pole_a = (pole_a or "").strip()
        pole_b = (pole_b or "").strip()
        if not pole_a or not pole_b:
            return cards

        next_cards = [TensionCard.from_dict(card.to_dict()) for card in cards]
        target = None
        for card in next_cards:
            if card.kind == "bipolar" and card.pole_a and card.pole_b:
                continue
            if target is None or (card.intensity_hint or 0.0) > (target.intensity_hint or 0.0):
                target = card
        if target is None:
            return next_cards
        target.kind = "bipolar"
        target.pole_a = pole_a
        target.pole_b = pole_b
        target.last_evidence_round = round_index
        return next_cards

    def unattended(
        self,
        cards: list[TensionCard],
        round_index: int,
        gap: int = 2,
    ) -> list[TensionCard]:
        threshold = round_index - gap
        return [
            card
            for card in cards
            if card.status in ACTIVE_STATUSES
            and (
                (card.last_focus_round is None and card.source_round <= threshold)
                or (card.last_focus_round is not None and card.last_focus_round <= threshold)
            )
        ]

    def _apply_grounded_fields(
        self,
        card: TensionCard,
        delta: dict[str, Any],
        user_text: str,
        round_index: int,
        *,
        is_new_card: bool,
    ) -> None:
        incoming_kind = str(delta.get("kind") or "").strip().lower()
        valid_kind = incoming_kind if incoming_kind in {"bipolar", "undecided", "tangled"} else None

        pole_a = self._grounded_text(delta.get("pole_a"), user_text, allow_generic=False)
        pole_b = self._grounded_text(delta.get("pole_b"), user_text, allow_generic=False)
        if pole_a and pole_b:
            if normalize_comparable_text(pole_a) != normalize_comparable_text(pole_b):
                card.pole_a = pole_a
                card.pole_b = pole_b
        elif pole_a and normalize_comparable_text(pole_a) != normalize_comparable_text(card.pole_b):
            card.pole_a = pole_a
        elif pole_b and normalize_comparable_text(pole_b) != normalize_comparable_text(card.pole_a):
            card.pole_b = pole_b

        for field_name in ("candidates", "threads"):
            incoming_values = delta.get(field_name)
            if not isinstance(incoming_values, list):
                continue
            values = getattr(card, field_name)
            existing = {normalize_comparable_text(item) for item in values}
            for item in incoming_values:
                value = self._grounded_text(item, user_text, allow_generic=False)
                key = normalize_comparable_text(value)
                if value and key not in existing:
                    values.append(value)
                    existing.add(key)

        if valid_kind:
            if is_new_card or valid_kind == card.kind or self._supports_kind(card, valid_kind):
                card.kind = valid_kind
        else:
            if card.pole_a and card.pole_b:
                card.kind = "bipolar"
            elif card.candidates:
                card.kind = "undecided"
            elif len(card.threads) >= 2:
                card.kind = "tangled"
            else:
                card.kind = "undecided"

        if "intensity_hint" in delta:
            try:
                card.intensity_hint = min(max(float(delta.get("intensity_hint")), 0.0), 1.0)
            except (TypeError, ValueError):
                pass

        incoming_layers = delta.get("layers") or []
        if not isinstance(incoming_layers, list):
            return

        existing_keys = {
            (
                normalize_comparable_text(layer.description),
                normalize_comparable_text(layer.user_language),
            )
            for layer in card.layers
        }
        for item in incoming_layers:
            if not isinstance(item, dict):
                continue
            user_language = self._grounded_text(item.get("user_language"), user_text)
            if not user_language:
                continue
            description = str(item.get("description") or user_language).strip()
            key = (normalize_comparable_text(description), normalize_comparable_text(user_language))
            if key in existing_keys:
                continue
            card.layers.append(
                CardLayer(
                    description=description,
                    user_language=user_language,
                    round_index=round_index,
                )
            )
            existing_keys.add(key)

    def _supports_kind(self, card: TensionCard, kind: str) -> bool:
        if kind == "bipolar":
            return bool(
                card.pole_a
                and card.pole_b
                and normalize_comparable_text(card.pole_a) != normalize_comparable_text(card.pole_b)
            )
        if kind == "undecided":
            return len(card.candidates) >= 2
        if kind == "tangled":
            return len(card.threads) >= 3
        return False

    def _advance_status(self, card: TensionCard) -> None:
        if card.status == "saturated":
            return
        if len(card.layers) >= 2:
            card.status = "saturated"
        elif card.layers:
            card.status = "layered"
        elif (
            card.kind == "bipolar"
            and card.pole_a
            and card.pole_b
            and normalize_comparable_text(card.pole_a) != normalize_comparable_text(card.pole_b)
        ):
            card.status = "probed"
        elif card.kind == "undecided" and len(card.candidates) >= 2:
            card.status = "probed"
        elif card.kind == "tangled" and len(card.threads) >= 3:
            card.status = "probed"
        else:
            card.status = "surfaced"

    def _age_layered_cards(
        self,
        cards: list[TensionCard],
        round_index: int,
        stale_gap: int,
    ) -> None:
        for card in cards:
            if card.status != "layered":
                continue
            last_evidence = card.last_evidence_round if card.last_evidence_round is not None else card.source_round
            if round_index - last_evidence >= stale_gap:
                card.status = "saturated"

    def _card_id(self, delta: dict[str, Any], raw_quote: str, cards: list[TensionCard]) -> str:
        # An exact (normalized) raw_quote match wins over any explicit card_id:
        # the LLM sometimes mints a fresh id for a sentence it already carded,
        # so the quote — not the drifting id — is the dedup key (A3 / RC-2).
        quote_key = normalize_comparable_text(raw_quote)
        if quote_key:
            for card in cards:
                if normalize_comparable_text(card.raw_quote) == quote_key:
                    return card.id

        explicit = str(delta.get("card_id") or delta.get("id") or "").strip()
        if explicit:
            return explicit

        base = self._slug(raw_quote)
        for card in cards:
            if card.id == base:
                return base
            if card.id.startswith(f"{base}-") and card.id[len(base) + 1 :].isdigit():
                return card.id

        return base

    def _fold_legacy_duplicates(self, cards: list[TensionCard]) -> list[TensionCard]:
        """Merge legacy suffix cards that were created from the same raw quote."""
        if len(cards) < 2:
            return cards

        by_slug: dict[str, list[TensionCard]] = {}
        first_seen: dict[str, int] = {}
        for index, card in enumerate(cards):
            slug = self._slug(card.raw_quote)
            by_slug.setdefault(slug, []).append(card)
            first_seen.setdefault(slug, index)

        merged: list[TensionCard] = []
        for slug, group in by_slug.items():
            if len(group) == 1:
                merged.append(group[0])
                continue

            group.sort(key=lambda card: (card.source_round, self._suffix_rank(card.id), card.id))
            survivor = group[0]
            for other in group[1:]:
                self._merge_card(survivor, other)
            self._advance_status(survivor)
            merged.append(survivor)

        merged.sort(key=lambda card: first_seen.get(self._slug(card.raw_quote), 999))
        return merged

    def _merge_card(self, survivor: TensionCard, other: TensionCard) -> None:
        survivor.last_evidence_round = max(
            survivor.last_evidence_round or 0,
            other.last_evidence_round or 0,
        ) or None
        if other.last_focus_round is not None:
            if survivor.last_focus_round is None or other.last_focus_round > survivor.last_focus_round:
                survivor.last_focus_round = other.last_focus_round
        survivor.intensity_hint = max(survivor.intensity_hint, other.intensity_hint)

        if not survivor.pole_a and other.pole_a:
            survivor.pole_a = other.pole_a
        if not survivor.pole_b and other.pole_b:
            survivor.pole_b = other.pole_b
        if self._supports_kind(survivor, other.kind):
            survivor.kind = other.kind

        for field_name in ("threads", "candidates"):
            values = getattr(survivor, field_name)
            existing = {normalize_comparable_text(item) for item in values}
            for item in getattr(other, field_name):
                key = normalize_comparable_text(item)
                if key and key not in existing:
                    values.append(item)
                    existing.add(key)

        layer_keys = {
            (normalize_comparable_text(layer.description), normalize_comparable_text(layer.user_language))
            for layer in survivor.layers
        }
        for layer in other.layers:
            key = (normalize_comparable_text(layer.description), normalize_comparable_text(layer.user_language))
            if key in layer_keys:
                continue
            survivor.layers.append(layer)
            layer_keys.add(key)

    def _suffix_rank(self, card_id: str) -> int:
        if re.search(r"-\d+$", card_id):
            return 1
        return 0

    def _slug(self, raw_quote: str) -> str:
        ascii_words = re.findall(r"[a-zA-Z0-9]+", raw_quote.lower())
        if ascii_words:
            return "-".join(ascii_words)[:48]
        digest = hashlib.sha1(raw_quote.encode("utf-8")).hexdigest()[:8]
        return f"card-{digest}"

    def _find_card(self, cards: list[TensionCard], card_id: str) -> TensionCard | None:
        for card in cards:
            if card.id == card_id:
                return card
        return None

    def _is_well_formed(self, card: TensionCard) -> bool:
        """Reject only a half-formed bipolar — exactly one pole present, the other
        missing (the A3 pathology). A bipolar with no poles yet is still forming and
        stays focus-eligible; non-bipolar kinds are always eligible."""
        if card.kind == "bipolar" and bool(card.pole_a) != bool(card.pole_b):
            return False
        return True

    def _grounded_text(
        self,
        value: Any,
        user_text: str,
        *,
        allow_generic: bool = True,
    ) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        comparable = normalize_comparable_text(text)
        if not comparable:
            return None
        if not allow_generic and self._is_generic_label(text):
            return None
        if self._is_grounded(text, user_text):
            return text
        return None

    def _is_grounded(self, value: str, user_text: str) -> bool:
        value_norm = normalize_comparable_text(value)
        user_norm = normalize_comparable_text(user_text)
        if not value_norm or not user_norm:
            return False
        if value_norm in user_norm:
            return True
        if len(value_norm) < 4:
            return False
        return any(value_norm[index : index + 4] in user_norm for index in range(len(value_norm) - 3))

    def _is_generic_label(self, value: str) -> bool:
        normalized = normalize_comparable_text(value)
        return normalized in GENERIC_LABELS or voice_name_is_label(value)
