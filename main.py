from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from app.routers import games, questions, search
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(games.router, prefix="/api/games", tags=["games"])
app.include_router(questions.router, prefix="/api/questions", tags=["questions"])
app.include_router(search.router, prefix="/api/search", tags=["search"])

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "environment": os.getenv("ENVIRONMENT", "development"),
        # False means broadcasts are staying in this process, so the service
        # must not be scaled past one instance.
        "ws_fanout": manager.fanout_active,
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