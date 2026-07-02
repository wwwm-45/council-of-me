"""Semantic voice-to-agent mapping for portrait composition."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from app.models.elicitation import InnerVoice, Tension
from app.services.framing import apply_framing
from app.services.identity_card import ROLES, get_roles_for_level
from app.services.language_guard import has_sufficient_chinese
from app.services.llm import generate, is_llm_error

logger = logging.getLogger(__name__)


@dataclass
class AgentAssignment:
    voice_name: str
    agent_role: str
    display_name: str
    mapping_reason: str
    system_prompt_addon: str

    def to_dict(self) -> dict:
        return {
            "voice_name": self.voice_name,
            "agent_role": self.agent_role,
            "display_name": self.display_name,
            "mapping_reason": self.mapping_reason,
            "system_prompt_addon": self.system_prompt_addon,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "AgentAssignment":
        return cls(
            voice_name=str(payload.get("voice_name") or ""),
            agent_role=str(payload.get("agent_role") or ""),
            display_name=str(payload.get("display_name") or ""),
            mapping_reason=str(payload.get("mapping_reason") or ""),
            system_prompt_addon=str(payload.get("system_prompt_addon") or ""),
        )


def _strip_json_block(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
    return text


def _safe_chinese_text(value: str, fallback: str) -> str:
    text = str(value or "").strip()
    if text and has_sufficient_chinese(text):
        return text
    return fallback


def _json_candidates(raw: str) -> list[str]:
    candidates: list[str] = []

    def _add(value: str) -> None:
        text = value.strip()
        if text and text not in candidates:
            candidates.append(text)

    _add(raw)
    stripped = _strip_json_block(raw)
    _add(stripped)

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        _add(stripped[start : end + 1])

    return candidates


def _repair_json_text(text: str) -> str:
    repaired = text.replace("\ufeff", "").strip()
    repaired = repaired.replace("“", '"').replace("”", '"')
    repaired = repaired.replace("‘", "'").replace("’", "'")
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    repaired = re.sub(r"}(\s*)(?=\{)", r"},\1", repaired)
    repaired = re.sub(r"](\s*)(?=\[)", r"],\1", repaired)
    return repaired


def _find_matching_bracket(text: str, start: int, open_char: str, close_char: str) -> int | None:
    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char == open_char:
            depth += 1
            continue
        if char == close_char:
            depth -= 1
            if depth == 0:
                return index

    return None


def _extract_assignments_array(text: str) -> str | None:
    match = re.search(r'"assignments"\s*:\s*\[', text)
    if not match:
        return None

    array_start = match.end() - 1
    array_end = _find_matching_bracket(text, array_start, "[", "]")
    if array_end is None:
        return None

    return text[array_start + 1 : array_end]


def _split_top_level_objects(text: str) -> list[str]:
    objects: list[str] = []
    start: int | None = None
    depth = 0
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
            continue
        if char == "}":
            if depth == 0:
                continue
            depth -= 1
            if depth == 0 and start is not None:
                objects.append(text[start : index + 1])
                start = None

    return objects


def _parse_json_payload(raw: str) -> dict:
    for candidate in _json_candidates(raw):
        for variant in (candidate, _repair_json_text(candidate)):
            try:
                parsed = json.loads(variant)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(parsed, dict):
                return parsed

    for candidate in _json_candidates(raw):
        assignments_blob = _extract_assignments_array(_repair_json_text(candidate))
        if not assignments_blob:
            continue

        assignments: list[dict] = []
        for chunk in _split_top_level_objects(assignments_blob):
            for variant in (chunk, _repair_json_text(chunk)):
                try:
                    parsed = json.loads(variant)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(parsed, dict):
                    assignments.append(parsed)
                    break

        if assignments:
            return {"assignments": assignments}

    raise ValueError("Unable to parse AgentMapper JSON payload")


class AgentMapper:
    def _relationship_summary(self, voice: InnerVoice) -> str:
        snippets: list[str] = []
        for item in voice.relationship_to_others:
            target = str(item.get("target") or "").strip()
            dynamic = str(item.get("dynamic") or "").strip()
            description = str(item.get("description") or "").strip()
            if not target and not dynamic and not description:
                continue
            summary = f"{target or 'other voice'}: {dynamic or 'related'}"
            if description:
                summary += f" ({description})"
            snippets.append(summary)
        return " | ".join(snippets)

    def _voice_portrait_line(self, voice: InnerVoice) -> str:
        segments = [
            f"- {voice.name}: concern={voice.core_concern}; intent={voice.protective_intent}; intensity={voice.intensity:.2f}"
        ]
        if voice.fear:
            segments.append(f"fear={voice.fear}")
        if voice.language_style:
            segments.append(f"language_style={voice.language_style}")
        if voice.typical_phrases:
            phrases = " | ".join(item for item in voice.typical_phrases if item)
            if phrases:
                segments.append(f"typical_phrases={phrases}")
        relationships = self._relationship_summary(voice)
        if relationships:
            segments.append(f"relationships={relationships}")
        return "; ".join(segments)

    def _fallback_addon(self, voice: InnerVoice | None) -> str:
        if not voice:
            return "请特别留意这次对话中的关注点：整体困境。"

        parts = [f"请特别留意这次对话中的关注点：{voice.core_concern or voice.name}。"]
        if voice.fear:
            parts.append(f"它最担心的是：{voice.fear}。")
        if voice.language_style:
            parts.append(f"它常用的表达风格是：{voice.language_style}。")
        if voice.typical_phrases:
            parts.append(f"它常出现的话语包括：{'；'.join(voice.typical_phrases)}。")
        return "".join(parts)

    async def map_voices(
        self,
        voices: list[InnerVoice],
        tensions: list[Tension],
        debate_level: str,
        *,
        framing_preference: str | None = None,
    ) -> list[AgentAssignment]:
        try:
            parsed = await self._map_with_llm(voices, tensions, debate_level)
            return self._normalize_assignments(parsed, voices, debate_level, framing_preference)
        except Exception:
            logger.warning("AgentMapper failed; using fallback mapping.", exc_info=True)
            return self._fallback(voices, debate_level, framing_preference)

    async def _map_with_llm(
        self,
        voices: list[InnerVoice],
        tensions: list[Tension],
        debate_level: str,
    ) -> list[AgentAssignment]:
        voices_blob = "\n".join(
            self._voice_portrait_line(item)
            for item in voices
        ) or "- None"
        tensions_blob = "\n".join(f"- {item.pole_a} <-> {item.pole_b}: {item.user_evidence}" for item in tensions) or "- None"
        prompt = (
            "请把下面这些内在声音映射到最适合的顾问角色上，并输出 JSON。\n"
            f"debate_level: {debate_level}\n"
            f"voices:\n{voices_blob}\n"
            f"tensions:\n{tensions_blob}\n"
            f"available_roles: {ROLES}\n"
            '返回格式: {"assignments":[{"voice_name":"","agent_role":"","display_name":"","mapping_reason":"","system_prompt_addon":""}]}'
        )
        raw = await generate(
            prompt,
            system="你是一个内在声音与议会角色的映射专家。只返回 JSON。",
            temperature=0.2,
            max_tokens=700,
        )
        if not raw or is_llm_error(raw):
            raise ValueError("LLM error sentinel returned")
        payload = _parse_json_payload(raw)
        assignments = payload.get("assignments") or []
        return [
            AgentAssignment.from_dict(item)
            for item in assignments
            if isinstance(item, dict) and str(item.get("agent_role") or "").strip()
        ]

    def _normalize_assignments(
        self,
        assignments: list[AgentAssignment],
        voices: list[InnerVoice],
        debate_level: str,
        framing_preference: str | None,
    ) -> list[AgentAssignment]:
        target_count = {"L1": 2, "L2": 4, "L3": 5}.get(debate_level, 4)
        normalized: list[AgentAssignment] = []
        used_roles: set[str] = set()

        for item in assignments:
            role = item.agent_role if item.agent_role in ROLES else ""
            if not role or role in used_roles:
                continue
            matched_voice = next((voice for voice in voices if voice.name == item.voice_name), None)
            fallback_reason = f"{item.voice_name or '这个声音'}适合由 {role} 的视角来承接。"
            fallback_addon = self._fallback_addon(matched_voice)
            mapping_reason = _safe_chinese_text(item.mapping_reason, fallback_reason)
            system_prompt_addon = _safe_chinese_text(item.system_prompt_addon, fallback_addon)
            normalized.append(
                AgentAssignment(
                    voice_name=item.voice_name,
                    agent_role=role,
                    display_name=apply_framing(framing_preference or "neutral", role),
                    mapping_reason=mapping_reason,
                    system_prompt_addon=system_prompt_addon,
                )
            )
            used_roles.add(role)
            if len(normalized) >= target_count:
                return normalized

        fallback = self._fallback(voices, debate_level, framing_preference)
        for item in fallback:
            if item.agent_role in used_roles:
                continue
            normalized.append(item)
            used_roles.add(item.agent_role)
            if len(normalized) >= target_count:
                break

        return normalized[:target_count]

    def _fallback(
        self,
        voices: list[InnerVoice],
        debate_level: str,
        framing_preference: str | None,
    ) -> list[AgentAssignment]:
        roles = get_roles_for_level(debate_level)
        assignments: list[AgentAssignment] = []
        for index, role in enumerate(roles):
            voice = voices[index] if index < len(voices) else None
            voice_name = voice.name if voice else ""
            assignments.append(
                AgentAssignment(
                    voice_name=voice_name,
                    agent_role=role,
                    display_name=apply_framing(framing_preference or "neutral", role),
                    mapping_reason=f"{voice_name or '剩余张力'}更适合由 {role} 的视角来承接。",
                    system_prompt_addon=self._fallback_addon(voice),
                )
            )
        return assignments
