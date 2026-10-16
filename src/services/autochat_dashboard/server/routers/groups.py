"""Group Configuration & Sandbox Evaluation Routes."""
from __future__ import annotations

import hashlib
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from ..core.native_config import native_config_loader
from ..core.etag import compute_group_config_etag, is_etag_match, not_modified_response
from ..core.rbac import SessionUser, require_group_access, require_superuser
from ..db.adapter import adapter
from ..db.engine import get_engine, get_readonly_db
from ..db.repositories.config_repo import (
    get_global_state,
    get_group_config_by_gid,
    get_group_config_by_scope,
    update_global_state,
    upsert_group_config,
)
from ..schemas.auth import GlobalStateResponse, GlobalStateUpdate
from ..schemas.group_config import (
    GroupConfigDetail,
    GroupConfigUpdate,
    SandboxEvalRequest,
    SandboxEvalResponse,
)

router = APIRouter(prefix="/api/v1", tags=["Groups"])


@router.get("/global/state", response_model=GlobalStateResponse)
async def get_global(user: SessionUser = Depends(require_superuser), conn=Depends(get_readonly_db)):
    """Fetch global Autochat status and LLM provider settings (Superuser only)."""
    enabled, watermark, llm_cfg = await get_global_state(conn)
    if not llm_cfg.get("model"):
        llm_cfg = {**llm_cfg, "model": native_config_loader.get_default_model()}
    return GlobalStateResponse(
        global_enabled=enabled, config_watermark=watermark, llm_provider_config=llm_cfg
    )


@router.put("/global/state", response_model=GlobalStateResponse)
async def update_global(
    payload: GlobalStateUpdate,
    user: SessionUser = Depends(require_superuser),
):
    """Update global switch and LLM provider config, advancing watermark (Superuser only)."""
    engine = get_engine()
    async with engine.write_transaction() as conn:
        enabled, watermark = await update_global_state(
            conn, enabled=payload.global_enabled, llm_config=payload.llm_provider_config
        )
    return GlobalStateResponse(
        global_enabled=enabled, config_watermark=watermark, llm_provider_config=payload.llm_provider_config or {}
    )


@router.get("/groups/{gid}/config", response_model=GroupConfigDetail)
async def get_group_config(
    gid: str,
    request: Request,
    response: Response,
    user: SessionUser = Depends(require_group_access),
    conn=Depends(get_readonly_db),
):
    """Fetch complete group configuration with Weak ETag 304 negotiation."""
    config = await get_group_config_by_gid(conn, gid)
    if not config:
        # Dynamic defaults inherited from native autochat.yaml configuration
        exact_scope = await adapter.resolve_scope(conn, gid) or f"bot:{gid}"
        defaults = native_config_loader.get_default_group_config(gid)
        config = GroupConfigDetail(
            scope=exact_scope,
            **defaults,
        )

    etag = compute_group_config_etag(gid, config.updated_at)
    if is_etag_match(request, etag):
        return not_modified_response(etag)

    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "no-cache"
    return config


@router.put("/groups/{gid}/config", response_model=GroupConfigDetail)
async def update_group_config(
    gid: str,
    update: GroupConfigUpdate,
    response: Response,
    user: SessionUser = Depends(require_group_access),
):
    """Update group configuration, advancing the global config watermark."""
    engine = get_engine()
    async with engine.write_transaction() as conn:
        exact_scope = await adapter.resolve_scope(conn, gid) or f"bot:{gid}"
        updated = await upsert_group_config(conn, exact_scope, gid, update)

    etag = compute_group_config_etag(gid, updated.updated_at)
    response.headers["ETag"] = etag
    return updated


@router.post("/groups/{gid}/sandbox-eval", response_model=SandboxEvalResponse)
async def sandbox_eval(
    gid: str,
    payload: SandboxEvalRequest,
    user: SessionUser = Depends(require_group_access),
    conn=Depends(get_readonly_db),
):
    """Memory-only simulation of Autochat trigger probability and keywords.
    
    Strictly zero LLM calls and zero QQ messages sent.
    """
    config = await get_group_config_by_gid(conn, gid)
    defaults = native_config_loader.get_default_group_config(gid) if not config else None
    ambient_p = config.ambient_p if config else defaults["ambient_p"]
    followup_p = config.followup_p if config else defaults["followup_p"]
    keywords = config.trigger_keywords if config else defaults["trigger_keywords"]
    blacklist = config.blacklist_user_ids if config else []

    # 1. Blacklist check
    if payload.mock_user_id in blacklist:
        return SandboxEvalResponse(
            will_trigger=False,
            final_probability=0.0,
            base_probability=0.0,
            reason="blacklisted_user",
            matched_keywords={},
            hard_blocked=False,
            blacklisted=True,
            explanation=f"发送者 QQ {payload.mock_user_id} 在该群黑名单中，彻底静默阻断。",
        )

    # 2. Base probability
    direct = payload.is_at_bot
    followup = payload.is_followup
    if direct:
        base_probability = 1.0
        reason = "direct_at"
    elif followup:
        base_probability = followup_p
        reason = "followup"
    else:
        base_probability = ambient_p
        reason = "ambient"

    # 3. Match keywords
    matched_keywords = {}
    lower_text = payload.mock_text.lower()
    for kw, delta in keywords.items():
        if kw.lower() in lower_text:
            matched_keywords[kw] = delta

    keyword_delta = sum(matched_keywords.values())
    hard_blocked = any(delta <= -1.0 for delta in matched_keywords.values())

    if hard_blocked:
        final_probability = 0.0
    elif direct:
        final_probability = 1.0
    else:
        final_probability = max(0.0, min(1.0, base_probability + keyword_delta))

    # 4. Mock deterministic draw
    mock_seed = f"sandbox:{gid}:{payload.mock_text}"
    draw = int(hashlib.sha256(mock_seed.encode()).hexdigest()[:13], 16) / 16**13
    will_trigger = draw < final_probability

    explanation = (
        f"判定原因: {reason} | 基础概率: {base_probability:.2f} | 关键词增量: {keyword_delta:+.2f} | "
        f"最终唤醒概率: {final_probability:.2f} (测试随机采样值: {draw:.3f} -> "
        f"{'触发' if will_trigger else '静默'})"
    )

    return SandboxEvalResponse(
        will_trigger=will_trigger,
        final_probability=final_probability,
        base_probability=base_probability,
        reason=reason,
        matched_keywords=matched_keywords,
        hard_blocked=hard_blocked,
        blacklisted=False,
        explanation=explanation,
    )
