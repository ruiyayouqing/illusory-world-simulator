from __future__ import annotations
import asyncio
import json
import logging
from typing import Callable, Any

logger = logging.getLogger("chronoverse.streamer")


class NarrativeStreamer:
    def __init__(self):
        self.connections: dict[str, Any] = {}
        self._streaming: dict[str, bool] = {}

    async def connect(self, client_id: str, websocket):
        self.connections[client_id] = websocket

    def disconnect(self, client_id: str):
        self.connections.pop(client_id, None)

    async def send_to(self, client_id: str, message: dict):
        ws = self.connections.get(client_id)
        if ws:
            try:
                await ws.send_text(json.dumps(message, ensure_ascii=False))
            except Exception as e:
                logger.debug("WebSocket send failed for %s: %s", client_id, e)
                self.disconnect(client_id)

    async def broadcast(self, message: dict):
        dead = []
        for cid, ws in self.connections.items():
            try:
                await ws.send_text(json.dumps(message, ensure_ascii=False))
            except Exception as e:
                logger.debug("WebSocket broadcast failed for %s: %s", cid, e)
                dead.append(cid)
        for cid in dead:
            self.disconnect(cid)

    async def stream_narrative(self, client_id: str, text: str, chunk_size: int = 20):
        self._streaming[client_id] = True
        try:
            for i in range(0, len(text), chunk_size):
                if not self._streaming.get(client_id, False):
                    break
                chunk = text[i:i + chunk_size]
                await self.send_to(client_id, {
                    "type": "narrative_chunk",
                    "chunk": chunk,
                    "progress": min(100, int((i + chunk_size) / len(text) * 100)),
                })
                await asyncio.sleep(0.03)
            await self.send_to(client_id, {
                "type": "narrative_done",
                "full_text": text,
            })
        finally:
            self._streaming.pop(client_id, None)

    async def stream_event(self, client_id: str, event: dict):
        await self.send_to(client_id, {
            "type": "world_event",
            "event": event,
        })

    async def stream_options(self, client_id: str, options: list[dict]):
        await self.send_to(client_id, {
            "type": "options",
            "options": options,
        })

    async def stream_status(self, client_id: str, state: dict):
        await self.send_to(client_id, {
            "type": "status_update",
            "state": state,
        })

    async def stream_dice(self, client_id: str, result: dict):
        await self.send_to(client_id, {
            "type": "dice_result",
            "result": result,
        })

    async def stream_age_event(self, client_id: str, event: dict):
        await self.send_to(client_id, {
            "type": "age_event",
            "event": event,
        })

    async def stream_system(self, client_id: str, message: str):
        await self.send_to(client_id, {
            "type": "system_message",
            "message": message,
        })

    def stop_streaming(self, client_id: str):
        self._streaming[client_id] = False
