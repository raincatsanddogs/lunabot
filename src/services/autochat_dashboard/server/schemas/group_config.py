"""Group Configuration & Sandbox Schemas (1:1 Native Parameters)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class GroupSummary(BaseModel):
    """Ultra-lightweight group metadata for bootstrap without long persona strings."""
    scope: str
    group_id: str
    group_name: str = ""
    enabled: bool = True
    ambient_p: float = 0.04
    updated_at: int = 0


class GroupConfigDetail(BaseModel):
    """Full per-group configuration model with 1:1 LunaBot native fields."""
    scope: str
    group_id: str
    group_name_cache: str = ""
    enabled: bool = True
    ambient_p: float = Field(0.04, ge=0.0, le=1.0, description="基础闲聊接话概率")
    followup_p: float = Field(0.85, ge=0.0, le=1.0, description="互动跟进接话概率")
    cooldown_sec: int = Field(25, ge=0, le=3600, description="回复冷却秒数")
    trigger_keywords: Dict[str, float] = Field(default_factory=dict, description="关键词与增量权重字典")
    persona: str = Field("", description="分群专用人设 System Prompt")
    blacklist_user_ids: List[str] = Field(default_factory=list, description="忽略的群成员 QQ 号列表")
    updated_at: int = 0


class GroupConfigUpdate(BaseModel):
    """Payload for partial/full update of group settings."""
    group_name_cache: Optional[str] = None
    enabled: Optional[bool] = None
    ambient_p: Optional[float] = Field(None, ge=0.0, le=1.0)
    followup_p: Optional[float] = Field(None, ge=0.0, le=1.0)
    cooldown_sec: Optional[int] = Field(None, ge=0, le=3600)
    trigger_keywords: Optional[Dict[str, float]] = None
    persona: Optional[str] = None
    blacklist_user_ids: Optional[List[str]] = None


class SandboxEvalRequest(BaseModel):
    """Simulated message for memory-only evaluation of probability and keywords."""
    mock_text: str = Field(..., description="群友模拟发言文本")
    mock_user_id: str = Field("10001", description="发送者 QQ 号")
    is_at_bot: bool = Field(False, description="是否被 @ 或提及 Bot 昵称")
    is_followup: bool = Field(False, description="是否处于正在对话的互动跟进窗口中")


class SandboxEvalResponse(BaseModel):
    will_trigger: bool
    final_probability: float
    base_probability: float
    reason: str
    matched_keywords: Dict[str, float]
    hard_blocked: bool
    blacklisted: bool
    explanation: str
