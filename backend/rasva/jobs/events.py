"""In-process fan-out of job updates to Server-Sent Event subscribers."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any


class EventBus:
    def __init__(self, max_queue: int = 500) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any] | None]] = set()
        self._max_queue = max_queue
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue[dict[str, Any] | None]:
        q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(self._max_queue)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any] | None]) -> None:
        self._subscribers.discard(q)

    def _publish(self, event: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer: disconnect it; the client reconnects and refetches.
                self._subscribers.discard(q)
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(None)

    def publish(self, event: dict[str, Any]) -> None:
        """Safe to call from the event loop or from worker threads."""
        loop = self._loop
        if loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._publish(event)
        elif not loop.is_closed():
            loop.call_soon_threadsafe(self._publish, event)
