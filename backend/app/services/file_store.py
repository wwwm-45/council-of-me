"""
SessionFileStore: Incremental disk persistence for session data.

Writes JSON files to disk after each interaction so data survives server restarts.
Each session gets a folder: {SESSION_EXPORT_DIR}/{session_id}/
Files are updated in place (atomic write via temp file + rename).

On new server start, sessions start fresh (no loading from disk).
The files on disk are purely for data preservation / post-hoc analysis.
"""
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from app import config
from app.services.language_guard import drain_counter_patch

logger = logging.getLogger(__name__)


def _session_dir(session_id: UUID | str) -> Path:
    return Path(config.SESSION_EXPORT_DIR).expanduser() / str(session_id)


def _write_json_atomic(filepath: Path, data: Any) -> None:
    """Write JSON atomically: write to temp file then rename, to avoid corruption."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(filepath.parent), suffix=".tmp")
    try:
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        except Exception:
            os.unlink(tmp_path)
            raise
        os.replace(tmp_path, str(filepath))
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _write_text_atomic(filepath: Path, text: str) -> None:
    """Write text atomically: temp file then rename, to avoid corruption."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(filepath.parent), suffix=".tmp")
    try:
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            os.unlink(tmp_path)
            raise
        os.replace(tmp_path, str(filepath))
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _merge_language_guard_patch(
    meta: dict[str, Any],
    patch: dict[str, dict[str, int]],
) -> dict[str, Any]:
    if not patch:
        return meta

    guard_patch = patch.get("language_guard")
    if not isinstance(guard_patch, dict) or not guard_patch:
        return meta

    merged = dict(meta)
    existing = merged.get("language_guard")
    existing_counts = existing if isinstance(existing, dict) else {}
    combined = dict(existing_counts)

    for key, value in guard_patch.items():
        if not isinstance(value, int):
            continue
        previous = combined.get(key, 0)
        combined[key] = (previous if isinstance(previous, int) else 0) + value

    merged["language_guard"] = combined
    return merged


def _flush_language_guard_meta(session_id: UUID | str) -> None:
    patch = drain_counter_patch()
    if not patch:
        return

    path = _session_dir(session_id) / "session_meta.json"
    existing = _read_json(path)
    if not isinstance(existing, dict):
        existing = {}
    _write_json_atomic(path, _merge_language_guard_patch(existing, patch))


# ── Public API: call these from route handlers ──


def save_session_meta(session_id: UUID | str, meta: dict) -> None:
    """Save/update session_meta.json."""
    d = _session_dir(session_id)
    existing = _read_json(d / "session_meta.json")
    existing.update(meta)
    existing = _merge_language_guard_patch(existing, drain_counter_patch())
    _write_json_atomic(d / "session_meta.json", existing)


def load_session_meta(session_id: UUID | str) -> dict:
    """Load session_meta.json."""
    data = _read_json(_session_dir(session_id) / "session_meta.json")
    return data if isinstance(data, dict) else {}


def save_elicitation(session_id: UUID | str, elicitation_history: dict) -> None:
    """Save/update elicitation.json (conversation history + extracted info)."""
    _write_json_atomic(_session_dir(session_id) / "elicitation.json", elicitation_history)


def load_elicitation(session_id: UUID | str) -> dict:
    """Load elicitation.json."""
    data = _read_json(_session_dir(session_id) / "elicitation.json")
    return data if isinstance(data, dict) else {}


def save_conflict_profile(session_id: UUID | str, profile: dict) -> None:
    """Save/update conflict_profile.json."""
    _write_json_atomic(_session_dir(session_id) / "conflict_profile.json", profile)


def save_elicitation_outcome(session_id: UUID | str, outcome: dict) -> None:
    """Save/update elicitation_outcome.json."""
    _write_json_atomic(_session_dir(session_id) / "elicitation_outcome.json", outcome)


def load_elicitation_outcome(session_id: UUID | str) -> dict:
    """Load elicitation_outcome.json."""
    data = _read_json(_session_dir(session_id) / "elicitation_outcome.json")
    return data if isinstance(data, dict) else {}


def save_portrait(session_id: UUID | str, portrait: dict) -> None:
    """Save/update portrait.json."""
    _write_json_atomic(_session_dir(session_id) / "portrait.json", portrait)


def load_portrait(session_id: UUID | str) -> dict:
    """Load portrait.json."""
    data = _read_json(_session_dir(session_id) / "portrait.json")
    return data if isinstance(data, dict) else {}


def save_identity_cards(session_id: UUID | str, cards: Any) -> None:
    """Save/update identity_cards.json."""
    _write_json_atomic(_session_dir(session_id) / "identity_cards.json", cards)


def load_identity_cards(session_id: UUID | str) -> list:
    """Load identity_cards.json."""
    data = _read_json(_session_dir(session_id) / "identity_cards.json")
    return data if isinstance(data, list) else []


def save_debate_statements(session_id: UUID | str, statements: list) -> None:
    """Save/update debate_statements.json."""
    _write_json_atomic(_session_dir(session_id) / "debate_statements.json", statements)


def load_debate_statements(session_id: UUID | str) -> list:
    """Load debate_statements.json."""
    data = _read_json(_session_dir(session_id) / "debate_statements.json")
    return data if isinstance(data, list) else []


def load_workspace_debate_statements() -> list:
    """Load workspace fixture, falling back to the parent worktree when needed."""
    workspace_root = Path(config.WORKSPACE_ROOT).expanduser()
    candidate_paths = (
        workspace_root / "debate_statements.json",
        workspace_root.parent / "debate_statements.json",
    )

    for path in candidate_paths:
        data = _read_json(path)
        if isinstance(data, list) and data:
            return data
    return []


def save_debate_artifacts(session_id: UUID | str, artifacts: dict) -> None:
    """Save/update debate_artifacts.json."""
    _write_json_atomic(_session_dir(session_id) / "debate_artifacts.json", artifacts)
    _flush_language_guard_meta(session_id)


def load_debate_artifacts(session_id: UUID | str) -> dict:
    """Load debate_artifacts.json."""
    data = _read_json(_session_dir(session_id) / "debate_artifacts.json")
    return data if isinstance(data, dict) else {}


def save_synthesis(session_id: UUID | str, synthesis: dict) -> None:
    """Save/update synthesis.json. A fresh synthesis invalidates any cached report."""
    _write_json_atomic(_session_dir(session_id) / "synthesis.json", synthesis)
    delete_session_export(session_id, "report.html")
    _flush_language_guard_meta(session_id)


def load_synthesis(session_id: UUID | str) -> dict:
    """Load synthesis.json."""
    data = _read_json(_session_dir(session_id) / "synthesis.json")
    return data if isinstance(data, dict) else {}


def save_report_html(session_id: UUID | str, html: str) -> None:
    """Save/update the cached downloadable report.html."""
    _write_text_atomic(_session_dir(session_id) / "report.html", html)


def load_report_html(session_id: UUID | str) -> str | None:
    """Load cached report.html, or None when absent/unreadable."""
    path = _session_dir(session_id) / "report.html"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def delete_session_export(session_id: UUID | str, filename: str) -> None:
    """Delete a persisted session export file if it exists."""
    (_session_dir(session_id) / filename).unlink(missing_ok=True)


def append_debate_round(session_id: UUID | str, round_statements: list) -> None:
    """Append new round statements to debate_statements.json."""
    filepath = _session_dir(session_id) / "debate_statements.json"
    existing = _read_json(filepath)
    if not isinstance(existing, list):
        existing = []
    existing.extend(round_statements)
    _write_json_atomic(filepath, existing)


def append_intervention(session_id: UUID | str, intervention: dict) -> None:
    """Append a single intervention to user_interventions.json."""
    filepath = _session_dir(session_id) / "user_interventions.json"
    existing = _read_json(filepath)
    if not isinstance(existing, list):
        existing = []
    existing.append(intervention)
    _write_json_atomic(filepath, existing)


def save_reflections(session_id: UUID | str, reflections: list) -> None:
    """Save/update reflections.json."""
    _write_json_atomic(_session_dir(session_id) / "reflections.json", reflections)


def load_reflections(session_id: UUID | str) -> list:
    """Load reflections.json."""
    data = _read_json(_session_dir(session_id) / "reflections.json")
    return data if isinstance(data, list) else []


def append_reflection(session_id: UUID | str, reflection: dict) -> None:
    """Append a single reflection to reflections.json."""
    filepath = _session_dir(session_id) / "reflections.json"
    existing = _read_json(filepath)
    if not isinstance(existing, list):
        existing = []
    existing.append(reflection)
    _write_json_atomic(filepath, existing)


def save_reflection_state(session_id: UUID | str, state: dict) -> None:
    """Save/update reflection_state.json."""
    _write_json_atomic(_session_dir(session_id) / "reflection_state.json", state)


def save_reflection_trace(session_id: UUID | str, trace: dict) -> None:
    """Save/update reflection_trace.json."""
    _write_json_atomic(_session_dir(session_id) / "reflection_trace.json", trace)


def load_reflection_trace(session_id: UUID | str) -> dict:
    """Load reflection_trace.json."""
    data = _read_json(_session_dir(session_id) / "reflection_trace.json")
    return data if isinstance(data, dict) else {}


def save_closure_summary(session_id: UUID | str, summary: dict) -> None:
    """Save/update closure_summary.json."""
    _write_json_atomic(_session_dir(session_id) / "closure_summary.json", summary)


def save_closure_emotion(session_id: UUID | str, emotion: dict) -> None:
    """Save/update closure_emotion.json."""
    _write_json_atomic(_session_dir(session_id) / "closure_emotion.json", emotion)


def load_closure_emotion(session_id: UUID | str) -> dict:
    """Load closure_emotion.json."""
    data = _read_json(_session_dir(session_id) / "closure_emotion.json")
    return data if isinstance(data, dict) else {}


def _read_json(filepath: Path) -> Any:
    """Read existing JSON file, return empty dict/list on failure."""
    if not filepath.exists():
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
