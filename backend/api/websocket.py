from collections import defaultdict
from typing import DefaultDict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


class ConnectionManager:
    def __init__(self):
        self.active_connections: DefaultDict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, websocket: WebSocket, incident_id: str):
        await websocket.accept()
        self.active_connections[incident_id].add(websocket)

    def disconnect(self, websocket: WebSocket, incident_id: str):
        connections = self.active_connections.get(incident_id)
        if not connections:
            return
        connections.discard(websocket)
        if not connections:
            self.active_connections.pop(incident_id, None)

    async def broadcast(self, incident_id: str, message: dict):
        connections = list(self.active_connections.get(incident_id, set()))
        stale = []
        for connection in connections:
            try:
                await connection.send_json(message)
            except Exception:
                stale.append(connection)
        for connection in stale:
            self.disconnect(connection, incident_id)


manager = ConnectionManager()


@router.websocket("/ws/incidents/{incident_id}/progress")
async def websocket_endpoint(websocket: WebSocket, incident_id: str):
    await manager.connect(websocket, incident_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, incident_id)
    except Exception:
        manager.disconnect(websocket, incident_id)
