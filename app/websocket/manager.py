from fastapi import WebSocket
from typing import Dict, List
import json
import asyncio
from datetime import datetime, timezone

class ConnectionManager:
    def __init__(self):
        self.rooms: Dict[str, List[WebSocket]] = {}
        # Grace window: {player_id: {"game_code": str, "expires_at": datetime}}
        self.grace_window: Dict[str, dict] = {}

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

    def add_to_grace_window(self, player_id: str, game_code: str, seconds: int = 30):
        """Mark a player as disconnected but give them time to reconnect."""
        expires_at = datetime.now(timezone.utc).timestamp() + seconds
        self.grace_window[player_id] = {
            "game_code": game_code,
            "expires_at": expires_at,
        }

    def is_in_grace_window(self, player_id: str) -> bool:
        """Check if player disconnected recently and grace window hasn't expired."""
        if player_id not in self.grace_window:
            return False
        entry = self.grace_window[player_id]
        if datetime.now(timezone.utc).timestamp() < entry["expires_at"]:
            return True
        # Expired — clean up
        del self.grace_window[player_id]
        return False

    def remove_from_grace_window(self, player_id: str):
        """Call this when player successfully reconnects."""
        self.grace_window.pop(player_id, None)

    def grace_window_expired(self, player_id: str) -> bool:
        """Returns True if player was in grace window but it has now expired."""
        if player_id not in self.grace_window:
            return False
        entry = self.grace_window[player_id]
        if datetime.now(timezone.utc).timestamp() >= entry["expires_at"]:
            del self.grace_window[player_id]
            return True
        return False

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