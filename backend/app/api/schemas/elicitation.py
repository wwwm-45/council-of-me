"""Phase 1 elicitation API schemas."""

from typing import Any, Optional

from pydantic import BaseModel, Field


class ElicitationBody(BaseModel):
    message: str = Field(..., min_length=1)


class ElicitationEditBody(BaseModel):
    message: str = Field(..., min_length=1)


class ElicitationFinishBody(BaseModel):
    force: bool = False


class DepthInfo(BaseModel):
    depth_score: float
    depth_layer: int
    current_layer: int
    recommended_action: str
    strategy_hint: str
    emotional_state: str
    graduation_ready: bool


class ElicitationResponse(BaseModel):
    response: str
    should_continue: bool
    round: int
    depth: Optional[DepthInfo] = None
    conflict_profile_draft: Optional[dict[str, Any]] = None
    elicitation_outcome: Optional[dict[str, Any]] = None
    tension_cards: Optional[list[dict[str, Any]]] = None
    focus_card_id: Optional[str] = None
    safety_warning: Optional[str] = None
    portrait_quality: Optional[dict[str, Any]] = None
    requires_quality_confirmation: bool = False


class ProfileConfirmBody(BaseModel):
    use_as_is: bool = False
