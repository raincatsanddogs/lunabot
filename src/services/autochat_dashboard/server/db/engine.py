"""Database Engine & Connection Pool for Autochat Dashboard.

Supports SQLite WAL mode with concurrency hardening pragmas,
and provides async connection context managers with row factory.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Optional

import aiosqlite

DEFAULT_SQLITE_PATH = "data/chat/autochat/engine/autochat.sqlite"


class DatabaseEngine:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or os.getenv("AUTOCHAT_DB_PATH", DEFAULT_SQLITE_PATH)).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    async def _init_sqlite_pragmas(self, conn: aiosqlite.Connection) -> None:
        """Inject strict concurrency and performance pragmas to eliminate SQLITE_BUSY."""
        await conn.execute("PRAGMA journal_mode = WAL;")
        await conn.execute("PRAGMA synchronous = NORMAL;")
        await conn.execute("PRAGMA busy_timeout = 5000;")
        await conn.execute("PRAGMA temp_store = MEMORY;")
        await conn.execute("PRAGMA cache_size = -16000;")  # 16MB cache
        await conn.execute("PRAGMA foreign_keys = ON;")

    @asynccontextmanager
    async def connect(self, read_only: bool = False) -> AsyncGenerator[aiosqlite.Connection, None]:
        """Async connection context manager.
        
        Args:
            read_only: If True, opens SQLite using read-only URI mode to prevent any locking.
        """
        if not self.db_path.exists():
            # If not exists yet and read_only is requested, create parent and empty db first
            if not read_only:
                self.db_path.touch()

        if read_only:
            uri = f"file:{self.db_path.as_posix()}?mode=ro"
            conn = await aiosqlite.connect(uri, uri=True, timeout=5.0)
        else:
            conn = await aiosqlite.connect(self.db_path, timeout=10.0)

        conn.row_factory = aiosqlite.Row
        try:
            await self._init_sqlite_pragmas(conn)
            yield conn
        finally:
            await conn.close()

    @asynccontextmanager
    async def write_transaction(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        """Begin an IMMEDIATE transaction for write operations.
        
        Ensures strict atomicity and acquires the SQLite write lock up-front,
        preventing deadlocks or SQLITE_BUSY mid-transaction.
        """
        async with self.connect(read_only=False) as conn:
            await conn.execute("BEGIN IMMEDIATE;")
            try:
                yield conn
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise


# Global default engine instance
_engine: Optional[DatabaseEngine] = None


def get_engine(db_path: str | Path | None = None) -> DatabaseEngine:
    global _engine
    from ..config import settings
    target_path = Path(db_path or settings.DB_PATH).resolve()
    if _engine is None or _engine.db_path != target_path:
        _engine = DatabaseEngine(target_path)
    return _engine


async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """FastAPI dependency yielding a database connection."""
    async with get_engine().connect(read_only=False) as conn:
        yield conn


async def get_readonly_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """FastAPI dependency yielding a read-only database connection."""
    async with get_engine().connect(read_only=True) as conn:
        yield conn
