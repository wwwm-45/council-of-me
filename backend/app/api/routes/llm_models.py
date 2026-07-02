"""LLM model catalog and runtime model switching endpoints."""
import logging

from fastapi import APIRouter, Body, HTTPException

from app import config
from app.services.llm import _resolve_base_url_for_model, reset_clients
from app.services.debate_engine import _orchestrators

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/llm", tags=["llm"])

# Source references:
# - DeepSeek on Bailian: https://help.aliyun.com/zh/model-studio/deepseek-api
# - Qwen on Bailian: https://help.aliyun.com/zh/model-studio/models
AVAILABLE_MODELS: list[dict[str, str]] = [
    {"id": "deepseek-v4-flash", "label": "DeepSeek V4 Flash", "provider": "deepseek", "family": "deepseek-v4"},
    {"id": "qwen-flash", "label": "Qwen Flash", "provider": "qwen", "family": "qwen-flash"},
]


@router.get("/models")
async def list_models():
    """List available models and current active model."""
    return {
        "current_model": config.LLM_MODEL,
        "wire_api": config.LLM_WIRE_API,
        "base_url": _resolve_base_url_for_model(config.LLM_MODEL),
        "models": AVAILABLE_MODELS,
    }


@router.post("/models/select")
async def select_model(body: dict = Body(...)):
    """Switch active model at runtime. body: { model: str }"""
    model = (body.get("model") or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="model required")

    allowed = {m["id"] for m in AVAILABLE_MODELS}
    if model not in allowed:
        raise HTTPException(status_code=400, detail={"error": "unsupported_model", "model": model})

    old_model = config.LLM_MODEL
    config.LLM_MODEL = model
    reset_clients()

    if _orchestrators and old_model != model:
        cleared = list(_orchestrators.keys())
        _orchestrators.clear()
        logger.info("Model switch %s -> %s: cleared %d cached orchestrator(s)", old_model, model, len(cleared))

    return {"ok": True, "current_model": config.LLM_MODEL}
