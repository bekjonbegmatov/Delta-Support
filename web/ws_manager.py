import json
from typing import Set
from fastapi import WebSocket
from loguru import logger

class WSManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"WS connected: {id(websocket)} total={len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WS disconnected: {id(websocket)} total={len(self.active_connections)}")

    async def broadcast(self, event: str, data: dict):
        message = json.dumps({"event": event, "data": data}, ensure_ascii=False)
        for ws in list(self.active_connections):
            try:
                await ws.send_text(message)
            except Exception:
                self.disconnect(ws)

