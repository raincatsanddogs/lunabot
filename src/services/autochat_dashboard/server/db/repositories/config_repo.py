"""Configuration Repository for Per-Group Autochat Settings & Watermark Invalidation."""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

from ...schemas.group_config import GroupConfigDetail, GroupConfigUpdate, GroupSummary


async def get_global_state(conn: aiosqlite.Connection) -> Tuple[bool, int, Dict[str, Any]]:
    """Retrieve global switch, watermark, and LLM configuration."""
    async with conn.execute(
        "SELECT key, value_int, value_text FROM dash_global_kv"
    ) as cursor:
        rows = await cursor.fetchall()

    enabled = True
    watermark = 1
    llm_cfg = {}

    for r in rows:
        k = r["key"]
        if k == "global_autochat_enabled":
            enabled = bool(r["value_int"])
        elif k == "config_watermark":
            watermark = int(r["value_int"])
        elif k == "llm_provider_config":
            try:
                llm_cfg = json.loads(r["value_text"])
            except Exception:
                pass

    return enabled, watermark, llm_cfg


async def update_global_state(
    conn: aiosqlite.Connection,
    enabled: Optional[bool] = None,
    llm_config: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, int]:
    """Update global switch and advance config watermark."""
    now_ms = int(time.time() * 1000)

    if enabled is not None:
        await conn.execute(
            """
            INSERT INTO dash_global_kv (key, value_int, value_text, updated_at)
            VALUES ('global_autochat_enabled', ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value_int = excluded.value_int, updated_at = excluded.updated_at
            """,
            (1 if enabled else 0, "true" if enabled else "false", now_ms),
        )

    if llm_config is not None:
        await conn.execute(
            """
            INSERT INTO dash_global_kv (key, value_int, value_text, updated_at)
            VALUES ('llm_provider_config', 0, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value_text = excluded.value_text, updated_at = excluded.updated_at
            """,
            (json.dumps(llm_config, ensure_ascii=False), now_ms),
        )

    # Advance watermark
    await conn.execute(
        """
        INSERT INTO dash_global_kv (key, value_int, value_text, updated_at)
        VALUES ('config_watermark', ?, '', ?)
        ON CONFLICT(key) DO UPDATE SET value_int = excluded.value_int, updated_at = excluded.updated_at
        """,
        (now_ms, now_ms),
    )

    curr_enabled, curr_watermark, _ = await get_global_state(conn)
    return curr_enabled, curr_watermark


async def list_group_summaries(
    conn: aiosqlite.Connection, allowed_groups: Optional[List[str]] = None
) -> List[GroupSummary]:
    """Retrieve lightweight summaries of groups for bootstrap payload."""
    query = "SELECT scope, group_id, group_name_cache, enabled, ambient_p, updated_at FROM dash_group_config"
    args = []

    if allowed_groups and "*" not in allowed_groups:
        placeholders = ",".join("?" for _ in allowed_groups)
        query += f" WHERE group_id IN ({placeholders})"
        args.extend(allowed_groups)

    query += " ORDER BY group_id ASC"

    async with conn.execute(query, args) as cur:
        rows = await cur.fetchall()

    return [
        GroupSummary(
            scope=r["scope"],
            group_id=str(r["group_id"]),
            group_name=r["group_name_cache"] or f"群 {r['group_id']}",
            enabled=bool(r["enabled"]),
            ambient_p=float(r["ambient_p"]),
            updated_at=int(r["updated_at"]),
        )
        for r in rows
    ]


async def get_group_config_by_scope(
    conn: aiosqlite.Connection, scope: str
) -> Optional[GroupConfigDetail]:
    """Retrieve full configuration for an exact scope."""
    sql = "SELECT * FROM dash_group_config WHERE scope = ? LIMIT 1"
    async with conn.execute(sql, (scope,)) as cur:
        row = await cur.fetchone()
        if not row:
            return None
        return _parse_config_row(row)


async def get_group_config_by_gid(
    conn: aiosqlite.Connection, group_id: str
) -> Optional[GroupConfigDetail]:
    """Retrieve full configuration by group_id."""
    sql = "SELECT * FROM dash_group_config WHERE group_id = ? LIMIT 1"
    async with conn.execute(sql, (str(group_id),)) as cur:
        row = await cur.fetchone()
        if not row:
            return None
        return _parse_config_row(row)


async def upsert_group_config(
    conn: aiosqlite.Connection,
    scope: str,
    group_id: str,
    update: GroupConfigUpdate,
) -> GroupConfigDetail:
    """Upsert configuration and advance global config watermark."""
    now_ms = int(time.time() * 1000)
    existing = await get_group_config_by_scope(conn, scope)

    group_name = update.group_name_cache if update.group_name_cache is not None else (existing.group_name_cache if existing else "")
    enabled = update.enabled if update.enabled is not None else (existing.enabled if existing else True)
    ambient_p = update.ambient_p if update.ambient_p is not None else (existing.ambient_p if existing else 0.04)
    followup_p = update.followup_p if update.followup_p is not None else (existing.followup_p if existing else 0.85)
    cooldown = update.cooldown_sec if update.cooldown_sec is not None else (existing.cooldown_sec if existing else 25)
    keywords = update.trigger_keywords if update.trigger_keywords is not None else (existing.trigger_keywords if existing else {})
    persona = update.persona if update.persona is not None else (existing.persona if existing else "")
    blacklist = update.blacklist_user_ids if update.blacklist_user_ids is not None else (existing.blacklist_user_ids if existing else [])

    await conn.execute(
        """
        INSERT INTO dash_group_config (
            scope, group_id, group_name_cache, enabled, ambient_p, followup_p,
            cooldown_sec, trigger_keywords, persona, blacklist_user_ids, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scope) DO UPDATE SET
            group_name_cache = excluded.group_name_cache,
            enabled = excluded.enabled,
            ambient_p = excluded.ambient_p,
            followup_p = excluded.followup_p,
            cooldown_sec = excluded.cooldown_sec,
            trigger_keywords = excluded.trigger_keywords,
            persona = excluded.persona,
            blacklist_user_ids = excluded.blacklist_user_ids,
            updated_at = excluded.updated_at
        """,
        (
            scope,
            str(group_id),
            group_name,
            1 if enabled else 0,
            ambient_p,
            followup_p,
            cooldown,
            json.dumps(keywords, ensure_ascii=False),
            persona,
            json.dumps(blacklist, ensure_ascii=False),
            now_ms,
        ),
    )

    # Invalidate watermark in the same transaction
    await conn.execute(
        """
        INSERT INTO dash_global_kv (key, value_int, value_text, updated_at)
        VALUES ('config_watermark', ?, '', ?)
        ON CONFLICT(key) DO UPDATE SET value_int = excluded.value_int, updated_at = excluded.updated_at
        """,
        (now_ms, now_ms),
    )

    return GroupConfigDetail(
        scope=scope,
        group_id=str(group_id),
        group_name_cache=group_name,
        enabled=enabled,
        ambient_p=ambient_p,
        followup_p=followup_p,
        cooldown_sec=cooldown,
        trigger_keywords=keywords,
        persona=persona,
        blacklist_user_ids=blacklist,
        updated_at=now_ms,
    )


def _parse_config_row(row: aiosqlite.Row) -> GroupConfigDetail:
    keywords = {}
    blacklist = []
    try:
        keywords = json.loads(row["trigger_keywords"])
    except Exception:
        pass
    try:
        blacklist = json.loads(row["blacklist_user_ids"])
    except Exception:
        pass

    return GroupConfigDetail(
        scope=row["scope"],
        group_id=str(row["group_id"]),
        group_name_cache=row["group_name_cache"] or "",
        enabled=bool(row["enabled"]),
        ambient_p=float(row["ambient_p"]),
        followup_p=float(row["followup_p"]),
        cooldown_sec=int(row["cooldown_sec"]),
        trigger_keywords=keywords,
        persona=row["persona"] or "",
        blacklist_user_ids=blacklist,
        updated_at=int(row["updated_at"]),
    )
