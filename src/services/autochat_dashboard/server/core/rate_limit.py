"""In-memory Token Bucket Rate Limiter for Sensitive Endpoints."""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Dict, List

from fastapi import HTTPException, Request, status


class SlidingWindowRateLimiter:
    def __init__(self, limit: int = 5, window_seconds: int = 60):
        self.limit = limit
        self.window_seconds = window_seconds
        self.requests: Dict[str, List[float]] = defaultdict(list)

    def check(self, key: str) -> None:
        """Check rate limit for a client key (IP or user ID). Raises 429 if exceeded."""
        now = time.monotonic()
        history = self.requests[key]

        # Prune expired timestamps
        cutoff = now - self.window_seconds
        self.requests[key] = [t for t in history if t > cutoff]

        if len(self.requests[key]) >= self.limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many attempts. Rate limit exceeded ({self.limit} requests per {self.window_seconds}s).",
            )

        self.requests[key].append(now)


# Global limiter for ticket exchange: 5 requests per 60 seconds per client IP
ticket_exchange_limiter = SlidingWindowRateLimiter(limit=5, window_seconds=60)
