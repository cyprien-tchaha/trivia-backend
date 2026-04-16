from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from app.routers import games, questions
from app.websocket.manager import manager
import uvicorn
import os 

app = FastAPI(title="Trivia API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(games.router, prefix="/api/games", tags=["games"])
app.include_router(questions.router, prefix="/api/questions", tags=["questions"])

@app.get("/health")
async def health():
    return {"status": "ok", "environment": "development"}

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
                room = manager.rooms.get(code.upper(), [])
                print(f"[WS-BCAST] next_question idx={data.get('question_index')} room_size={len(room)}")
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