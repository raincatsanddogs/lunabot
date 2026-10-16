"""Authentication & Session Schemas."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ExchangeTicketRequest(BaseModel):
    ticket: str = Field(..., description="Raw 32-byte hex one-time authentication ticket")


class SessionUserResponse(BaseModel):
    user_id: str
    role: str
    allowed_groups: List[str]
    allowed_scopes: List[str]


class GlobalStateResponse(BaseModel):
    global_enabled: bool
    config_watermark: int
    llm_provider_config: Dict[str, Any] = Field(default_factory=dict)


class GlobalStateUpdate(BaseModel):
    global_enabled: Optional[bool] = None
    llm_provider_config: Optional[Dict[str, Any]] = None
