"""Deterministic Weak ETag Generation & HTTP 304 Negotiation Helpers."""
from __future__ import annotations

from typing import Optional

from fastapi import Request, Response


def compute_group_config_etag(group_id: str, updated_at: int) -> str:
    """Generate weak ETag for single group configuration."""
    return f'W/"cfg-{group_id}-{updated_at}"'


def compute_bootstrap_etag(
    global_enabled: bool, watermark: int, user_id: str, groups_count: int, today_tokens: int = 0
) -> str:
    """Generate weak ETag for bootstrap overview payload."""
    return f'W/"boot-{int(global_enabled)}-{watermark}-{user_id}-{groups_count}-{today_tokens}"'


def is_etag_match(request: Request, current_etag: str) -> bool:
    """Check If-None-Match against current weak ETag."""
    if_none_match = request.headers.get("if-none-match")
    if not if_none_match:
        return False
    return if_none_match.strip() == current_etag.strip()


def not_modified_response(etag: str) -> Response:
    """Return an empty HTTP 304 Not Modified response with ETag and Cache-Control headers."""
    return Response(
        status_code=304,
        headers={
            "ETag": etag,
            "Cache-Control": "no-cache",
        },
    )
