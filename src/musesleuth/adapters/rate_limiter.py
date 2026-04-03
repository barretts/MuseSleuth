"""Per-adapter rate limiter using a simple token bucket."""
from __future__ import annotations

import time


class RateLimiter:
    """Simple rate limiter that enforces max N requests per time period.

    Accepts either ``requests_per_second`` (legacy) or ``max_calls`` / ``period``
    which is more readable for sub-1-rps limits.
    """

    def __init__(
        self,
        requests_per_second: float | None = None,
        *,
        max_calls: int | None = None,
        period: float = 1.0,
    ) -> None:
        if max_calls is not None:
            rps = max_calls / period if period > 0 else 0.0
        elif requests_per_second is not None:
            rps = requests_per_second
        else:
            rps = 1.0

        self.rps = rps
        self.min_interval = 1.0 / rps if rps > 0 else 0.0
        self._last_request: float = 0.0
        self.request_count: int = 0

    def acquire(self) -> bool:
        """Record a request (non-blocking)."""
        self._last_request = time.monotonic()
        self.request_count += 1
        return True

    def wait_time(self) -> float:
        """Seconds to wait before the next request is allowed."""
        if self._last_request == 0.0:
            return 0.0
        elapsed = time.monotonic() - self._last_request
        return max(0.0, self.min_interval - elapsed)

    def wait_and_acquire(self) -> bool:
        """Sleep if needed, then acquire."""
        wait = self.wait_time()
        if wait > 0:
            time.sleep(wait)
        return self.acquire()

    # Convenience alias used by adapters
    wait = wait_and_acquire

    def reset(self) -> None:
        """Reset the rate limiter state."""
        self._last_request = 0.0
        self.request_count = 0