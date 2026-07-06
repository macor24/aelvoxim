"""aelvoxim.server.ratelimit — Simple in-memory rate limiter.

Pure stdlib, no external deps. Uses sliding window counters.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Dict, Tuple


class RateLimiter:
    """In-memory sliding window rate limiter.

    Thread-safe for FastAPI's async context (no shared mutable state across
    requests in the same thread — each request is independent).
    """

    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self._max = max_requests
        self._window = window_seconds
        self._buckets: Dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> Tuple[bool, int]:
        """Check if key is rate-limited.

        Args:
            key: Identifier (API key suffix, email, IP).

        Returns:
            (allowed: bool, retry_after_seconds: int)
        """
        now = time.time()
        cutoff = now - self._window
        bucket = self._buckets[key]
        # Prune old entries
        self._buckets[key] = [t for t in bucket if t > cutoff]
        bucket = self._buckets[key]

        if len(bucket) >= self._max:
            oldest = bucket[0]
            retry_after = int(self._window - (now - oldest))
            return False, max(retry_after, 1)

        bucket.append(now)
        return True, 0

    def reset(self, key: str) -> None:
        """Reset rate limit for a key."""
        self._buckets.pop(key, None)


# Default instance: 20 requests per 60 seconds for API Key auth
# Separate instance: 5 login attempts per 60 seconds (more restrictive)
api_limiter = RateLimiter(max_requests=20, window_seconds=60)
login_limiter = RateLimiter(max_requests=5, window_seconds=60)
