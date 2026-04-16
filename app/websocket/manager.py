from fastapi import WebSocket
from typing import Dict, List
import json

class ConnectionManager:
    def __init__(self):
        self.rooms: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, game_code: str):
        await websocket.accept()
        if game_code not in self.rooms:
            self.rooms[game_code] = []
        self.rooms[game_code].append(websocket)
        print(f"[WS-CONNECT] {game_code} room_size={len(self.rooms[game_code])}")

    def disconnect(self, websocket: WebSocket, game_code: str):
        if game_code in self.rooms:
            try:
                self.rooms[game_code].remove(websocket)
            except ValueError:
                pass
            print(f"[WS-DISCONNECT] {game_code} room_size={len(self.rooms[game_code])}")

    async def broadcast(self, game_code: str, message: dict):
        if game_code not in self.rooms:
            return
        dead = []
        for ws in self.rooms[game_code]:
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.rooms[game_code].remove(ws)

    async def send_personal(self, websocket: WebSocket, message: dict):
        await websocket.send_text(json.dumps(message))

manager = ConnectionManager()