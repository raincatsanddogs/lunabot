"""Authentication Repository: Single-Use Ticket Atomic Exchange."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, Optional

import aiosqlite


async def consume_ticket(conn: aiosqlite.Connection, raw_ticket: str) -> Optional[Dict[str, Any]]:
    """Atomically consume an authentication ticket inside an IMMEDIATE transaction.
    
    Guarantees strict single-use semantics by querying and deleting in the same write lock.
    """
    if not raw_ticket or len(raw_ticket) != 64:
        return None

    ticket_hash = hashlib.sha256(raw_ticket.encode("utf-8")).hexdigest()
    now = int(time.time())

    # Try atomic delete with RETURNING (supported in SQLite 3.35+)
    try:
        async with conn.execute(
            """
            DELETE FROM dash_auth_tickets
            WHERE ticket_hash = ? AND expires_at > ?
            RETURNING user_id, role, allowed_groups, allowed_scopes
            """,
            (ticket_hash, now),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return _parse_ticket_row(row)
    except Exception:
        # Fallback for older SQLite versions: select then delete inside same transaction
        async with conn.execute(
            """
            SELECT user_id, role, allowed_groups, allowed_scopes
            FROM dash_auth_tickets
            WHERE ticket_hash = ? AND expires_at > ?
            """,
            (ticket_hash, now),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None

        await conn.execute("DELETE FROM dash_auth_tickets WHERE ticket_hash = ?", (ticket_hash,))
        return _parse_ticket_row(row)

    return None


def _parse_ticket_row(row: aiosqlite.Row) -> Dict[str, Any]:
    groups = []
    scopes = []
    try:
        groups = json.loads(row["allowed_groups"])
    except Exception:
        pass
    try:
        scopes = json.loads(row["allowed_scopes"])
    except Exception:
        pass

    return {
        "user_id": str(row["user_id"]),
        "role": str(row["role"]),
        "allowed_groups": [str(g) for g in groups],
        "allowed_scopes": [str(s) for s in scopes],
    }
