"""In-memory Config Cache with 2-second Throttled Watermark Synchronization.

Maintains hot-reloaded per-group Autochat settings in memory with near-zero
database overhead when configurations remain unchanged.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("AutochatDashboard.ConfigSync")


@dataclass
class GroupConfig:
    scope: str
    group_id: str
    group_name_cache: str = ""
    enabled: bool = True
    ambient_p: float = 0.04
    followup_p: float = 0.85
    cooldown_sec: int = 25
    trigger_keywords: Dict[str, float] = field(default_factory=dict)
    persona: str = ""
    blacklist_user_ids: List[str] = field(default_factory=list)
    updated_at: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> GroupConfig:
        keywords = {}
        blacklist = []
        try:
            keywords = json.loads(row["trigger_keywords"])
            if not isinstance(keywords, dict):
                keywords = {}
        except Exception:
            pass

        try:
            blacklist = json.loads(row["blacklist_user_ids"])
            if not isinstance(blacklist, list):
                blacklist = []
        except Exception:
            pass

        return cls(
            scope=row["scope"],
            group_id=str(row["group_id"]),
            group_name_cache=row["group_name_cache"] or "",
            enabled=bool(row["enabled"]),
            ambient_p=float(row["ambient_p"]),
            followup_p=float(row["followup_p"]),
            cooldown_sec=int(row["cooldown_sec"]),
            trigger_keywords={str(k): float(v) for k, v in keywords.items()},
            persona=row["persona"] or "",
            blacklist_user_ids=[str(uid) for uid in blacklist],
            updated_at=int(row["updated_at"]),
        )


class ConfigSync:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).resolve()
        # In-memory caches indexed by scope and by group_id
        self._scope_cache: Dict[str, GroupConfig] = {}
        self._group_cache: Dict[str, GroupConfig] = {}
        self._last_sync_watermark: int = 0
        self._last_check_ts: float = 0.0
        self._global_enabled: bool = True
        self._check_interval: float = 2.0  # Throttle to max once per 2 seconds

    def is_global_enabled(self) -> bool:
        self.sync_if_needed()
        return self._global_enabled

    def get_group_config(self, group_id: str, bot_id: Optional[str] = None) -> Optional[GroupConfig]:
        """Get group configuration with automatic throttled watermark check.
        
        Args:
            group_id: QQ Group ID
            bot_id: Optional bot ID. If provided, searches exact scope first.
        """
        self.sync_if_needed()

        if bot_id:
            exact_scope = f"{bot_id}:{group_id}"
            if exact_scope in self._scope_cache:
                return self._scope_cache[exact_scope]

        return self._group_cache.get(str(group_id))

    def sync_if_needed(self, force: bool = False) -> None:
        """Poll the watermark if 2 seconds have elapsed since last check."""
        now = time.monotonic()
        if not force and (now - self._last_check_ts < self._check_interval):
            return

        self._last_check_ts = now
        try:
            self._do_sync()
        except Exception as exc:
            logger.warning(f"Error during config watermark sync: {exc}")

    def _do_sync(self) -> None:
        conn = sqlite3.connect(str(self.db_path), timeout=3.0)
        conn.row_factory = sqlite3.Row
        try:
            # 1. Fetch current global enabled switch and watermark
            cursor = conn.execute(
                "SELECT key, value_int FROM dash_global_kv WHERE key IN ('global_autochat_enabled', 'config_watermark')"
            )
            rows = cursor.fetchall()
            watermark = 0
            for r in rows:
                if r["key"] == "global_autochat_enabled":
                    self._global_enabled = bool(r["value_int"])
                elif r["key"] == "config_watermark":
                    watermark = int(r["value_int"])

            # 2. Check if watermark advanced
            if watermark > self._last_sync_watermark:
                logger.info(
                    f"Autochat config watermark changed: {self._last_sync_watermark} -> {watermark}. Reloading configs..."
                )
                cfg_cursor = conn.execute("SELECT * FROM dash_group_config")
                cfg_rows = cfg_cursor.fetchall()

                new_scope_cache = {}
                new_group_cache = {}
                for row in cfg_rows:
                    cfg = GroupConfig.from_row(row)
                    new_scope_cache[cfg.scope] = cfg
                    new_group_cache[cfg.group_id] = cfg

                self._scope_cache = new_scope_cache
                self._group_cache = new_group_cache
                self._last_sync_watermark = watermark
                logger.info(f"Loaded {len(new_scope_cache)} group configurations into memory cache.")
        finally:
            conn.close()
