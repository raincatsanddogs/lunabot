"""Role-Based Access Control (RBAC) & FastAPI Dependency Guards."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional

from fastapi import Cookie, Depends, Header, HTTPException, Request, status

from ..config import settings
from .auth import decode_access_token, verify_csrf


@dataclass
class SessionUser:
    user_id: str
    role: str
    allowed_groups: List[str] = field(default_factory=list)
    allowed_scopes: List[str] = field(default_factory=list)

    @property
    def is_superuser(self) -> bool:
        return self.role == "superuser"

    def has_group_access(self, group_id: str) -> bool:
        if self.is_superuser:
            return True
        return "*" in self.allowed_groups or str(group_id) in self.allowed_groups

    def has_scope_access(self, scope: str) -> bool:
        if self.is_superuser:
            return True
        if "*" in self.allowed_scopes:
            return True
        if scope in self.allowed_scopes:
            return True
        # Scope fallback: extract group_id from bot_id:group_id
        if ":" in scope:
            gid = scope.split(":", 1)[1]
            return self.has_group_access(gid)
        return False


async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> SessionUser:
    """Dependency extracting and verifying the authenticated user session."""
    # 1. Enforce stateless CSRF check for state-mutating requests
    verify_csrf(request)

    # 2. Extract token from cookie first, then fallback to Bearer header
    token = request.cookies.get(settings.COOKIE_NAME)
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Cookie"},
        )

    payload = decode_access_token(token)
    if not payload or not payload.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid signature",
        )

    user_id = str(payload["sub"])
    role = str(payload.get("role", "group_admin"))
    groups = payload.get("groups", [])
    scopes = payload.get("scopes", [])

    return SessionUser(
        user_id=user_id,
        role=role,
        allowed_groups=[str(g) for g in groups],
        allowed_scopes=[str(s) for s in scopes],
    )


async def require_superuser(user: SessionUser = Depends(get_current_user)) -> SessionUser:
    """Guard requiring superuser role."""
    if not user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superuser privilege required for this operation",
        )
    return user


class GroupAccessGuard:
    """Dependency factory checking group-level permissions."""
    def __init__(self, group_param_name: str = "gid"):
        self.group_param_name = group_param_name

    async def __call__(
        self, request: Request, user: SessionUser = Depends(get_current_user)
    ) -> SessionUser:
        group_id = request.path_params.get(self.group_param_name) or request.query_params.get(self.group_param_name)
        if not group_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Missing group identifier"
            )

        if not user.has_group_access(group_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied to group {group_id}",
            )
        return user


# Common dependency
require_group_access = GroupAccessGuard("gid")
