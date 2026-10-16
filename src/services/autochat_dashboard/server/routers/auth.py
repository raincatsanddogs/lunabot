"""Authentication Routes: OTT Exchange, Logout & Session Identity."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from ..core.auth import clear_auth_cookie, create_access_token, set_auth_cookie
from ..core.rate_limit import ticket_exchange_limiter
from ..core.rbac import SessionUser, get_current_user
from ..db.engine import get_engine
from ..db.repositories.auth_repo import consume_ticket
from ..schemas.auth import ExchangeTicketRequest, SessionUserResponse

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


@router.post("/exchange", response_model=SessionUserResponse)
async def exchange_ticket(
    request: Request, response: Response, payload: ExchangeTicketRequest
):
    """Exchange single-use OTT ticket for an authenticated JWT session cookie."""
    # 1. Rate limit by client IP (5 requests / 60 seconds)
    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    ticket_exchange_limiter.check(client_ip)

    # 2. Atomically consume ticket inside an IMMEDIATE write transaction
    engine = get_engine()
    async with engine.write_transaction() as conn:
        user_data = await consume_ticket(conn, payload.ticket.strip())

    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ticket is invalid, already consumed, or expired.",
        )

    # 3. Issue signed JWT token
    token = create_access_token(
        {
            "sub": user_data["user_id"],
            "role": user_data["role"],
            "groups": user_data["allowed_groups"],
            "scopes": user_data["allowed_scopes"],
        }
    )

    # 4. Write HttpOnly SameSite=Lax cookie
    set_auth_cookie(response, token, request=request)

    return SessionUserResponse(
        user_id=user_data["user_id"],
        role=user_data["role"],
        allowed_groups=user_data["allowed_groups"],
        allowed_scopes=user_data["allowed_scopes"],
        token=token,
    )


@router.post("/logout")
async def logout(response: Response, user: SessionUser = Depends(get_current_user)):
    """Clear session cookie and invalidate local login."""
    clear_auth_cookie(response)
    return {"ok": True}


@router.get("/me", response_model=SessionUserResponse)
async def get_me(user: SessionUser = Depends(get_current_user)):
    """Return currently authenticated user and permission scopes."""
    return SessionUserResponse(
        user_id=user.user_id,
        role=user.role,
        allowed_groups=user.allowed_groups,
        allowed_scopes=user.allowed_scopes,
    )
