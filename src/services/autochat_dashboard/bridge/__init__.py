"""Public Bridge Interface for LunaBot Embedding.

Provides a unified facade for bootstrap, watermark config caching,
non-blocking audit telemetry recording, and OTT ticket issuance.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .audit_sink import AuditSink
from .config_sync import ConfigSync, GroupConfig
from .schema_bootstrap import bootstrap_database
from .ticket_issuer import TicketIssuer

logger = logging.getLogger("AutochatDashboard.Bridge")

DEFAULT_DB_PATH = "data/chat/autochat/engine/autochat.sqlite"


class AutoChatBridge:
    _instance: Optional[AutoChatBridge] = None

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path).resolve()
        self.config_sync = ConfigSync(self.db_path)
        self.ticket_issuer = TicketIssuer(self.db_path)
        self.audit_sink = AuditSink(self.db_path)

    @classmethod
    def get(cls, db_path: str | Path | None = None) -> AutoChatBridge:
        if db_path is None:
            if cls._instance is not None:
                return cls._instance
            db_path = DEFAULT_DB_PATH
        resolved = Path(db_path).resolve()
        if cls._instance is None or cls._instance.db_path != resolved:
            cls._instance = cls(resolved)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    @classmethod
    def bootstrap(cls, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        """Initialize database tables and WAL pragmas."""
        bootstrap_database(db_path)
        # Ensure singleton initialized
        cls.get(db_path)

    @classmethod
    def start_background(cls) -> None:
        """Start async workers (audit queue flush & prune loops)."""
        cls.get().audit_sink.start()

    @classmethod
    async def stop_background(cls) -> None:
        """Stop async workers gracefully."""
        if cls._instance:
            await cls._instance.audit_sink.stop()

    @classmethod
    def is_global_enabled(cls) -> bool:
        """Check if global Autochat switch is on."""
        return cls.get().config_sync.is_global_enabled()

    @classmethod
    def get_group_config(cls, group_id: str, bot_id: Optional[str] = None) -> Optional[GroupConfig]:
        """Fetch group config with 2s watermark throttle."""
        return cls.get().config_sync.get_group_config(group_id, bot_id)

    @classmethod
    def record_audit_nowait(
        cls,
        scope: str,
        group_id: str,
        trigger_user_id: str,
        trigger_user_name: str = "",
        decision: str = "replied",
        probability: float = 1.0,
        draw: float = 0.0,
        details: Optional[Dict[str, Any]] = None,
        context_digest: str = "",
        bot_reply: str = "",
        model_used: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: int = 0,
    ) -> None:
        """Record audit telemetry non-blockingly."""
        cls.get().audit_sink.record_nowait(
            scope=scope,
            group_id=group_id,
            trigger_user_id=trigger_user_id,
            trigger_user_name=trigger_user_name,
            decision=decision,
            probability=probability,
            draw=draw,
            details=details,
            context_digest=context_digest,
            bot_reply=bot_reply,
            model_used=model_used,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )

    @classmethod
    def issue_ticket(
        cls,
        user_id: str,
        role: str,
        allowed_groups: List[str],
        allowed_scopes: Optional[List[str]] = None,
        base_url: str = "http://127.0.0.1:18088",
    ) -> str:
        """Issue an OTT ticket link with URL hash fragment."""
        return cls.get().ticket_issuer.issue_ticket(
            user_id=user_id,
            role=role,
            allowed_groups=allowed_groups,
            allowed_scopes=allowed_scopes,
            base_url=base_url,
        )


__all__ = ["AutoChatBridge", "GroupConfig"]
