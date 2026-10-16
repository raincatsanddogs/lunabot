"""Authentication Core: JWT Issuance, Verification, and CSRF Protection."""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

import jwt
from fastapi import Cookie, HTTPException, Request, Response, status

from ..config import settings


def create_access_token(data: Dict[str, Any], expires_seconds: int = settings.JWT_EXPIRE_SECONDS) -> str:
    """Issue a signed JWT access token."""
    to_encode = data.copy()
    now = int(time.time())
    to_encode.update({"iat": now, "exp": now + expires_seconds})
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode and verify a signed JWT token."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except (jwt.PyJWTError, Exception):
        return None


def verify_csrf(request: Request) -> None:
    """Stateless CSRF Protection.
    
    Mutating methods (POST, PUT, PATCH, DELETE) must supply the custom header
    X-Requested-With: LunaDashClient, which cannot be forged via cross-origin HTML forms.
    """
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        # Allow ticket exchange endpoint to be called initially
        if request.url.path.endswith("/api/v1/auth/exchange"):
            pass
        header_val = request.headers.get("X-Requested-With")
        if header_val != "LunaDashClient":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF header check failed: missing or invalid X-Requested-With header",
            )


def set_auth_cookie(response: Response, token: str, request: Optional[Request] = None) -> None:
    """Set secure HttpOnly authentication cookie."""
    secure = settings.COOKIE_SECURE
    if request is not None:
        is_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        # If secure is required globally, only force it when actually served over https or not on localhost
        secure = settings.COOKIE_SECURE and is_https

    response.set_cookie(
        key=settings.COOKIE_NAME,
        value=token,
        max_age=settings.JWT_EXPIRE_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    """Clear authentication cookie."""
    response.delete_cookie(
        key=settings.COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
    )
