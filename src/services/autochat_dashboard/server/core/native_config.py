"""Native Autochat Configuration Loader and Dynamic Defaults Provider.

Reads `config/chat/autochat.yaml` (fallback `example_config/chat/autochat.yaml`)
to provide dynamic group defaults (ambient_p, followup_p, trigger_keywords,
persona, and models) rather than using static hardcoded magic numbers.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from src.services.autochat.config import ConfigFile
from src.services.autochat.types import Settings

logger = logging.getLogger("AutochatDashboard.NativeConfig")


class NativeConfigLoader:
    _instance: Optional[NativeConfigLoader] = None

    def __init__(self, config_paths: Optional[list[Path]] = None):
        self.config_paths = config_paths or [
            Path("config/chat/autochat.yaml"),
            Path("example_config/chat/autochat.yaml"),
        ]
        self._cached_settings: Optional[Settings] = None
        self._last_loaded_path: Optional[Path] = None
        self._last_check_time: float = 0.0
        self._check_interval: float = 2.0  # Check for file updates at most once every 2 seconds
        self._file_mtimes: Dict[str, float] = {}
        self._active_config_file: Optional[ConfigFile] = None

    @classmethod
    def get_instance(cls) -> NativeConfigLoader:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _resolve_active_path(self) -> Optional[Path]:
        for p in self.config_paths:
            if p.exists():
                return p
        return None

    def get_settings(self, force: bool = False) -> Settings:
        now = time.monotonic()
        active_path = self._resolve_active_path()

        if not active_path:
            if self._cached_settings is None:
                self._cached_settings = Settings()
            return self._cached_settings

        if (
            not force
            and self._cached_settings is not None
            and (now - self._last_check_time < self._check_interval)
            and active_path == self._last_loaded_path
        ):
            return self._cached_settings

        self._last_check_time = now
        try:
            mtime = os.path.getmtime(active_path)
            cached_mtime = self._file_mtimes.get(str(active_path))

            if force or self._cached_settings is None or cached_mtime != mtime or self._last_loaded_path != active_path:
                if self._active_config_file is None or self._last_loaded_path != active_path:
                    self._active_config_file = ConfigFile(str(active_path))
                
                parsed = self._active_config_file.read()
                if parsed is not None:
                    settings, _rpc, _level = parsed
                    self._cached_settings = settings
                    self._last_loaded_path = active_path
                    self._file_mtimes[str(active_path)] = mtime
                    logger.debug(f"Loaded native autochat settings from {active_path}")
        except Exception as exc:
            logger.warning(f"Error loading native autochat settings from {active_path}: {exc}")
            if self._cached_settings is None:
                self._cached_settings = Settings()

        return self._cached_settings or Settings()

    def get_group_persona(self, group_id: str) -> str:
        """Fetch persona for a group from native configuration.
        
        Prioritizes per-group persona from `chat.prompt.persona[group_id]`,
        falling back to `chat.prompt.persona.default`.
        """
        settings = self.get_settings()
        gid = str(group_id)
        if hasattr(settings, "personas") and gid in settings.personas:
            return settings.personas[gid].strip()
        if hasattr(settings, "persona") and settings.persona:
            return settings.persona.strip()
        return ""

    def get_default_ambient_p(self) -> float:
        settings = self.get_settings()
        return float(getattr(settings, "ambient_p", 0.04))

    def get_default_followup_p(self) -> float:
        settings = self.get_settings()
        return float(getattr(settings, "followup_p", 0.85))

    def get_default_cooldown_sec(self) -> int:
        settings = self.get_settings()
        # Fall back to stickers cooldown or 0
        return int(getattr(settings, "sticker_cooldown", 0))

    def get_default_keywords(self) -> Dict[str, float]:
        settings = self.get_settings()
        return dict(getattr(settings, "trigger_keywords", {}))

    def get_default_model(self) -> str:
        settings = self.get_settings()
        return str(getattr(settings, "model", "gg:gemini-3.8-flash"))

    def get_default_group_config(self, group_id: str) -> Dict[str, Any]:
        """Generate a complete default configuration dictionary for a given group."""
        return {
            "group_id": str(group_id),
            "group_name_cache": f"群 {group_id}",
            "enabled": True,
            "ambient_p": self.get_default_ambient_p(),
            "followup_p": self.get_default_followup_p(),
            "cooldown_sec": self.get_default_cooldown_sec(),
            "trigger_keywords": self.get_default_keywords(),
            "persona": self.get_group_persona(group_id),
            "blacklist_user_ids": [],
            "updated_at": 0,
        }


native_config_loader = NativeConfigLoader.get_instance()
