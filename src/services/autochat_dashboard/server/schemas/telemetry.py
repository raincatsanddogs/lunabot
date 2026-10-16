"""Telemetry, Audit Stream & Bootstrap Schemas."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from .auth import SessionUserResponse
from .group_config import GroupSummary


class OverviewStatsResponse(BaseModel):
    today_total_tokens: int = 0
    today_prompt_tokens: int = 0
    today_completion_tokens: int = 0
    today_replied_count: int = 0
    today_silent_count: int = 0
    groups_count: int = 0
    active_groups_count: int = 0


class AuditLogItem(BaseModel):
    id: int
    scope: str
    group_id: str
    trigger_user_id: str
    trigger_user_name: str = ""
    decision: str
    probability: float
    draw: float = 0.0
    details: Dict[str, Any] = Field(default_factory=dict)
    context_digest: str = ""
    bot_reply: str = ""
    model_used: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    created_at: int


class BootstrapResponse(BaseModel):
    """Ultra-lightweight bootstrap payload (< 2KB) for instantaneous initial render."""
    user: SessionUserResponse
    global_enabled: bool
    config_watermark: int
    stats: OverviewStatsResponse
    groups: List[GroupSummary]
