"""Memory & Sticker Cache Inspection Routes."""
from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..core.rbac import SessionUser, require_group_access
from ..db.engine import get_readonly_db
from ..db.repositories.memory_repo import memory_repo
from ..schemas.memory import (
    BatchDeleteRequest,
    MemoryCursorResponse,
    MemoryHistoryItem,
    MemoryItem,
    StickerCursorResponse,
    StickerItem,
)

router = APIRouter(prefix="/api/v1/groups/{gid}", tags=["Memories & Stickers"])


@router.get("/memories", response_model=MemoryCursorResponse)
async def list_memories(
    gid: str,
    cursor: Optional[float] = Query(None, description="Cursor timestamp for reverse pagination"),
    limit: int = Query(20, ge=1, le=100),
    kind: Optional[str] = Query(None, description="fact | event | impression"),
    status: str = Query("active", description="active | candidate | pending_review | all"),
    q: Optional[str] = Query(None, description="Fuzzy content keyword search"),
    subject_id: Optional[str] = Query(None, description="Target QQ user ID"),
    user: SessionUser = Depends(require_group_access),
    conn=Depends(get_readonly_db),
):
    """Cursor-paginated query of long-term memories under exact scope."""
    scope = await memory_repo.resolve_scope(conn, gid)
    if not scope:
        return MemoryCursorResponse(items=[], next_cursor=None, has_more=False)

    records, next_cur = await memory_repo.list_memories(
        conn=conn,
        scope=scope,
        cursor=cursor,
        limit=limit,
        status=status,
        kind=kind,
        query=q,
        subject_id=subject_id,
    )
    return MemoryCursorResponse(
        items=[MemoryItem(**r) for r in records],
        next_cursor=next_cur,
        has_more=next_cur is not None,
    )


@router.get("/memories/{mid}/history", response_model=list[MemoryHistoryItem])
async def get_memory_history(
    gid: str,
    mid: str,
    user: SessionUser = Depends(require_group_access),
    conn=Depends(get_readonly_db),
):
    """Fetch full version evolution and audit log for a memory item."""
    scope = await memory_repo.resolve_scope(conn, gid)
    if not scope:
        raise HTTPException(status_code=404, detail="Group scope not found")

    history = await memory_repo.get_memory_history(conn, scope, mid)
    return [MemoryHistoryItem(**h) for h in history]


@router.delete("/memories/{mid}")
async def delete_single_memory(
    gid: str,
    mid: str,
    user: SessionUser = Depends(require_group_access),
    conn=Depends(get_readonly_db),
):
    """Single memory deletion: marks deleted, cleans memory_subjects, and adds memory_guard."""
    scope = await memory_repo.resolve_scope(conn, gid)
    if not scope:
        raise HTTPException(status_code=404, detail="Group scope not found")

    success = await memory_repo.delete_memory(scope=scope, memory_id=mid, operator_id=user.user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Memory not found or already deleted")
    return {"ok": True, "id": mid}


@router.delete("/memories")
async def batch_delete_memories(
    gid: str,
    payload: BatchDeleteRequest,
    user: SessionUser = Depends(require_group_access),
    conn=Depends(get_readonly_db),
):
    """Batch delete polluted memories, cleans memory_subjects, and installs memory guards."""
    scope = await memory_repo.resolve_scope(conn, gid)
    if not scope:
        raise HTTPException(status_code=404, detail="Group scope not found")

    deleted_count = await memory_repo.batch_delete_memories(
        scope=scope,
        memory_ids=payload.ids,
        operator_id=user.user_id,
    )
    return {"deleted": deleted_count, "total_requested": len(payload.ids)}


@router.get("/stickers", response_model=StickerCursorResponse)
async def list_stickers(
    gid: str,
    cursor: Optional[str] = Query(None, description="Sticker ID cursor"),
    limit: int = Query(30, ge=1, le=100),
    q: Optional[str] = Query(None, description="Keyword in sticker description/text"),
    user: SessionUser = Depends(require_group_access),
    conn=Depends(get_readonly_db),
):
    """Cursor-paginated query of sticker/multimodal descriptions.
    
    Returns pure text metadata without transporting raw image binaries.
    """
    scope = await memory_repo.resolve_scope(conn, gid)
    if not scope:
        return StickerCursorResponse(items=[], next_cursor=None, has_more=False)

    stickers, next_cur = await memory_repo.list_stickers(
        conn=conn, scope=scope, cursor_id=cursor, limit=limit, query=q
    )
    return StickerCursorResponse(
        items=[StickerItem(**s) for s in stickers],
        next_cursor=next_cur,
        has_more=next_cur is not None,
    )


@router.delete("/stickers/{sid}")
async def delete_sticker(
    gid: str,
    sid: str,
    user: SessionUser = Depends(require_group_access),
    conn=Depends(get_readonly_db),
):
    """Soft delete an erroneously captioned sticker."""
    scope = await memory_repo.resolve_scope(conn, gid)
    if not scope:
        raise HTTPException(status_code=404, detail="Group scope not found")

    success = await memory_repo.delete_sticker(scope=scope, sticker_id=sid)
    if not success:
        raise HTTPException(status_code=404, detail="Sticker not found or already deleted")
    return {"ok": True, "id": sid}


@router.get("/events")
async def list_recent_events(
    gid: str,
    limit: int = Query(30, ge=1, le=100),
    before_seq: Optional[int] = Query(None),
    user: SessionUser = Depends(require_group_access),
    conn=Depends(get_readonly_db),
):
    """Fetch chat history waterfall under exact scope for debugging context."""
    scope = await memory_repo.resolve_scope(conn, gid)
    if not scope:
        return []
    return await memory_repo.get_recent_events(conn, scope, limit, before_seq)
