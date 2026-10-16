"""Native Table Adapter for LunaBot Autochat.

Decouples Dashboard operations from LunaBot's internal schema.
Strictly uses exact scope matches (e.g. `WHERE scope = :exact_scope`)
to guarantee 100% SQLite B-Tree index utilization without wildcard scans.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite


class NativeTableAdapter:
    """Adapter for interacting with LunaBot's native tables:
    - memories & memory_subjects
    - stickers & assets
    - events
    - trace
    """

    def __init__(self):
        # In-memory fast cache mapping group_id -> exact_scope (e.g. "123456" -> "10001:123456")
        self._scope_cache: Dict[str, str] = {}

    async def resolve_scope(self, conn: aiosqlite.Connection, group_id: str) -> Optional[str]:
        """Resolve exact scope ('bot_id:group_id') for a given group_id with caching.
        
        Never uses wildcard queries during frequent operations; resolves once and caches.
        """
        if not group_id:
            return None
        if group_id in self._scope_cache:
            return self._scope_cache[group_id]

        # 1. Try resolving from dash_group_config
        async with conn.execute(
            "SELECT scope FROM dash_group_config WHERE group_id = ? LIMIT 1", (group_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row and row["scope"]:
                exact_scope = row["scope"]
                self._scope_cache[group_id] = exact_scope
                return exact_scope

        # 2. Try resolving from events table (get the latest event for this group)
        try:
            async with conn.execute(
                "SELECT scope FROM events WHERE scope LIKE ? ORDER BY seq DESC LIMIT 1", (f"%:{group_id}",)
            ) as cursor:
                row = await cursor.fetchone()
                if row and row["scope"]:
                    exact_scope = row["scope"]
                    self._scope_cache[group_id] = exact_scope
                    return exact_scope
        except Exception:
            pass

        # 3. Try resolving from memories table
        try:
            async with conn.execute(
                "SELECT scope FROM memories WHERE scope LIKE ? LIMIT 1", (f"%:{group_id}",)
            ) as cursor:
                row = await cursor.fetchone()
                if row and row["scope"]:
                    exact_scope = row["scope"]
                    self._scope_cache[group_id] = exact_scope
                    return exact_scope
        except Exception:
            pass

        return None

    def register_scope(self, group_id: str, scope: str) -> None:
        """Register or update in-memory mapping."""
        self._scope_cache[group_id] = scope

    # -------------------------------------------------------------------------
    # Memories Management (memories, memory_subjects, memory_history, memory_guards)
    # -------------------------------------------------------------------------

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
        """Query memories using exact scope matching with cursor pagination.
        
        Returns:
            (records, next_cursor_time)
        """
        where_clauses = ["m.scope = ?"]
        args: List[Any] = [scope]

        if status and status != "all":
            where_clauses.append("m.status = ?")
            args.append(status)

        if kind:
            where_clauses.append("m.kind = ?")
            args.append(kind)

        if cursor is not None:
            # Cursor is based on reverse chronological time
            where_clauses.append("m.time < ?")
            args.append(cursor)

        if query:
            where_clauses.append("instr(lower(m.content), lower(?)) > 0")
            args.append(query.strip())

        if subject_id:
            where_clauses.append(
                "EXISTS (SELECT 1 FROM memory_subjects s WHERE s.memory_id = m.id AND s.subject = ?)"
            )
            args.append(str(subject_id))

        sql = (
            "SELECT m.id, m.scope, m.kind, m.content, m.status, m.version, m.payload, m.time "
            "FROM memories m WHERE "
            + " AND ".join(where_clauses)
            + " ORDER BY m.time DESC, m.id DESC LIMIT ?"
        )
        args.append(limit + 1)

        async with conn.execute(sql, args) as cur:
            rows = await cur.fetchall()

        has_more = len(rows) > limit
        selected_rows = rows[:limit]

        records = []
        for r in selected_rows:
            payload = {}
            try:
                payload = json.loads(r["payload"])
            except Exception:
                pass

            records.append(
                {
                    "id": r["id"],
                    "scope": r["scope"],
                    "kind": r["kind"],
                    "content": r["content"],
                    "status": r["status"],
                    "version": r["version"],
                    "time": r["time"],
                    "subject_ids": payload.get("subject_ids", []),
                    "evidence": payload.get("evidence", []),
                    "evidence_type": payload.get("evidence_type", "unknown"),
                    "created_at": payload.get("created_at", r["time"]),
                    "manual": payload.get("manual"),
                }
            )

        next_cursor = selected_rows[-1]["time"] if (has_more and selected_rows) else None
        return records, next_cursor

    async def get_memory_history(
        self, conn: aiosqlite.Connection, scope: str, memory_id: str
    ) -> List[Dict[str, Any]]:
        """Get the full version history and audit log for a memory entry."""
        sql = (
            "SELECT id, version, scope, kind, content, status, payload, changed_at, audit "
            "FROM memory_history WHERE scope = ? AND id = ? ORDER BY version DESC"
        )
        async with conn.execute(sql, (scope, memory_id)) as cur:
            rows = await cur.fetchall()

        history = []
        for r in rows:
            audit = {}
            payload = {}
            try:
                audit = json.loads(r["audit"])
            except Exception:
                pass
            try:
                payload = json.loads(r["payload"])
            except Exception:
                pass

            history.append(
                {
                    "id": r["id"],
                    "version": r["version"],
                    "kind": r["kind"],
                    "content": r["content"],
                    "status": r["status"],
                    "changed_at": r["changed_at"],
                    "audit": audit,
                    "subject_ids": payload.get("subject_ids", []),
                }
            )
        return history

    async def delete_memory(
        self, conn: aiosqlite.Connection, scope: str, memory_id: str, operator_id: str
    ) -> bool:
        """Mark memory as deleted and install a memory guard to prevent model re-learning."""
        async with conn.execute(
            "SELECT * FROM memories WHERE scope = ? AND id = ?", (scope, memory_id)
        ) as cur:
            row = await cur.fetchone()
            if not row or row["status"] == "deleted":
                return False

        now = time.time()
        payload = {}
        try:
            payload = json.loads(row["payload"])
        except Exception:
            pass

        # 1. Install memory guard to block future model proposals using the same fragment
        guard_data = {
            **payload,
            "id": row["id"],
            "content": row["content"],
            "replaced_at": now,
            "operation": "delete",
            "operator_id": operator_id,
        }
        await conn.execute(
            "INSERT INTO memory_guards (scope, memory_id, payload) VALUES (?, ?, ?)",
            (scope, memory_id, json.dumps(guard_data, ensure_ascii=False)),
        )

        # 2. Update memory status to deleted, incrementing version for audit trigger
        new_version = row["version"] + 1
        payload["status"] = "deleted"
        payload["deleted_by"] = operator_id
        payload["deleted_at"] = now

        await conn.execute(
            "UPDATE memories SET status = 'deleted', version = ?, payload = ?, time = ? WHERE scope = ? AND id = ?",
            (new_version, json.dumps(payload, ensure_ascii=False), now, scope, memory_id),
        )

        # 3. Synchronously clean up association rows in memory_subjects
        await conn.execute(
            "DELETE FROM memory_subjects WHERE memory_id = ?",
            (memory_id,),
        )
        return True

    # -------------------------------------------------------------------------
    # Sticker & Image Description Cache Management (stickers & assets)
    # -------------------------------------------------------------------------

    async def list_stickers(
        self,
        conn: aiosqlite.Connection,
        scope: str,
        cursor_id: Optional[str] = None,
        limit: int = 30,
        query: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Query stickers with exact scope matching, joined with assets metadata."""
        where = ["s.scope = ?", "s.status != 'deleted'"]
        args: List[Any] = [scope]

        if cursor_id:
            where.append("s.id < ?")
            args.append(cursor_id)

        if query:
            where.append("instr(lower(s.metadata), lower(?)) > 0")
            args.append(query.strip())

        sql = (
            "SELECT s.id, s.scope, s.asset_id, s.status, s.metadata, s.version, s.last_sent, "
            "a.mime, a.size "
            "FROM stickers s "
            "LEFT JOIN assets a ON s.asset_id = a.id "
            "WHERE " + " AND ".join(where) + " ORDER BY s.id DESC LIMIT ?"
        )
        args.append(limit + 1)

        async with conn.execute(sql, args) as cur:
            rows = await cur.fetchall()

        has_more = len(rows) > limit
        selected_rows = rows[:limit]

        stickers = []
        for r in selected_rows:
            meta = {}
            try:
                meta = json.loads(r["metadata"])
            except Exception:
                pass

            stickers.append(
                {
                    "id": r["id"],
                    "scope": r["scope"],
                    "asset_id": r["asset_id"],
                    "status": r["status"],
                    "version": r["version"],
                    "last_sent": r["last_sent"],
                    "mime": r["mime"] or "image/png",
                    "size": r["size"] or 0,
                    "description": meta.get("description", ""),
                    "text": meta.get("text", ""),
                    "intents": meta.get("intents", []),
                    "tone": meta.get("tone", []),
                    "avoid_contexts": meta.get("avoid_contexts", []),
                    "persona_tags": meta.get("persona_tags", []),
                }
            )

        next_cursor = selected_rows[-1]["id"] if (has_more and selected_rows) else None
        return stickers, next_cursor

    async def delete_sticker(self, conn: aiosqlite.Connection, scope: str, sticker_id: str) -> bool:
        """Soft delete a sticker entry."""
        async with conn.execute(
            "UPDATE stickers SET status = 'deleted', version = version + 1 WHERE scope = ? AND id = ?",
            (scope, sticker_id),
        ) as cur:
            return cur.rowcount > 0

    # -------------------------------------------------------------------------
    # Recent Events (events)
    # -------------------------------------------------------------------------

    async def get_recent_events(
        self, conn: aiosqlite.Connection, scope: str, limit: int = 30, before_seq: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Fetch recent chat events under exact scope for context waterfall inspection."""
        where = ["scope = ?"]
        args: List[Any] = [scope]
        if before_seq is not None:
            where.append("seq < ?")
            args.append(before_seq)

        sql = (
            "SELECT seq, message_id, speaker, time, payload FROM events "
            "WHERE " + " AND ".join(where) + " ORDER BY seq DESC LIMIT ?"
        )
        args.append(limit)

        async with conn.execute(sql, args) as cur:
            rows = await cur.fetchall()

        events = []
        for r in rows:
            data = {}
            try:
                data = json.loads(r["payload"])
            except Exception:
                pass
            events.append(
                {
                    "seq": r["seq"],
                    "message_id": r["message_id"],
                    "speaker": r["speaker"],
                    "time": r["time"],
                    "nickname": data.get("nickname", ""),
                    "segments": data.get("segments", []),
                    "text": "".join(
                        s.get("data", {}).get("text", "")
                        for s in data.get("segments", [])
                        if s.get("type") == "text"
                    ),
                }
            )
        # Reverse to chronological order
        events.reverse()
        return events


# Singleton adapter instance
adapter = NativeTableAdapter()
