from __future__ import annotations

from collections import defaultdict
from typing import Awaitable, Callable

from models.events import AgentEvent, EventType


EventHandler = Callable[[AgentEvent], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[EventType, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        self._subscribers[event_type].append(handler)

    async def publish(self, event: AgentEvent) -> None:
        for handler in self._subscribers.get(event.event_type, []):
            await handler(event)

