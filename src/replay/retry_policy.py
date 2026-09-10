"""Bounded retry helpers for transient conditions (slow loads, brief races) - deliberately
small and capped: replay must never retry indefinitely, per the brief's "avoid blindly proceeding.\""""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")

MAX_TRANSIENT_RETRIES = 3
TRANSIENT_RETRY_DELAY_SECONDS = 0.75
MAX_RECOVERY_ATTEMPTS_PER_STEP = 2


async def retry_async(fn: Callable[[], Awaitable[T]], max_attempts: int = MAX_TRANSIENT_RETRIES, delay_seconds: float = TRANSIENT_RETRY_DELAY_SECONDS) -> T:
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return await fn()
        except Exception as e:  # noqa: BLE001 - re-raised after exhausting attempts
            last_exc = e
            if attempt < max_attempts - 1:
                await asyncio.sleep(delay_seconds)
    assert last_exc is not None
    raise last_exc
