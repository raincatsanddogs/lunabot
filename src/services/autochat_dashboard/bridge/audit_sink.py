"""Asynchronous, Non-blocking Audit Logging Queue & Batch Writer.

Buffers decision and token telemetry into an in-memory queue, flushing in
batches of 20 or every 1.0s via a single SQLite transaction. Automatically
prunes logs older than 7 days every 6 hours.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("AutochatDashboard.AuditSink")


def sanitize_context_digest(text: str) -> str:
    """Transform CQ code images and raw image refs into pure-text badges [[IMG:description]].
    
    e.g. [CQ:image,file=xxx,summary=绘名无语凝噎] -> [[IMG:绘名无语凝噎]]
    """
    if not text:
        return ""
    # 1. Match CQ image with summary
    text = re.sub(
        r'\[CQ:image,[^\]]*summary=([^,\]]+)[^\]]*\]',
        r'[[IMG:\1]]',
        text,
    )
    # 2. Match generic CQ image without summary
    text = re.sub(r'\[CQ:image,[^\]]*\]', '[[IMG:图片]]', text)
    # 3. Match internal image references
    text = re.sub(r'\[图片:([^\]]+)\]', r'[[IMG:\1]]', text)
    # 4. Limit length
    return text[:500]


@dataclass
class AuditRecord:
    scope: str
    group_id: str
    trigger_user_id: str
    trigger_user_name: str
    decision: str
    probability: float
    draw: float
    details: Dict[str, Any]
    context_digest: str
    bot_reply: str
    model_used: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    created_at: int

    def to_db_tuple(self) -> tuple:
        return (
            self.scope,
            str(self.group_id),
            str(self.trigger_user_id),
            self.trigger_user_name or "",
            self.decision,
            float(self.probability),
            float(self.draw),
            json.dumps(self.details or {}, ensure_ascii=False),
            sanitize_context_digest(self.context_digest),
            self.bot_reply or "",
            self.model_used or "",
            int(self.prompt_tokens or 0),
            int(self.completion_tokens or 0),
            int(self.latency_ms or 0),
            int(self.created_at or time.time()),
        )


class AuditSink:
    def __init__(self, db_path: str | Path, max_queue_size: int = 1000):
        self.db_path = Path(db_path).resolve()
        self.queue: asyncio.Queue[AuditRecord] = asyncio.Queue(maxsize=max_queue_size)
        self._flush_task: Optional[asyncio.Task] = None
        self._clean_task: Optional[asyncio.Task] = None
        self._running = False
        self._dropped_count = 0

    def start(self) -> None:
        """Start background batch flushing and maintenance loops."""
        if self._running:
            return
        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())
        self._clean_task = asyncio.create_task(self._clean_loop())
        logger.info("Autochat dashboard audit sink background workers started.")

    async def stop(self) -> None:
        """Stop background tasks and flush remaining queue items."""
        self._running = False
        if self._flush_task:
            self._flush_task.cancel()
        if self._clean_task:
            self._clean_task.cancel()
        # Final flush
        await self._flush_now()

    def record_nowait(
        self,
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
        """Record an audit entry immediately without blocking main event loop."""
        record = AuditRecord(
            scope=scope,
            group_id=str(group_id),
            trigger_user_id=str(trigger_user_id),
            trigger_user_name=trigger_user_name,
            decision=decision,
            probability=probability,
            draw=draw,
            details=details or {},
            context_digest=sanitize_context_digest(context_digest),
            bot_reply=bot_reply,
            model_used=model_used,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            created_at=int(time.time()),
        )
        try:
            self.queue.put_nowait(record)
        except asyncio.QueueFull:
            # Drop the oldest item to make room, ensuring zero-latency protection
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(record)
            except Exception:
                pass
            self._dropped_count += 1
            if self._dropped_count % 50 == 1:
                logger.warning(
                    f"Audit queue full; dropped {self._dropped_count} records to protect bot latency."
                )

    async def _flush_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(1.0)
                await self._flush_now()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Error in audit flush loop: {exc}", exc_info=True)

    async def _flush_now(self) -> None:
        if self.queue.empty():
            return

        batch: List[AuditRecord] = []
        while not self.queue.empty() and len(batch) < 50:
            try:
                batch.append(self.queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        if not batch:
            return

        tuples = [r.to_db_tuple() for r in batch]
        # Run blocking sqlite insert in default threadpool executor
        await asyncio.to_thread(self._sync_insert_batch, tuples)

    def _sync_insert_batch(self, rows: List[tuple]) -> None:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        try:
            with conn:
                conn.executemany(
                    """
                    INSERT INTO dash_audit_logs (
                        scope, group_id, trigger_user_id, trigger_user_name, decision,
                        probability, draw, details, context_digest, bot_reply,
                        model_used, prompt_tokens, completion_tokens, latency_ms, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
        except Exception as exc:
            logger.error(f"Failed to batch write {len(rows)} audit records: {exc}")
        finally:
            conn.close()

    async def _clean_loop(self) -> None:
        """Runs every 6 hours to prune logs older than 7 days."""
        while self._running:
            try:
                await asyncio.sleep(6 * 3600)
                await asyncio.to_thread(self._sync_prune_expired)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(f"Error in audit clean loop: {exc}")

    def _sync_prune_expired(self) -> None:
        seven_days_ago = int(time.time()) - 7 * 86400
        now = int(time.time())
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        try:
            with conn:
                cur1 = conn.execute("DELETE FROM dash_audit_logs WHERE created_at < ?", (seven_days_ago,))
                cur2 = conn.execute("DELETE FROM dash_auth_tickets WHERE expires_at < ?", (now,))
                logger.info(
                    f"Maintenance clean complete: pruned {cur1.rowcount} old audit logs and {cur2.rowcount} expired tickets."
                )
        except Exception as exc:
            logger.warning(f"Failed to prune expired logs/tickets: {exc}")
        finally:
            conn.close()
