"""aelvoxim.server.ratelimit — Simple in-memory rate limiter.

Pure stdlib, no external deps. Uses sliding window counters.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Dict, Tuple


class RateLimiter:
    """In-memory sliding window rate limiter.

    Thread-safe (lock-protected) and bounded (bucket map cannot grow
    unboundedly — C6, 9.txt audit).
    """

    # Hard cap on distinct keys; evict the oldest ~20% when exceeded so a
    # flood of unique keys cannot exhaust memory.
    _MAX_BUCKETS = 50000

    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self._max = max_requests
        self._window = window_seconds
        self._buckets: Dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, key: str) -> Tuple[bool, int]:
        """Check if key is rate-limited.

        Args:
            key: Identifier (API key suffix, email, IP).

        Returns:
            (allowed: bool, retry_after_seconds: int)
        """
        with self._lock:
            now = time.time()
            cutoff = now - self._window
            if len(self._buckets) > self._MAX_BUCKETS:
                _sorted = sorted(
                    self._buckets,
                    key=lambda k: (self._buckets[k][-1] if self._buckets[k] else 0),
                )
                for _k in _sorted[: self._MAX_BUCKETS // 5]:
                    del self._buckets[_k]
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
        with self._lock:
            self._buckets.pop(key, None)


# Default instance: 20 requests per 60 seconds for API Key auth
# Separate instance: 5 login attempts per 60 seconds (more restrictive)
api_limiter = RateLimiter(max_requests=20, window_seconds=60)
login_limiter = RateLimiter(max_requests=5, window_seconds=60)
