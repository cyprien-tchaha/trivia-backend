from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from app.routers import auth, games, questions, search
from app.websocket.manager import manager
from app import game_loop
import asyncio
import uvicorn
import os


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Bring up Redis fanout so WebSocket broadcasts reach players connected to
    # other instances. Never raises: with no REDIS_URL, or Redis down, the
    # manager falls back to local delivery and the app starts either way.
    await manager.start()
    # The server clock. Every instance ticks; a claim per transition means the
    # work happens once. Without it a game freezes the moment the host's tab
    # goes away.
    clock = asyncio.create_task(game_loop.run())
    try:
        yield
    finally:
        clock.cancel()
        try:
            await clock
        except (asyncio.CancelledError, Exception):
            pass
        await manager.stop()


app = FastAPI(title="Trivia API", version="0.1.0", lifespan=lifespan)

def _normalise_origin(raw: str) -> str:
    """
    Reduce a configured value to the form a browser actually sends.

    An `Origin` header is lowercase scheme + host (+ port) and nothing else, and
    CORSMiddleware compares it as a literal string. So `playfanatic.gg`,
    `https://PlayFanatic.gg/` and `https://playfanatic.gg/host` all look right
    in the dashboard and all match nothing — every request fails with no
    `Access-Control-Allow-Origin` header and the frontend can only say
    "check your connection". A missing scheme becomes https; http is left
    alone so local development still works.
    """
    value = raw.strip()
    if not value:
        return ""
    if "://" not in value:
        value = f"https://{value}"
    scheme, _, rest = value.partition("://")
    host = rest.split("/", 1)[0]
    return f"{scheme.lower()}://{host.lower()}" if host else ""


def _cors_config() -> dict:
    """
    Credentialed CORS needs explicit origins.

    The spec forbids pairing `Access-Control-Allow-Credentials: true` with a
    wildcard origin, and browsers enforce it — so with the old
    `allow_origins=["*"], allow_credentials=False` the session cookie was
    never sent and `/api/auth/me` could only ever answer 401.

    With no FRONTEND_URL configured this keeps the previous open,
    credential-less policy, so deploying changes nothing until sign-in is
    actually set up. Extra origins can be added with ALLOWED_ORIGINS
    (comma-separated).
    """
    raw = [os.getenv("FRONTEND_URL", ""), *os.getenv("ALLOWED_ORIGINS", "").split(",")]
    origins = [o for o in (_normalise_origin(r) for r in raw) if o]

    # Accept the apex and www form of whatever was configured. A site served
    # at www. with FRONTEND_URL set to the apex (or the reverse) would
    # otherwise have every API call blocked, and the symptom — "couldn't
    # create the game" with no HTTP response — points nowhere near CORS.
    for origin in list(origins):
        scheme, _, host = origin.partition("://")
        twin = host[4:] if host.startswith("www.") else f"www.{host}"
        origins.append(f"{scheme}://{twin}")

    # A real host configured as http is served over https regardless, so the
    # browser sends an https Origin and nothing matches. Same host, stricter
    # transport — accepting it is not a wider trust. Localhost is exempt:
    # there the http form is the real one.
    for origin in list(origins):
        scheme, _, host = origin.partition("://")
        if scheme == "http" and not host.split(":")[0] in ("localhost", "127.0.0.1"):
            origins.append(f"https://{host}")

    if not origins:
        print("[CORS] FRONTEND_URL unset — open policy, no credentials, sign-in disabled")
        return {"allow_origins": ["*"], "allow_credentials": False}

    if os.getenv("ENVIRONMENT", "development").lower() != "production":
        # Local dev servers, outside production only: a credentialed policy
        # that trusts localhost would let anything a developer happens to be
        # running read authenticated responses.
        origins += [f"http://{h}:{p}" for h in ("localhost", "127.0.0.1") for p in (3000, 3001)]

    origins = sorted(set(origins))
    print(f"[CORS] credentialed, origins={origins}")
    return {"allow_origins": origins, "allow_credentials": True}


CORS = _cors_config()


app.add_middleware(
    CORSMiddleware,
    allow_methods=["*"],
    allow_headers=["*"],
    **CORS,
)

app.include_router(games.router, prefix="/api/games", tags=["games"])
app.include_router(questions.router, prefix="/api/questions", tags=["questions"])
app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "environment": os.getenv("ENVIRONMENT", "development"),
        # False means broadcasts are staying in this process, so the service
        # must not be scaled past one instance.
        "ws_fanout": manager.fanout_active,
        # A CORS misconfiguration blocks every call with no HTTP response, so
        # the browser can only report a generic network error. Publishing the
        # effective policy makes the diagnosis one page load instead of a hunt
        # through a deploy log that has already scrolled away.
        "cors_origins": CORS["allow_origins"],
        "cors_credentials": CORS["allow_credentials"],
    }

@app.websocket("/api/games/{code}/ws")
async def websocket_endpoint(websocket: WebSocket, code: str):
    await manager.connect(websocket, code.upper())
    try:
        while True:
            data = await websocket.receive_json()
            event = data.get("event")

            if event == "player_joined":
                await manager.broadcast(code.upper(), {
                    "event": "player_joined",
                    "player": data.get("player")
                })
            elif event == "game_started":
                await manager.broadcast(code.upper(), {
                    "event": "game_started"
                })
            elif event == "answer_submitted":
                await manager.broadcast(code.upper(), {
                    "event": "answer_submitted",
                    "player_id": data.get("player_id"),
                    "answer": data.get("answer")
                })
            elif event == "next_question":
                print(
                    f"[WS-BCAST] next_question idx={data.get('question_index')} "
                    f"local_room_size={manager.local_connections(code.upper())}"
                )
                await manager.broadcast(code.upper(), {
                    "event": "next_question",
                    "question_index": data.get("question_index")
                })
            elif event == "score_updated":
                await manager.broadcast(code.upper(), {
                    "event": "score_updated",
                    "players": data.get("players")
                })
            elif event == "game_finished":
                await manager.broadcast(code.upper(), {
                    "event": "game_finished",
                    "players": data.get("players")
                })
            else:
                await manager.broadcast(code.upper(), data)

    except WebSocketDisconnect:
        manager.disconnect(websocket, code.upper())

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)