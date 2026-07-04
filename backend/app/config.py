"""
Configuration for Council of Me backend.
Project-level .env and environment variables take precedence over workspace defaults.
"""
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

try:
    import tomllib  # Python 3.11+
except Exception:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_toml(path: Path) -> dict[str, Any]:
    if not tomllib or not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _parse_dotenv_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return value


def _load_project_env() -> None:
    """
    Load project-level .env defaults once.
    Skip implicit loading in pytest to keep tests hermetic unless explicitly overridden.
    """
    env_override = os.getenv("COUNCIL_ENV_FILE")
    if "pytest" in sys.modules and not env_override:
        return

    env_path = Path(env_override) if env_override else (PROJECT_ROOT / ".env")
    if not env_path.exists():
        return

    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("export "):
                stripped = stripped[len("export "):].strip()
            if "=" not in stripped:
                continue

            key, raw_value = stripped.split("=", 1)
            key = key.strip()
            if not key:
                continue

            os.environ.setdefault(key, _parse_dotenv_value(raw_value))
    except Exception:
        return


def _clean_setting(value: Any) -> Optional[str]:
    if value is None:
        return None

    text = value.strip() if isinstance(value, str) else str(value).strip()
    if not text:
        return None

    upper = text.upper()
    if (
        upper.startswith("__REPLACE_WITH_")
        or upper.startswith("__SET_")
        or upper.startswith("YOUR_")
        or upper.endswith("_HERE")
        or (text.startswith("<") and text.endswith(">"))
    ):
        return None
    return text


def _pick_setting(*values: Any) -> Optional[str]:
    for value in values:
        cleaned = _clean_setting(value)
        if cleaned is not None:
            return cleaned
    return None


_load_project_env()


def _load_codex_fallbacks() -> dict[str, Any]:
    """
    Optional fallback for local Codex desktop/CLI config:
    - $CODEX_HOME/config.toml
    - $CODEX_HOME/auth.json
    """
    if "pytest" in sys.modules:
        return {}

    codex_home = Path(os.getenv("CODEX_HOME") or (Path.home() / ".codex"))
    cfg = _read_toml(codex_home / "config.toml")
    auth = _read_json(codex_home / "auth.json")

    provider_key = cfg.get("model_provider")
    providers = cfg.get("model_providers") if isinstance(cfg.get("model_providers"), dict) else {}
    provider_cfg: dict[str, Any] = {}
    if isinstance(provider_key, str) and isinstance(providers, dict):
        maybe = providers.get(provider_key)
        if isinstance(maybe, dict):
            provider_cfg = maybe

    return {
        "provider_name": provider_cfg.get("name"),
        "model": cfg.get("model"),
        "base_url": provider_cfg.get("base_url"),
        "wire_api": provider_cfg.get("wire_api"),
        "api_key": auth.get("OPENAI_API_KEY"),
    }


_CODEX = _load_codex_fallbacks()

# Database
DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/council_of_me")

# Safety thresholds (COMPLETE_PROCESS_FLOW Phase 0.3)
SAFETY_CRITICAL_THRESHOLD: float = float(os.getenv("SAFETY_CRITICAL_THRESHOLD", "0.7"))
SAFETY_WARNING_THRESHOLD: float = float(os.getenv("SAFETY_WARNING_THRESHOLD", "0.5"))
SAFETY_AFTER_CRISIS_THRESHOLD: float = float(os.getenv("SAFETY_AFTER_CRISIS_THRESHOLD", "0.5"))

# LLM
LLM_PROVIDER: str = (
    _pick_setting(os.getenv("LLM_PROVIDER"), _CODEX.get("provider_name"))
    or "openai"
)
LLM_MODEL: str = (
    _pick_setting(os.getenv("LLM_MODEL"), _CODEX.get("model"))
    or "deepseek-v4-flash"
)
LLM_API_KEY: Optional[str] = _pick_setting(
    os.getenv("LLM_API_KEY"),
    os.getenv("OPENAI_API_KEY"),
    _CODEX.get("api_key"),
)
LLM_BASE_URL: Optional[str] = _pick_setting(
    os.getenv("LLM_BASE_URL"),
    os.getenv("OPENAI_BASE_URL"),
    _CODEX.get("base_url"),
)
CLAUDE_BASE_URL: Optional[str] = _pick_setting(os.getenv("CLAUDE_BASE_URL"))

# Qwen (OpenAI-compatible via DashScope or other relay)
QWEN_API_KEY: Optional[str] = _pick_setting(os.getenv("QWEN_API_KEY"))
QWEN_BASE_URL: Optional[str] = _pick_setting(os.getenv("QWEN_BASE_URL"))

# DeepSeek (first-party official API, OpenAI-compatible)
DEEPSEEK_API_KEY: Optional[str] = _pick_setting(os.getenv("DEEPSEEK_API_KEY"))
DEEPSEEK_BASE_URL: Optional[str] = _pick_setting(os.getenv("DEEPSEEK_BASE_URL")) or "https://api.deepseek.com"

# Supported: "chat_completions" (default), "responses"
LLM_WIRE_API: str = (
    _pick_setting(os.getenv("LLM_WIRE_API"), _CODEX.get("wire_api"))
    or "chat_completions"
).lower()
LLM_TEMPERATURE_DEFAULT: float = float(os.getenv("LLM_TEMPERATURE_DEFAULT", "0.7"))
# Default output-token ceiling for calls that omit max_tokens. Keep at or below
# the provider's per-request output cap (DeepSeek official: 8192) so budget-less
# callers (identity/synthesis stages) are not rejected with a 400.
LLM_MAX_TOKENS_DEFAULT: int = int(os.getenv("LLM_MAX_TOKENS_DEFAULT", "4096"))
LLM_TIMEOUT_SEC: int = int(os.getenv("LLM_TIMEOUT_SEC", "60"))
LLM_TIMEOUT_SYNTHESIS_SEC: int = int(os.getenv("LLM_TIMEOUT_SYNTHESIS_SEC", "90"))
LLM_MAX_CONCURRENT: int = int(os.getenv("LLM_MAX_CONCURRENT", "3"))

# Debate follow-up gate (asks the user 1-3 questions after R2 and R3.5)
DEBATE_FOLLOWUP_ENABLED: bool = os.getenv("DEBATE_FOLLOWUP_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}

# Embedding
# Default is multilingual (Chinese-capable). To swap models, set EMBEDDING_MODEL
# (e.g. "BAAI/bge-small-zh-v1.5") — note the consistency/convergence rescaling
# constants are calibrated for this default and need re-tuning if you change it.
EMBEDDING_PROVIDER: str = os.getenv("EMBEDDING_PROVIDER", "local")
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")

# Session export directory (for independent session folder export)
SESSION_EXPORT_DIR: str = os.getenv(
    "SESSION_EXPORT_DIR",
    str(WORKSPACE_ROOT / "sessions"),
)
