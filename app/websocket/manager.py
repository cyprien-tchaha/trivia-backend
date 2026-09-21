"""
WebSocket rooms, with cross-instance fanout over Redis.

A WebSocket lives inside a single server process, so `rooms` can only ever
hold *this* instance's sockets. Behind a load balancer with more than one
instance, players in the same game land on different processes — and a
broadcast written straight to local sockets reaches only the slice of the room
that happens to share a process with the publisher. Everyone else silently
misses every event: their question never advances, their scores never update.
That ceiling is why the service could only ever run one instance.

So a broadcast does not write to local sockets. It publishes to Redis, and
every instance — including the one that published — delivers to its own
sockets from the subscriber. One delivery path, so nobody receives an event
twice.

When Redis is not configured or not reachable, broadcast writes local sockets
directly. That is exactly the old single-instance behaviour: correct for one
process, and far better than a game that stops working because the cache is
down.
"""
from fastapi import WebSocket
from typing import Any, Dict, List, Optional
import asyncio
import json
import os


class ConnectionManager:
    #: Every instance subscribes to this one channel and filters by game code.
    #: A channel per game would cut idle traffic, but it means subscribing and
    #: unsubscribing as rooms come and go, which races against reconnects. At
    #: party-game volume the filtering cost is irrelevant and this cannot get
    #: out of sync. Revisit if broadcast volume ever becomes the bottleneck.
    CHANNEL = "trivia:ws:broadcast"

    def __init__(self) -> None:
        self.rooms: Dict[str, List[WebSocket]] = {}
        self._redis: Optional[Any] = None
        self._pubsub: Optional[Any] = None
        self._listener: Optional[asyncio.Task] = None
        self._healthy = False

    @property
    def fanout_active(self) -> bool:
        """True when broadcasts are going through Redis rather than staying
        local. Also the signal the health endpoint reports."""
        return self._redis is not None and self._healthy

    # ------------------------------------------------------------- lifecycle

    async def start(self, redis_url: Optional[str] = None) -> None:
        """
        Connect to Redis and begin listening. Safe to call when Redis is
        absent or down — the manager falls back to local delivery and the app
        still starts. Never raises.
        """
        url = redis_url if redis_url is not None else os.getenv("REDIS_URL", "")
        if not url:
            print("[WS] REDIS_URL not set — single-instance mode, broadcasts stay local")
            return

        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                url,
                decode_responses=True,
                # Detect a silently dropped connection rather than waiting
                # forever on a subscriber that will never receive again.
                health_check_interval=30,
            )
            await self._redis.ping()
            self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
            await self._pubsub.subscribe(self.CHANNEL)
            self._healthy = True
            self._listener = asyncio.create_task(self._listen())
            print(f"[WS] redis fanout active on {self.CHANNEL}")
        except Exception as e:
            print(
                f"[WS] redis unavailable ({type(e).__name__}: {e}) — "
                f"falling back to local broadcast"
            )
            await self._close_redis()

    async def stop(self) -> None:
        if self._listener is not None:
            self._listener.cancel()
            try:
                await self._listener
            except (asyncio.CancelledError, Exception):
                pass
            self._listener = None
        await self._close_redis()

    async def _close_redis(self) -> None:
        self._healthy = False
        if self._pubsub is not None:
            try:
                await self._pubsub.aclose()
            except Exception:
                pass
            self._pubsub = None
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None

    # ----------------------------------------------------------- membership

    async def connect(self, websocket: WebSocket, game_code: str) -> None:
        await websocket.accept()
        self.rooms.setdefault(game_code, []).append(websocket)
        print(f"[WS-CONNECT] {game_code} room_size={len(self.rooms[game_code])}")

    def disconnect(self, websocket: WebSocket, game_code: str) -> None:
        if game_code in self.rooms:
            try:
                self.rooms[game_code].remove(websocket)
            except ValueError:
                pass
            print(f"[WS-DISCONNECT] {game_code} room_size={len(self.rooms[game_code])}")

    def local_connections(self, game_code: str) -> int:
        """Sockets held by *this* instance. With fanout on, a room can have
        members on other instances that this number does not see."""
        return len(self.rooms.get(game_code, []))

    # ------------------------------------------------------------ broadcast

    async def broadcast(self, game_code: str, message: dict) -> None:
        if self.fanout_active:
            try:
                await self._redis.publish(
                    self.CHANNEL,
                    json.dumps({"code": game_code, "message": message}),
                )
                return
            except Exception as e:
                # Don't drop the event. Mark fanout down so subsequent
                # broadcasts go local too, and deliver this one locally.
                print(f"[WS] publish failed ({type(e).__name__}: {e}); delivering locally")
                self._healthy = False

        await self._deliver_local(game_code, message)

    async def _deliver_local(self, game_code: str, message: dict) -> None:
        sockets = self.rooms.get(game_code)
        if not sockets:
            return
        dead = []
        for ws in sockets:
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                dead.append(ws)
        for ws in dead:
            try:
                sockets.remove(ws)
            except ValueError:
                pass

    async def send_personal(self, websocket: WebSocket, message: dict) -> None:
        await websocket.send_text(json.dumps(message))

    # ------------------------------------------------------------ subscriber

    async def _listen(self) -> None:
        """
        Deliver every published broadcast to this instance's sockets.

        Reconnects with backoff. While disconnected `_healthy` is false, so
        broadcast() delivers locally instead of publishing into a channel
        nobody is reading — the failure mode that would otherwise look like
        the game quietly freezing for everyone.
        """
        backoff = 1.0
        while True:
            try:
                async for raw in self._pubsub.listen():
                    if raw.get("type") != "message":
                        continue
                    self._healthy = True
                    backoff = 1.0
                    await self._dispatch(raw.get("data"))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._healthy = False
                print(
                    f"[WS] fanout listener error ({type(e).__name__}: {e}); "
                    f"retrying in {backoff:.0f}s"
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                try:
                    await self._pubsub.subscribe(self.CHANNEL)
                    self._healthy = True
                except Exception:
                    pass

    async def _dispatch(self, data: Any) -> None:
        """One malformed message must not take fanout down for every game on
        this instance, so parse failures are logged and skipped."""
        try:
            payload = json.loads(data)
            code = payload["code"]
            message = payload["message"]
        except (TypeError, ValueError, KeyError) as e:
            print(f"[WS] dropped malformed fanout payload ({type(e).__name__}: {e})")
            return
        await self._deliver_local(code, message)


manager = ConnectionManager()
