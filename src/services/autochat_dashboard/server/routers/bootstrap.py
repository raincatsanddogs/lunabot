"""Bootstrap Aggregation Route for Instantaneous SPA Initial Render."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from ..core.etag import compute_bootstrap_etag, is_etag_match, not_modified_response
from ..core.rbac import SessionUser, get_current_user
from ..db.engine import get_readonly_db
from ..db.repositories.config_repo import get_global_state, list_group_summaries
from ..db.repositories.stats_repo import get_overview_stats
from ..schemas.auth import SessionUserResponse
from ..schemas.telemetry import BootstrapResponse

router = APIRouter(prefix="/api/v1", tags=["Bootstrap"])


@router.get("/bootstrap", response_model=BootstrapResponse)
async def get_bootstrap(
    request: Request,
    response: Response,
    user: SessionUser = Depends(get_current_user),
    conn=Depends(get_readonly_db),
):
    """Aggregate initial user, global switch, token stats, and group summaries with 304 ETag support."""
    # 1. Fetch lightweight overview data
    global_enabled, watermark, _ = await get_global_state(conn)
    groups = await list_group_summaries(conn, user.allowed_groups)
    stats = await get_overview_stats(conn, user.allowed_groups)

    # 2. Compute Weak ETag
    etag = compute_bootstrap_etag(
        global_enabled=global_enabled,
        watermark=watermark,
        user_id=user.user_id,
        groups_count=len(groups),
        today_tokens=stats.today_total_tokens,
    )

    # 3. Check for 304 Not Modified
    if is_etag_match(request, etag):
        return not_modified_response(etag)

    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "no-cache"

    return BootstrapResponse(
        user=SessionUserResponse(
            user_id=user.user_id,
            role=user.role,
            allowed_groups=user.allowed_groups,
            allowed_scopes=user.allowed_scopes,
        ),
        global_enabled=global_enabled,
        config_watermark=watermark,
        stats=stats,
        groups=groups,
    )
