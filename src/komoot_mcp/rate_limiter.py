import asyncio
import os
import time


class RateLimiter:
    """Token-bucket rate limiter — async-safe.

    ``acquire`` is a coroutine; callers MUST ``await`` it. Sleeping
    with ``time.sleep`` would block the event loop and starve other
    in-flight tenant requests, so this implementation uses
    ``asyncio.sleep`` and an ``asyncio.Lock`` for mutual exclusion.
    """

    def __init__(self):
        self.rate = float(os.environ.get("KOMOOT_RATE_LIMIT", "2"))
        self.interval = 1.0 / self.rate
        self.tokens = self.rate
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
            self.last_refill = now

            if self.tokens < 1.0:
                sleep_time = (1.0 - self.tokens) / self.rate
                await asyncio.sleep(sleep_time)
                # After sleeping we have re-accumulated 1 token; spend it.
                self.tokens = 0.0
            else:
                self.tokens -= 1.0
