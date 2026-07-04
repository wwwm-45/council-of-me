from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.psyche.builder import bundle_or_legacy


@dataclass(frozen=True)
class VoiceEntryPoint:
    agent_id: str
    what_i_protect: str
    what_i_fear: str
    what_i_refuse_to_pay: str


@dataclass(frozen=True)
class ActiveQuestion:
    question_id: str
    prompt_text: str
    tension_ids: list[str]


@dataclass(frozen=True)
class ProgressMarker:
    question_id: str
    status: str = "introduced"


@dataclass(frozen=True)
class DebateSpine:
    core_contradiction: str
    voice_entry_points: dict[str, VoiceEntryPoint]
    active_questions: list[ActiveQuestion]
    cost_ledger: list[str]
    progress_markers: dict[str, ProgressMarker] = field(default_factory=dict)

    def to_prompt_text(self) -> str:
        lines = [
            "## debate spine",
            f"core_contradiction: {self.core_contradiction}",
        ]

        if self.active_questions:
            lines.append("\u6d3b\u8dc3\u95ee\u9898:")
            for question in self.active_questions:
                lines.append(
                    f"- {question.question_id}: {question.prompt_text}"
                )

        if self.cost_ledger:
            lines.append(
                "\u4ee3\u4ef7\u8d26\u672c: " + "\u3001".join(self.cost_ledger)
            )

        if self.voice_entry_points:
            lines.append("\u89d2\u8272\u5165\u53e3:")
            for agent_id, entry in self.voice_entry_points.items():
                lines.append(
                    f"- {agent_id}: "
                    f"protect={entry.what_i_protect}; "
                    f"fear={entry.what_i_fear}; "
                    f"refuse={entry.what_i_refuse_to_pay}"
                )

        return "\n".join(lines)


class DebateSpineBuilder:
    _DEPTH_ORDER = {"surface": 0, "emotional": 1, "existential": 2}

    def build(self, *, profile: dict[str, Any], identity_cards: list[dict[str, Any]]) -> DebateSpine:
        voices = self._sorted_voices(profile.get("inner_voices") or [])
        layers = self._sorted_layers(profile.get("dilemma_layers") or [])
        raw_tensions = profile.get("core_tension_pairs") or []
        tensions = self._reorder_tensions_by_bundle(raw_tensions, profile)
        emotions = self._sorted_emotions(profile.get("emotion_map") or [])

        contradiction = self._build_core_contradiction(profile, tensions, layers)
        cost_ledger = self._build_cost_ledger(tensions, layers, emotions)
        voice_entry_points = self._build_voice_entry_points(
            identity_cards=identity_cards,
            voices=voices,
            layers=layers,
            tensions=tensions,
            emotions=emotions,
            cost_ledger=cost_ledger,
        )
        active_questions = self._build_active_questions(
            contradiction=contradiction,
            tensions=tensions,
            layers=layers,
            emotions=emotions,
            cost_ledger=cost_ledger,
        )
        progress_markers = {
            question.question_id: ProgressMarker(question_id=question.question_id)
            for question in active_questions
        }

        return DebateSpine(
            core_contradiction=contradiction,
            voice_entry_points=voice_entry_points,
            active_questions=active_questions,
            cost_ledger=cost_ledger,
            progress_markers=progress_markers,
        )

    def _build_core_contradiction(
        self,
        profile: dict[str, Any],
        tensions: list[dict[str, Any]],
        layers: list[dict[str, Any]],
    ) -> str:
        core_dilemma = self._pick_text(profile.get("core_dilemma"))
        if core_dilemma and any(token in core_dilemma.lower() for token in (" but ", " yet ", " while ", " though ")):
            return core_dilemma

        first_tension = tensions[0] if tensions else {}
        first_layer = layers[0] if layers else {}
        pole_a = self._pick_text(first_tension.get("pole_a"), "现在这点稳妥")
        pole_b = self._pick_text(first_tension.get("pole_b"), "更想靠近的活法")
        layer_text = self._pick_text(first_layer.get("user_language"), first_layer.get("description"))
        if layer_text:
            return (
                f"用户一边想靠近{pole_b}，一边又怕失去{pole_a}的代价，"
                f"而{layer_text}让他始终没法干脆做决定。"
            )
        if core_dilemma:
            return core_dilemma
        return f"用户既想靠近{pole_b}，又害怕失去{pole_a}，于是一直卡在中间。"

    def _build_voice_entry_points(
        self,
        *,
        identity_cards: list[dict[str, Any]],
        voices: list[dict[str, Any]],
        layers: list[dict[str, Any]],
        tensions: list[dict[str, Any]],
        emotions: list[dict[str, Any]],
        cost_ledger: list[str],
    ) -> dict[str, VoiceEntryPoint]:
        primary_voice = voices[0] if voices else {}
        secondary_voice = voices[1] if len(voices) > 1 else primary_voice
        tertiary_voice = voices[2] if len(voices) > 2 else secondary_voice
        deepest_layer = layers[-1] if layers else {}
        first_tension = tensions[0] if tensions else {}
        second_tension = tensions[1] if len(tensions) > 1 else first_tension
        strongest_emotion = emotions[0] if emotions else {}
        second_emotion = emotions[1] if len(emotions) > 1 else strongest_emotion
        strongest_cost = cost_ledger[0] if cost_ledger else "把这一步走错之后要自己吞下的代价"
        second_cost = cost_ledger[1] if len(cost_ledger) > 1 else strongest_cost

        entry_points: dict[str, VoiceEntryPoint] = {}
        for card in identity_cards:
            agent_id = self._pick_text(card.get("agent_id"))
            if not agent_id:
                continue

            if agent_id == "empathic_listener":
                entry_points[agent_id] = VoiceEntryPoint(
                    agent_id=agent_id,
                    what_i_protect=self._pick_text(
                        primary_voice.get("protective_intent"),
                        strongest_emotion.get("context"),
                        deepest_layer.get("user_language"),
                        "用户心里那个还需要安全感的部分",
                    ),
                    what_i_fear=self._pick_text(
                        primary_voice.get("core_concern"),
                        strongest_emotion.get("context"),
                        "走得太快之后，现实后果会砸到人身上",
                    ),
                    what_i_refuse_to_pay=strongest_cost,
                )
            elif agent_id == "rational_analyst":
                entry_points[agent_id] = VoiceEntryPoint(
                    agent_id=agent_id,
                    what_i_protect=self._pick_text(
                        secondary_voice.get("protective_intent"),
                        first_tension.get("pole_a"),
                        "一条还能回撤、也经得住现实检验的路",
                    ),
                    what_i_fear=self._pick_text(
                        secondary_voice.get("core_concern"),
                        first_tension.get("user_evidence"),
                        "把用户锁进一个根本付不起的代价里",
                    ),
                    what_i_refuse_to_pay=second_cost,
                )
            elif agent_id == "critical_examiner":
                entry_points[agent_id] = VoiceEntryPoint(
                    agent_id=agent_id,
                    what_i_protect=self._pick_text(
                        tertiary_voice.get("protective_intent"),
                        second_tension.get("pole_a"),
                        "别再用自我欺骗替自己壮胆",
                    ),
                    what_i_fear=self._pick_text(
                        tertiary_voice.get("core_concern"),
                        deepest_layer.get("description"),
                        "把渴望误认成准备好了",
                    ),
                    what_i_refuse_to_pay=self._pick_text(second_cost, strongest_cost),
                )
            else:
                entry_points[agent_id] = VoiceEntryPoint(
                    agent_id=agent_id,
                    what_i_protect=self._pick_text(
                        deepest_layer.get("description"),
                        primary_voice.get("protective_intent"),
                        "别只抓住冲突的一边，要把整块矛盾都看见",
                    ),
                    what_i_fear=self._pick_text(
                        second_emotion.get("context"),
                        deepest_layer.get("user_language"),
                        "整场争论最后塌成一个假二选一",
                    ),
                    what_i_refuse_to_pay=self._pick_text(strongest_cost, second_cost),
                )

        return entry_points

    def _build_active_questions(
        self,
        *,
        contradiction: str,
        tensions: list[dict[str, Any]],
        layers: list[dict[str, Any]],
        emotions: list[dict[str, Any]],
        cost_ledger: list[str],
    ) -> list[ActiveQuestion]:
        questions: list[ActiveQuestion] = []
        contradiction_subject = contradiction.rstrip(" .!?")

        if tensions:
            first_tension = tensions[0]
            pole_a = self._pick_text(first_tension.get("pole_a"), "稳定")
            pole_b = self._pick_text(first_tension.get("pole_b"), "活法")
            questions.append(
                ActiveQuestion(
                    question_id="q1",
                    prompt_text=f"现在更扛不住的代价到底是哪一个：失去{pole_a}，还是继续放弃{pole_b}？",
                    tension_ids=["tension:0"],
                )
            )

        if layers:
            deepest_layer = layers[-1]
            layer_text = self._pick_text(deepest_layer.get("user_language"), deepest_layer.get("description"))
            questions.append(
                ActiveQuestion(
                    question_id=f"q{len(questions) + 1}",
                    prompt_text=f"这一步选择像是在说用户是个什么样的人：{layer_text}？",
                    tension_ids=[f"layer:{len(layers) - 1}"],
                )
            )

        if emotions:
            strongest_emotion = emotions[0]
            emotion = self._pick_text(strongest_emotion.get("emotion"), "害怕")
            context = self._pick_text(strongest_emotion.get("context"), contradiction)
            questions.append(
                ActiveQuestion(
                    question_id=f"q{len(questions) + 1}",
                    prompt_text=f"当{emotion}一上来，它其实在拼命护住什么边界、责任或不敢碰的后果：{context}？",
                    tension_ids=["emotion:0"],
                )
            )

        fallback_questions = [
            ActiveQuestion(
                question_id=f"q{len(questions) + 1}",
                prompt_text=(
                    f"到底是什么让这个矛盾一直拖着不落地：{contradiction_subject}？"
                ),
                tension_ids=["core"],
            ),
            ActiveQuestion(
                question_id=f"q{len(questions) + 2}",
                prompt_text=(
                    f"如果你不再围着“{contradiction_subject}”打转，"
                    "哪种失去、责任或恐惧会立刻变得最难回避？"
                ),
                tension_ids=["core"],
            ),
        ]

        if len(questions) < 2:
            questions.extend(fallback_questions)

        if len(questions) < 2:
            questions.append(
                ActiveQuestion(
                    question_id=f"q{len(questions) + 1}",
                    prompt_text=(
                        f"如果你把“{contradiction_subject}”当成真实取舍，"
                        "而不是继续拖延的问题，最先需要面对的变化会是什么？"
                    ),
                    tension_ids=["core"],
                )
            )

        deduped_questions: list[ActiveQuestion] = []
        seen_prompts: set[str] = set()
        for question in questions:
            prompt = question.prompt_text.strip()
            if not prompt or prompt in seen_prompts:
                continue
            seen_prompts.add(prompt)
            deduped_questions.append(question)

        return deduped_questions[:4]

    def _build_cost_ledger(
        self,
        tensions: list[dict[str, Any]],
        layers: list[dict[str, Any]],
        emotions: list[dict[str, Any]],
    ) -> list[str]:
        candidates: list[str] = []

        for tension in tensions:
            pole_a = self._pick_text(tension.get("pole_a"))
            pole_b = self._pick_text(tension.get("pole_b"))
            if pole_a:
                candidates.append(pole_a)
            if pole_b:
                candidates.append(pole_b)

        for emotion in emotions:
            candidates.append(self._pick_text(emotion.get("emotion")))
            candidates.append(self._pick_text(emotion.get("context")))

        for layer in layers:
            candidates.append(self._pick_text(layer.get("description")))

        deduped: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            text = candidate.strip()
            if not text:
                continue
            normalized = text.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(text)

        return deduped[:6]

    def _sorted_voices(self, voices: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(voices, key=lambda item: float(item.get("intensity") or 0.0), reverse=True)

    def _sorted_layers(self, layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            layers,
            key=lambda item: self._DEPTH_ORDER.get(self._pick_text(item.get("depth")).lower(), 0),
        )

    def _sorted_emotions(self, emotions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(emotions, key=lambda item: float(item.get("intensity") or 0.0), reverse=True)

    def _reorder_tensions_by_bundle(
        self,
        tensions: list[dict[str, Any]],
        profile: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not tensions:
            return tensions
        bundle = bundle_or_legacy(profile)
        focus = bundle.focus_plan
        if focus is None or not bundle.tension_threads:
            return tensions

        thread_order = [focus.primary_thread_id, *focus.next_focus]
        index_by_id: dict[str, int] = {}
        for index, thread in enumerate(bundle.tension_threads):
            if thread.thread_id.startswith("tension:"):
                try:
                    index_by_id[thread.thread_id] = int(thread.thread_id.split(":", 1)[1])
                except (IndexError, ValueError):
                    continue
            else:
                index_by_id[thread.thread_id] = index

        ordered: list[dict[str, Any]] = []
        used_indices: set[int] = set()
        for thread_id in thread_order:
            mapped = index_by_id.get(thread_id)
            if mapped is None or mapped >= len(tensions) or mapped in used_indices:
                continue
            ordered.append(tensions[mapped])
            used_indices.add(mapped)
        for index, tension in enumerate(tensions):
            if index not in used_indices:
                ordered.append(tension)
        return ordered

    def _pick_text(self, *values: Any) -> str:
        for value in values:
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""
