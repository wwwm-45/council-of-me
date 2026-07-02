"""Build PsycheBundle from elicitation + portrait state."""

from __future__ import annotations

from typing import Any, Iterable

from app.models.elicitation import ElicitationOutcome, TensionCard, normalize_comparable_text
from app.services.psyche.bundle import (
    FocusPlan,
    PortraitImprint,
    PsycheBundle,
    R1Signal,
    TensionThread,
)


class PsycheBundleBuilder:
    """Pure-function builder for PsycheBundle.

    All inputs are immutable. Output is a frozen PsycheBundle. No LLM calls.
    """

    _STATUS_WEIGHT: dict[str, float] = {
        "saturated": 1.0,
        "layered": 0.8,
        "probed": 0.6,
        "surfaced": 0.5,
    }

    def from_outcome(
        self,
        outcome: ElicitationOutcome,
        tension_cards: Iterable[Any] | None = None,
    ) -> PsycheBundle:
        normalized_cards = self._normalize_cards(tension_cards or [])
        if normalized_cards:
            raw_threads = [self._thread_from_card(card) for card in normalized_cards]
        else:
            raw_threads = [
                self._thread_from_outcome_tension(outcome, index)
                for index in range(len(outcome.core_tensions))
            ]
        bound_threads = tuple(
            self._with_bindings(thread, outcome, position=index)
            for index, thread in enumerate(raw_threads)
        )
        focus_plan = self._build_focus_plan(bound_threads)
        return PsycheBundle(
            version=1,
            tension_threads=bound_threads,
            focus_plan=focus_plan,
            portrait_imprint=None,
        )

    def _normalize_cards(self, raw: Iterable[Any]) -> list[TensionCard]:
        normalized: list[TensionCard] = []
        for item in raw:
            if isinstance(item, TensionCard):
                normalized.append(item)
            elif isinstance(item, dict):
                normalized.append(TensionCard.from_dict(item))
        return normalized

    def _thread_from_card(self, card: TensionCard) -> TensionThread:
        kind = card.kind or "undecided"
        if kind == "bipolar" and card.pole_a and card.pole_b:
            poles: tuple[str, str] | None = (card.pole_a, card.pole_b)
        else:
            poles = None
        layer_summaries = tuple(
            text
            for text in (
                (layer.description or layer.user_language or "").strip()
                for layer in card.layers
            )
            if text
        )
        status = card.status or "surfaced"
        source_round = int(card.source_round or 0)
        signal = R1Signal(
            intensity=float(card.intensity_hint),
            source_round=source_round,
            status=status,
            kind=kind,
            surfaced_only=(status == "surfaced" and source_round == 1),
        )
        return TensionThread(
            thread_id=card.id,
            poles=poles,
            candidates=tuple(card.candidates),
            threads_text=tuple(card.threads),
            layer_summaries=layer_summaries,
            bound_emotion=None,
            bound_voice_name=None,
            r1_signal=signal,
        )

    def _thread_from_outcome_tension(
        self, outcome: ElicitationOutcome, index: int
    ) -> TensionThread:
        tension = outcome.core_tensions[index]
        # outcome.dilemma_layers is session-scoped, so every fallback thread shares the same summaries.
        layer_summaries = tuple(
            layer.description.strip()
            for layer in outcome.dilemma_layers
            if layer.description and layer.description.strip()
        )
        signal = R1Signal(
            intensity=0.5,
            source_round=0,
            status="probed",
            kind="bipolar",
            surfaced_only=False,
        )
        return TensionThread(
            thread_id=f"tension:{index}",
            poles=(tension.pole_a, tension.pole_b),
            candidates=(),
            threads_text=(),
            layer_summaries=layer_summaries,
            bound_emotion=None,
            bound_voice_name=None,
            r1_signal=signal,
        )

    def _with_bindings(
        self,
        thread: TensionThread,
        outcome: ElicitationOutcome,
        *,
        position: int,
    ) -> TensionThread:
        bound_emotion = self._bind_emotion(thread, outcome, position)
        bound_voice = self._bind_voice(thread, outcome, position)
        return TensionThread(
            thread_id=thread.thread_id,
            poles=thread.poles,
            candidates=thread.candidates,
            threads_text=thread.threads_text,
            layer_summaries=thread.layer_summaries,
            bound_emotion=bound_emotion,
            bound_voice_name=bound_voice,
            r1_signal=thread.r1_signal,
        )

    def _bind_emotion(
        self,
        thread: TensionThread,
        outcome: ElicitationOutcome,
        position: int,
    ) -> dict[str, Any] | None:
        if not outcome.emotion_map:
            return None
        thread_keys = self._thread_text_keys(thread)
        for emotion in outcome.emotion_map:
            haystack = normalize_comparable_text(emotion.context or "") + normalize_comparable_text(emotion.emotion or "")
            if any(key and key in haystack for key in thread_keys):
                return {"emotion": emotion.emotion, "context": emotion.context, "intensity": float(emotion.intensity)}
        if position < len(outcome.emotion_map):
            fallback = outcome.emotion_map[position]
            return {"emotion": fallback.emotion, "context": fallback.context, "intensity": float(fallback.intensity)}
        return None

    def _bind_voice(
        self,
        thread: TensionThread,
        outcome: ElicitationOutcome,
        position: int,
    ) -> str | None:
        if not outcome.inner_voices:
            return None
        thread_keys = self._thread_text_keys(thread)
        best_match: tuple[str, float] | None = None
        for voice in outcome.inner_voices:
            haystack = normalize_comparable_text(voice.core_concern or "") + normalize_comparable_text(voice.protective_intent or "")
            if any(key and key in haystack for key in thread_keys):
                intensity = float(voice.intensity or 0.0)
                if best_match is None or intensity > best_match[1]:
                    best_match = (voice.name, intensity)
        if best_match:
            return best_match[0]
        if position < len(outcome.inner_voices):
            return outcome.inner_voices[position].name
        return None

    def build_legacy_bundle(self, profile: dict[str, Any]) -> PsycheBundle:
        tension_pairs = profile.get("core_tension_pairs") or []
        layer_dicts = profile.get("dilemma_layers") or []
        layer_summaries = tuple(
            str(layer.get("description") or "").strip()
            for layer in layer_dicts
            if isinstance(layer, dict) and str(layer.get("description") or "").strip()
        )
        emotion_dicts = profile.get("emotion_map") or []
        voice_dicts = profile.get("inner_voices") or []

        threads: list[TensionThread] = []
        for index, pair in enumerate(tension_pairs):
            if not isinstance(pair, dict):
                continue
            pole_a = str(pair.get("pole_a") or "").strip()
            pole_b = str(pair.get("pole_b") or "").strip()
            poles = (pole_a, pole_b) if pole_a and pole_b else None
            bound_emotion = (
                {
                    "emotion": str(emotion_dicts[index].get("emotion") or ""),
                    "context": str(emotion_dicts[index].get("context") or ""),
                    "intensity": float(emotion_dicts[index].get("intensity") or 0.0),
                }
                if index < len(emotion_dicts) and isinstance(emotion_dicts[index], dict)
                else None
            )
            bound_voice = (
                str(voice_dicts[index].get("name") or "")
                if index < len(voice_dicts)
                and isinstance(voice_dicts[index], dict)
                and voice_dicts[index].get("name")
                else None
            )
            threads.append(
                TensionThread(
                    thread_id=f"tension:{index}",
                    poles=poles,
                    candidates=(),
                    threads_text=(),
                    layer_summaries=layer_summaries,
                    bound_emotion=bound_emotion,
                    bound_voice_name=bound_voice,
                    r1_signal=R1Signal(
                        intensity=0.5,
                        source_round=0,
                        status="probed",
                        kind="bipolar",
                        surfaced_only=False,
                    ),
                )
            )

        bundled = tuple(threads)
        focus_plan = (
            FocusPlan(
                primary_thread_id=bundled[0].thread_id,
                next_focus=tuple(thread.thread_id for thread in bundled[1:]),
                selection_reason={
                    "ranked_by": "legacy_index",
                    "scores": {thread.thread_id: 0.0 for thread in bundled},
                },
            )
            if bundled
            else None
        )
        return PsycheBundle(
            version=1,
            tension_threads=bundled,
            focus_plan=focus_plan,
            portrait_imprint=None,
        )

    def merge_portrait(self, bundle: PsycheBundle, portrait_data: Any) -> PsycheBundle:
        if not isinstance(portrait_data, dict):
            return bundle
        assignments_raw = portrait_data.get("agent_assignments")
        if not isinstance(assignments_raw, list):
            return bundle
        assignments = tuple(
            {
                "agent_id": str(item.get("agent_id") or ""),
                "voice_name": str(item.get("voice_name") or ""),
                "specific_concern": str(item.get("specific_concern") or ""),
            }
            for item in assignments_raw
            if isinstance(item, dict)
        )
        kept_voices = tuple(
            str(name)
            for name in (portrait_data.get("kept_voice_names") or [])
            if str(name).strip()
        )
        renamed = {
            str(key): str(value)
            for key, value in (portrait_data.get("renamed_voices") or {}).items()
            if str(key).strip() and str(value).strip()
        }
        imprint = PortraitImprint(
            user_kept_voices=kept_voices,
            user_renamed=renamed,
            assignments=assignments,
        )

        new_threads = []
        for index, thread in enumerate(bundle.tension_threads):
            override = (
                assignments[index]["voice_name"]
                if index < len(assignments) and assignments[index].get("voice_name")
                else thread.bound_voice_name
            )
            new_threads.append(
                TensionThread(
                    thread_id=thread.thread_id,
                    poles=thread.poles,
                    candidates=thread.candidates,
                    threads_text=thread.threads_text,
                    layer_summaries=thread.layer_summaries,
                    bound_emotion=thread.bound_emotion,
                    bound_voice_name=override,
                    r1_signal=thread.r1_signal,
                )
            )
        return PsycheBundle(
            version=bundle.version,
            tension_threads=tuple(new_threads),
            focus_plan=bundle.focus_plan,
            portrait_imprint=imprint,
        )

    def _thread_text_keys(self, thread: TensionThread) -> list[str]:
        candidates = []
        if thread.poles:
            candidates.extend(thread.poles)
        candidates.extend(thread.candidates)
        candidates.extend(thread.threads_text)
        return [normalize_comparable_text(text) for text in candidates if text]

    def _build_focus_plan(self, threads: tuple[TensionThread, ...]) -> FocusPlan | None:
        if not threads:
            return None
        scored: list[tuple[str, float]] = []
        for thread in threads:
            score = self._score_thread(thread)
            scored.append((thread.thread_id, round(score, 4)))
        scored.sort(key=lambda item: item[1], reverse=True)
        primary_id, _ = scored[0]
        next_ids = tuple(thread_id for thread_id, _ in scored[1:])
        return FocusPlan(
            primary_thread_id=primary_id,
            next_focus=next_ids,
            selection_reason={
                "ranked_by": "psyche_score",
                "scores": {thread_id: score for thread_id, score in scored},
            },
        )

    def _score_thread(self, thread: TensionThread) -> float:
        signal = thread.r1_signal
        intensity = max(0.0, min(1.0, float(signal.intensity)))
        status_weight = self._STATUS_WEIGHT.get(signal.status, 0.5)
        r1_bonus = 1.0 if signal.source_round == 1 else 0.0
        surfaced_bonus = 1.0 if signal.surfaced_only else 0.0
        return (
            0.35 * intensity
            + 0.25 * status_weight
            + 0.20 * r1_bonus
            + 0.20 * surfaced_bonus
        )


def bundle_or_legacy(profile: dict[str, Any]) -> PsycheBundle:
    raw = profile.get("psyche_bundle") if isinstance(profile, dict) else None
    if isinstance(raw, dict):
        restored = PsycheBundle.from_dict(raw)
        if restored is not None:
            return restored
    return PsycheBundleBuilder().build_legacy_bundle(profile or {})
