"""In-process event bus for WebSocket fanout (Redis later)."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class EventHub:
    def __init__(self) -> None:
        self._subs: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, channel: str, ws: WebSocket) -> None:
        async with self._lock:
            self._subs[channel].add(ws)

    async def unsubscribe(self, channel: str, ws: WebSocket) -> None:
        async with self._lock:
            self._subs[channel].discard(ws)

    async def publish(self, channel: str, event: dict[str, Any]) -> None:
        payload = json.dumps(event)
        async with self._lock:
            targets = list(self._subs.get(channel, set()))
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            await self.unsubscribe(channel, ws)


hub = EventHub()
