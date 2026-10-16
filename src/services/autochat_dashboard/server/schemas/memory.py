"""Memory & Sticker Cache Schemas."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class MemoryItem(BaseModel):
    id: str
    scope: str
    kind: str
    content: str
    status: str
    version: int
    time: float
    subject_ids: List[str] = Field(default_factory=list)
    evidence: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_type: str = "unknown"
    created_at: float = 0.0
    manual: Optional[Dict[str, Any]] = None


class MemoryCursorResponse(BaseModel):
    items: List[MemoryItem]
    next_cursor: Optional[float] = None
    has_more: bool = False


class MemoryHistoryItem(BaseModel):
    id: str
    version: int
    kind: str
    content: str
    status: str
    changed_at: float
    audit: Dict[str, Any]
    subject_ids: List[str] = Field(default_factory=list)


class StickerItem(BaseModel):
    id: str
    scope: str
    asset_id: str
    status: str
    version: int
    last_sent: Optional[float] = None
    mime: str = "image/png"
    size: int = 0
    description: str = ""
    text: str = ""
    intents: List[str] = Field(default_factory=list)
    tone: List[str] = Field(default_factory=list)
    avoid_contexts: List[str] = Field(default_factory=list)
    persona_tags: List[str] = Field(default_factory=list)


class StickerCursorResponse(BaseModel):
    items: List[StickerItem]
    next_cursor: Optional[str] = None
    has_more: bool = False


class BatchDeleteRequest(BaseModel):
    ids: List[str] = Field(..., min_length=1, max_length=100)
