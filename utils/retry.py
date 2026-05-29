from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import TypeVar


T = TypeVar("T")


def async_retry(retries: int = 3, delay_seconds: float = 0.5):
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_exc = None
            for i in range(retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    if i < retries - 1:
                        await asyncio.sleep(delay_seconds)
            raise last_exc  # type: ignore[misc]

        return wrapper

    return decorator

