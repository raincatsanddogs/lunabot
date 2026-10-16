"""Telemetry & Stats Repository.

Combines `dash_audit_logs` and native `trace` table for comprehensive token accounting
and incremental cursor audit streaming.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiosqlite

from ...schemas.telemetry import AuditLogItem, OverviewStatsResponse


def _get_today_start_timestamp() -> int:
    """Get Unix timestamp for today's 00:00:00 local time."""
    now = datetime.now()
    today_midnight = datetime(now.year, now.month, now.day)
    return int(today_midnight.timestamp())


async def get_overview_stats(
    conn: aiosqlite.Connection, allowed_groups: Optional[List[str]] = None
) -> OverviewStatsResponse:
    """Aggregate token usage and decision counts combining dash_audit_logs & native trace."""
    today_start = _get_today_start_timestamp()

    # 1. Query aggregated metrics from dash_audit_logs
    where_audit = ["created_at >= ?"]
    args_audit = [today_start]
    if allowed_groups and "*" not in allowed_groups:
        placeholders = ",".join("?" for _ in allowed_groups)
        where_audit.append(f"group_id IN ({placeholders})")
        args_audit.extend(allowed_groups)

    audit_sql = f"""
        SELECT 
            COALESCE(SUM(prompt_tokens), 0) AS p_tokens,
            COALESCE(SUM(completion_tokens), 0) AS c_tokens,
            COUNT(CASE WHEN decision = 'replied' THEN 1 END) AS replied_cnt,
            COUNT(CASE WHEN decision != 'replied' THEN 1 END) AS silent_cnt
        FROM dash_audit_logs
        WHERE {" AND ".join(where_audit)}
    """
    async with conn.execute(audit_sql, args_audit) as cur:
        row = await cur.fetchone()
        p_tokens = int(row["p_tokens"])
        c_tokens = int(row["c_tokens"])
        replied_cnt = int(row["replied_cnt"])
        silent_cnt = int(row["silent_cnt"])

    # 2. Check native `trace` table fallback for model_usage if audit logs has zero tokens
    if p_tokens == 0 and c_tokens == 0:
        try:
            async with conn.execute(
                "SELECT payload FROM trace WHERE kind = 'model_usage' AND time >= ?", (today_start,)
            ) as cur:
                trace_rows = await cur.fetchall()
                for tr in trace_rows:
                    try:
                        data = json.loads(tr["payload"])
                        usage = data.get("usage", {})
                        p_tokens += usage.get("input_tokens", 0)
                        c_tokens += usage.get("output_tokens", 0)
                    except Exception:
                        pass
        except Exception:
            pass  # If trace table not present in test env

    # 3. Query group counts
    from .config_repo import list_group_summaries
    summaries = await list_group_summaries(conn, allowed_groups)
    groups_count = len(summaries)
    active_groups_count = sum(1 for g in summaries if g.enabled)

    return OverviewStatsResponse(
        today_total_tokens=p_tokens + c_tokens,
        today_prompt_tokens=p_tokens,
        today_completion_tokens=c_tokens,
        today_replied_count=replied_cnt,
        today_silent_count=silent_cnt,
        groups_count=groups_count,
        active_groups_count=active_groups_count,
    )


async def get_audit_logs_since(
    conn: aiosqlite.Connection,
    since_id: int,
    group_id: Optional[str] = None,
    allowed_groups: Optional[List[str]] = None,
    limit: int = 50,
) -> List[AuditLogItem]:
    """Cursor-based incremental retrieval of decision audit logs."""
    where = ["id > ?"]
    args: List[Any] = [since_id]

    if group_id and group_id != "all":
        where.append("group_id = ?")
        args.append(str(group_id))

    if allowed_groups and "*" not in allowed_groups:
        placeholders = ",".join("?" for _ in allowed_groups)
        where.append(f"group_id IN ({placeholders})")
        args.extend(allowed_groups)

    sql = (
        "SELECT id, scope, group_id, trigger_user_id, trigger_user_name, decision, "
        "probability, draw, details, context_digest, bot_reply, model_used, "
        "prompt_tokens, completion_tokens, latency_ms, created_at "
        "FROM dash_audit_logs "
        "WHERE " + " AND ".join(where) + " ORDER BY id ASC LIMIT ?"
    )
    args.append(limit)

    async with conn.execute(sql, args) as cur:
        rows = await cur.fetchall()

    items = []
    for r in rows:
        details = {}
        try:
            details = json.loads(r["details"])
        except Exception:
            pass

        items.append(
            AuditLogItem(
                id=r["id"],
                scope=r["scope"],
                group_id=str(r["group_id"]),
                trigger_user_id=str(r["trigger_user_id"]),
                trigger_user_name=r["trigger_user_name"] or "",
                decision=r["decision"],
                probability=float(r["probability"]),
                draw=float(r["draw"]),
                details=details,
                context_digest=r["context_digest"] or "",
                bot_reply=r["bot_reply"] or "",
                model_used=r["model_used"] or "",
                prompt_tokens=int(r["prompt_tokens"]),
                completion_tokens=int(r["completion_tokens"]),
                latency_ms=int(r["latency_ms"]),
                created_at=int(r["created_at"]),
            )
        )
    return items
