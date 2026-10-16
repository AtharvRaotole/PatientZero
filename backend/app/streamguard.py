"""Concurrency guards for long-lived work: SSE streams and crew runs.

Slots caps how many are open at once, globally and per client key (IP). GuardedStream is a StreamingResponse that
always gives its slot back when the response ends for any reason (finished, client vanished, cancelled, or the
body never started), so a burst of abrupt disconnects can never leak slots.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from typing import Callable

from fastapi.responses import StreamingResponse


class Slots:
    def __init__(self, name: str, total: int, per_key: int):
        self.name, self.total, self.per_key = name, total, per_key
        self._open = 0
        self._by_key: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def acquire(self, key: str) -> str | None:
        """Take a slot. Returns None on success, else the reason ('global' or 'per_client')."""
        with self._lock:
            if self._open >= self.total:
                return "global"
            if self._by_key[key] >= self.per_key:
                return "per_client"
            self._open += 1
            self._by_key[key] += 1
            return None

    def release(self, key: str) -> None:
        with self._lock:
            if self._by_key.get(key, 0) > 0:
                self._by_key[key] -= 1
                self._open -= 1
                if not self._by_key[key]:
                    del self._by_key[key]

    def status(self) -> dict:
        with self._lock:
            return {"open": self._open, "max": self.total, "max_per_client": self.per_key, "clients": len(self._by_key)}


class GuardedStream(StreamingResponse):
    """StreamingResponse that runs `on_close` exactly once when the ASGI call returns or raises."""

    def __init__(self, content, on_close: Callable[[], None], **kw):
        super().__init__(content, **kw)
        self._on_close = on_close

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            cb, self._on_close = self._on_close, None
            if cb:
                cb()
