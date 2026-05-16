import os
import time
import threading

class RateLimiter:
    def __init__(self):
        self.rate = float(os.environ.get("KOMOOT_RATE_LIMIT", "2"))
        self.interval = 1.0 / self.rate
        self.tokens = self.rate
        self.last_refill = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self):
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
            self.last_refill = now

            if self.tokens < 1.0:
                sleep_time = (1.0 - self.tokens) / self.rate
                time.sleep(sleep_time)
                self.tokens = 0.0
            else:
                self.tokens -= 1.0
