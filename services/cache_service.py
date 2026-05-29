import time
from dataclasses import dataclass
from typing import Any


@dataclass
class CacheValue:
    value: Any
    expire_at: float


class CacheService:
    def __init__(self) -> None:
        self._cache: dict[str, CacheValue] = {}

    def set(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        self._cache[key] = CacheValue(value=value, expire_at=time.time() + ttl_seconds)

    def get(self, key: str) -> Any | None:
        data = self._cache.get(key)
        if not data:
            return None
        if data.expire_at < time.time():
            self._cache.pop(key, None)
            return None
        return data.value

