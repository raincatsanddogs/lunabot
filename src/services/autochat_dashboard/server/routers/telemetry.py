"""Telemetry & Real-time Server-Sent Events (SSE) Audit Streaming."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from ..core.rbac import SessionUser, get_current_user
from ..db.engine import get_engine, get_readonly_db
from ..db.repositories.stats_repo import get_audit_logs_since, get_overview_stats
from ..schemas.telemetry import OverviewStatsResponse

logger = logging.getLogger("AutochatDashboard.Telemetry")

router = APIRouter(prefix="/api/v1/telemetry", tags=["Telemetry"])


@router.get("/overview", response_model=OverviewStatsResponse)
async def get_telemetry_overview(
    user: SessionUser = Depends(get_current_user), conn=Depends(get_readonly_db)
):
    """Aggregate token usage and decision counters across authorized groups."""
    return await get_overview_stats(conn, user.allowed_groups)


@router.get("/stream")
async def audit_event_stream(
    request: Request,
    group_id: str = Query("all", description="'all' or specific group_id"),
    since_id: int = Query(0, description="Last received audit record ID for resume"),
    user: SessionUser = Depends(get_current_user),
):
    """Server-Sent Events (SSE) streaming real-time decision audits with cursor resume.
    
    Includes 2-second batching to save HTTP chunk headers, 15-second heartbeat ping,
    and automatic teardown upon client disconnection.
    """
    engine = get_engine()

    async def sse_generator():
        last_id = since_id

        # 1. Initial snapshot: if since_id is 0, fetch latest 15 records
        if last_id == 0:
            async with engine.connect(read_only=True) as conn:
                where = []
                args = []
                if group_id != "all":
                    where.append("group_id = ?")
                    args.append(group_id)
                if not user.is_superuser:
                    placeholders = ",".join("?" for _ in user.allowed_groups)
                    where.append(f"group_id IN ({placeholders})")
                    args.extend(user.allowed_groups)

                where_clause = ("WHERE " + " AND ".join(where)) if where else ""
                sql = f"SELECT * FROM dash_audit_logs {where_clause} ORDER BY id DESC LIMIT 15"
                async with conn.execute(sql, args) as cur:
                    initial_rows = await cur.fetchall()

            if initial_rows:
                # Reverse to ascending order
                initial_rows.reverse()
                items = [
                    {
                        "id": r["id"],
                        "scope": r["scope"],
                        "group_id": str(r["group_id"]),
                        "trigger_user_id": str(r["trigger_user_id"]),
                        "trigger_user_name": r["trigger_user_name"] or "",
                        "decision": r["decision"],
                        "probability": float(r["probability"]),
                        "draw": float(r["draw"]),
                        "context_digest": r["context_digest"] or "",
                        "bot_reply": r["bot_reply"] or "",
                        "model_used": r["model_used"] or "",
                        "prompt_tokens": int(r["prompt_tokens"]),
                        "completion_tokens": int(r["completion_tokens"]),
                        "latency_ms": int(r["latency_ms"]),
                        "created_at": int(r["created_at"]),
                    }
                    for r in initial_rows
                ]
                last_id = items[-1]["id"]
                yield f"id: {last_id}\nevent: audit_batch\ndata: {json.dumps(items, ensure_ascii=False)}\n\n"

        idle_cycles = 0

        # 2. Incremental polling loop (every 2.0s)
        while True:
            if await request.is_disconnected():
                break

            try:
                async with engine.connect(read_only=True) as conn:
                    logs = await get_audit_logs_since(
                        conn=conn,
                        since_id=last_id,
                        group_id=group_id,
                        allowed_groups=user.allowed_groups,
                        limit=30,
                    )

                if logs:
                    idle_cycles = 0
                    last_id = logs[-1].id
                    batch_data = [l.model_dump() for l in logs]
                    yield f"id: {last_id}\nevent: audit_batch\ndata: {json.dumps(batch_data, ensure_ascii=False)}\n\n"
                else:
                    idle_cycles += 1
                    # Send lightweight 7-byte ping every ~14-16s to keep connection alive
                    if idle_cycles >= 7:
                        idle_cycles = 0
                        yield ": ping\n\n"
            except Exception as exc:
                logger.warning(f"Error in SSE stream query: {exc}")

            await asyncio.sleep(2.0)

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable proxy buffering in Nginx
        },
    )
