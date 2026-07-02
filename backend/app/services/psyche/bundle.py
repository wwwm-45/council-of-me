"""Frozen dataclasses for the PsycheBundle handoff layer.

All fields are tuple-based or primitive to keep PsycheBundle a value object
that downstream layers can hold without worrying about mutation. Dict-typed
fields (bound_emotion, selection_reason) are kept as plain dict for
ergonomic LLM-context serialization but treated as immutable by convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class R1Signal:
    intensity: float
    source_round: int
    status: str
    kind: str
    surfaced_only: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "intensity": float(self.intensity),
            "source_round": int(self.source_round),
            "status": str(self.status),
            "kind": str(self.kind),
            "surfaced_only": bool(self.surfaced_only),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "R1Signal":
        return cls(
            intensity=float(payload.get("intensity", 0.0)),
            source_round=int(payload.get("source_round", 0)),
            status=str(payload.get("status", "")),
            kind=str(payload.get("kind", "")),
            surfaced_only=bool(payload.get("surfaced_only", False)),
        )


@dataclass(frozen=True)
class TensionThread:
    thread_id: str
    poles: tuple[str, str] | None
    candidates: tuple[str, ...]
    threads_text: tuple[str, ...]
    layer_summaries: tuple[str, ...]
    bound_emotion: dict[str, Any] | None
    bound_voice_name: str | None
    r1_signal: R1Signal

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "poles": list(self.poles) if self.poles else None,
            "candidates": list(self.candidates),
            "threads_text": list(self.threads_text),
            "layer_summaries": list(self.layer_summaries),
            "bound_emotion": dict(self.bound_emotion) if self.bound_emotion else None,
            "bound_voice_name": self.bound_voice_name,
            "r1_signal": self.r1_signal.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TensionThread":
        poles_value = payload.get("poles")
        poles: tuple[str, str] | None
        if isinstance(poles_value, (list, tuple)) and len(poles_value) == 2:
            poles = (str(poles_value[0]), str(poles_value[1]))
        else:
            poles = None
        return cls(
            thread_id=str(payload.get("thread_id") or ""),
            poles=poles,
            candidates=tuple(str(item) for item in payload.get("candidates") or []),
            threads_text=tuple(str(item) for item in payload.get("threads_text") or []),
            layer_summaries=tuple(str(item) for item in payload.get("layer_summaries") or []),
            bound_emotion=dict(payload["bound_emotion"]) if isinstance(payload.get("bound_emotion"), dict) else None,
            bound_voice_name=str(payload["bound_voice_name"]) if payload.get("bound_voice_name") else None,
            r1_signal=R1Signal.from_dict(payload.get("r1_signal") or {}),
        )


@dataclass(frozen=True)
class FocusPlan:
    primary_thread_id: str
    next_focus: tuple[str, ...]
    selection_reason: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_thread_id": self.primary_thread_id,
            "next_focus": list(self.next_focus),
            "selection_reason": dict(self.selection_reason),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FocusPlan":
        return cls(
            primary_thread_id=str(payload.get("primary_thread_id") or ""),
            next_focus=tuple(str(item) for item in payload.get("next_focus") or []),
            selection_reason=dict(payload.get("selection_reason") or {}),
        )


@dataclass(frozen=True)
class PortraitImprint:
    user_kept_voices: tuple[str, ...]
    user_renamed: dict[str, str]
    assignments: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_kept_voices": list(self.user_kept_voices),
            "user_renamed": dict(self.user_renamed),
            "assignments": [dict(item) for item in self.assignments],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PortraitImprint":
        return cls(
            user_kept_voices=tuple(str(item) for item in payload.get("user_kept_voices") or []),
            user_renamed={str(k): str(v) for k, v in (payload.get("user_renamed") or {}).items()},
            assignments=tuple(dict(item) for item in payload.get("assignments") or [] if isinstance(item, dict)),
        )


@dataclass(frozen=True)
class PsycheBundle:
    version: int = 1
    tension_threads: tuple[TensionThread, ...] = field(default_factory=tuple)
    focus_plan: FocusPlan | None = None
    portrait_imprint: PortraitImprint | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "tension_threads": [thread.to_dict() for thread in self.tension_threads],
            "focus_plan": self.focus_plan.to_dict() if self.focus_plan else None,
            "portrait_imprint": self.portrait_imprint.to_dict() if self.portrait_imprint else None,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "PsycheBundle | None":
        if not isinstance(payload, dict):
            return None
        try:
            version = int(payload.get("version") or 1)
        except (TypeError, ValueError):
            return None
        try:
            threads = tuple(
                TensionThread.from_dict(item)
                for item in payload.get("tension_threads") or []
                if isinstance(item, dict)
            )
            focus = (
                FocusPlan.from_dict(payload["focus_plan"])
                if isinstance(payload.get("focus_plan"), dict)
                else None
            )
            imprint = (
                PortraitImprint.from_dict(payload["portrait_imprint"])
                if isinstance(payload.get("portrait_imprint"), dict)
                else None
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            return None
        return cls(version=version, tension_threads=threads, focus_plan=focus, portrait_imprint=imprint)
