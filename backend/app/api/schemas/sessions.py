"""Request/response schemas for sessions and Phase 0."""
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field


class CreateSessionBody(BaseModel):
    user_id: Optional[UUID] = None
    display_name: Optional[str] = None
    locale: str = Field(default="zh-CN", description="User locale for framing suggestion")


class CreateSessionResponse(BaseModel):
    session_id: UUID
    status: str


class ConsentBody(BaseModel):
    accepted: bool = True


class FramingBody(BaseModel):
    framing: str = Field(..., description="inner_parts | perspective | advisory | neutral")


class SafetyConfirmBody(BaseModel):
    confirmations: list[bool] = Field(..., min_length=3, max_length=3)
