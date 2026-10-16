"""Memory and Sticker Management Repository.

Provides cursor-paginated queries, keyword searches, single & batch deletions
with synchronous memory_subjects cleanup, and sticker cache eviction.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple
import aiosqlite

from ..adapter import adapter
from ..engine import get_engine

logger = logging.getLogger("AutochatDashboard.MemoryRepo")


class MemoryRepository:
    def __init__(self):
        self.adapter = adapter

    @property
    def engine(self):
        return get_engine()

    async def resolve_scope(self, conn: aiosqlite.Connection, group_id: str) -> Optional[str]:
        return await self.adapter.resolve_scope(conn, group_id)

    async def list_memories(
        self,
        conn: aiosqlite.Connection,
        scope: str,
        cursor: Optional[float] = None,
        limit: int = 20,
        status: str = "active",
        kind: Optional[str] = None,
        query: Optional[str] = None,
        subject_id: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[float]]:
        return await self.adapter.list_memories(
            conn=conn,
            scope=scope,
            cursor=cursor,
            limit=limit,
            status=status,
            kind=kind,
            query=query,
            subject_id=subject_id,
        )

    async def get_memory_history(
        self, conn: aiosqlite.Connection, scope: str, memory_id: str
    ) -> List[Dict[str, Any]]:
        return await self.adapter.get_memory_history(conn, scope, memory_id)

    async def delete_memory(self, scope: str, memory_id: str, operator_id: str) -> bool:
        """Single memory deletion inside a dedicated write transaction."""
        async with self.engine.write_transaction() as conn:
            return await self.adapter.delete_memory(conn, scope, memory_id, operator_id)

    async def batch_delete_memories(
        self, scope: str, memory_ids: List[str], operator_id: str
    ) -> int:
        """Batch memory deletion in a single write transaction."""
        deleted_count = 0
        async with self.engine.write_transaction() as conn:
            for mid in memory_ids:
                if await self.adapter.delete_memory(conn, scope, mid, operator_id):
                    deleted_count += 1
        return deleted_count

    async def list_stickers(
        self,
        conn: aiosqlite.Connection,
        scope: str,
        cursor_id: Optional[str] = None,
        limit: int = 30,
        query: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        return await self.adapter.list_stickers(
            conn=conn, scope=scope, cursor_id=cursor_id, limit=limit, query=query
        )

    async def delete_sticker(self, scope: str, sticker_id: str) -> bool:
        """Single sticker cache eviction inside a write transaction."""
        async with self.engine.write_transaction() as conn:
            return await self.adapter.delete_sticker(conn, scope, sticker_id)

    async def get_recent_events(
        self, conn: aiosqlite.Connection, scope: str, limit: int = 30, before_seq: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        return await self.adapter.get_recent_events(conn, scope, limit, before_seq)


memory_repo = MemoryRepository()
