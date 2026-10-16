"""One-Time Ticket (OTT) Issuer for Chat-to-Web Authentication.

Generates cryptographically secure, single-use authentication tickets
and persists only their SHA-256 hash in SQLite with 180s TTL.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
import time
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("AutochatDashboard.TicketIssuer")

TICKET_TTL_SECONDS = 180  # 3 minutes


class TicketIssuer:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).resolve()

    def issue_ticket(
        self,
        user_id: str,
        role: str,
        allowed_groups: List[str],
        allowed_scopes: Optional[List[str]] = None,
        base_url: Optional[str] = None,
    ) -> str:
        """Generate a single-use OTT and store its SHA-256 hash in SQLite.
        
        Args:
            user_id: Requester's QQ user ID string
            role: 'superuser' or 'group_admin'
            allowed_groups: list of authorized group ID strings (e.g. ["123", "456"], or ["*"] for superuser)
            allowed_scopes: list of authorized scopes (e.g. ["bot:123"])
            base_url: Base HTTP(S) address of the dashboard frontend (None to resolve from config)
            
        Returns:
            The complete URL containing the token in the URL hash fragment (#ticket=...),
            ensuring it never leaks via server access logs or HTTP Referer headers.
        """
        if not base_url:
            try:
                from ..server.config import settings
                base_url = (settings.PUBLIC_URL or f"http://{settings.HOST if settings.HOST != '0.0.0.0' else '127.0.0.1'}:{settings.PORT}").rstrip("/")
            except Exception:
                base_url = "http://127.0.0.1:18088"

        raw_ticket = secrets.token_hex(32)
        ticket_hash = hashlib.sha256(raw_ticket.encode("utf-8")).hexdigest()

        now = int(time.time())
        expires_at = now + TICKET_TTL_SECONDS

        allowed_scopes_json = json.dumps(allowed_scopes or (["*"] if role == "superuser" else []), ensure_ascii=False)
        allowed_groups_json = json.dumps(allowed_groups, ensure_ascii=False)

        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO dash_auth_tickets (
                        ticket_hash, user_id, role, allowed_groups, allowed_scopes, created_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ticket_hash,
                        str(user_id),
                        role,
                        allowed_groups_json,
                        allowed_scopes_json,
                        now,
                        expires_at,
                    ),
                )
            logger.info(f"Issued dashboard ticket for user {user_id} ({role}), expires in {TICKET_TTL_SECONDS}s")
        finally:
            conn.close()

        # Format URL with hash fragment
        clean_base = base_url.rstrip("/")
        return f"{clean_base}/#ticket={raw_ticket}"

    def clean_expired_tickets(self) -> int:
        """Purge expired tickets from the database."""
        now = int(time.time())
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        try:
            with conn:
                cursor = conn.execute("DELETE FROM dash_auth_tickets WHERE expires_at < ?", (now,))
                return cursor.rowcount
        except Exception as exc:
            logger.warning(f"Error purging expired tickets: {exc}")
            return 0
        finally:
            conn.close()
