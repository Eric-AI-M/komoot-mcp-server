"""Tests for RateLimiter."""
import os
import time
from komoot_mcp.rate_limiter import RateLimiter

class TestRateLimiter:
    def test_default_rate(self):
        os.environ.pop("KOMOOT_RATE_LIMIT", None)
        rl = RateLimiter()
        assert rl.rate == 2.0

    def test_custom_rate(self):
        os.environ["KOMOOT_RATE_LIMIT"] = "5"
        rl = RateLimiter()
        assert rl.rate == 5.0

    def test_acquire_does_not_block_initially(self):
        rl = RateLimiter()
        start = time.monotonic()
        for _ in range(5):
            rl.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 2.0

    def test_rate_limit_enforced_under_sustained_load(self):
        os.environ["KOMOOT_RATE_LIMIT"] = "10"
        rl = RateLimiter()
        start = time.monotonic()
        for _ in range(20):
            rl.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 2.5
