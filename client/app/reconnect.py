import random
import time


def backoff_seconds(attempt: int, base: float = 2.0, cap: float = 30.0) -> float:
    delay = min(cap, base * (2 ** min(attempt, 6)))
    jitter = random.uniform(0, 0.3 * delay)
    return delay + jitter


def backoff_sleep(attempt: int, base: float = 2.0, cap: float = 30.0) -> float:
    delay = backoff_seconds(attempt, base=base, cap=cap)
    time.sleep(delay)
    return delay
