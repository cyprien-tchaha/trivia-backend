from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import Game, Player, Question
from app.schemas import CreateGameRequest, JoinGameRequest
from app.websocket.manager import manager
import random, string

router = APIRouter()

def gen_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase, k=length))

@router.post("/create")
async def create_game(req: CreateGameRequest, db: AsyncSession = Depends(get_db)):
    code = gen_code()
    game = Game(
        code=code,
        host_name=req.host_name,
        category=req.category,
        difficulty=req.difficulty,
        question_count=req.question_count,
        topics=req.topics,
    )
    db.add(game)
    await db.commit()
    await db.refresh(game)
    return {
        "game_id": game.id,
        "code": game.code,
        "host_name": game.host_name,
        "category": game.category,
        "difficulty": game.difficulty,
        "question_count": game.question_count,
    }

@router.post("/{code}/join")
async def join_game(code: str, req: JoinGameRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Game).where(Game.code == code.upper()))
    game = result.scalar_one_or_none()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    if game.status != "lobby":
        raise HTTPException(status_code=400, detail="Game already started")
    player = Player(game_id=game.id, name=req.player_name)
    db.add(player)
    await db.commit()
    await db.refresh(player)
    await manager.broadcast(code.upper(), {
        "event": "player_joined",
        "player": {"id": player.id, "name": player.name, "score": 0}
    })
    return {"player_id": player.id, "game_id": game.id, "code": code.upper()}

@router.get("/{code}/players")
async def get_players(code: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Game).where(Game.code == code.upper()))
    game = result.scalar_one_or_none()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    result = await db.execute(select(Player).where(Player.game_id == game.id))
    players = result.scalars().all()
    return [{"id": p.id, "name": p.name, "score": p.score} for p in players]

@router.get("/{code}")
async def get_game(code: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Game).where(Game.code == code.upper()))
    game = result.scalar_one_or_none()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return {
        "game_id": game.id,
        "code": game.code,
        "host_name": game.host_name,
        "status": game.status,
        "category": game.category,
        "difficulty": game.difficulty,
        "question_count": game.question_count,
        "topics": game.topics,
    }

@router.post("/{code}/start")
async def start_game(code: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Game).where(Game.code == code.upper()))
    game = result.scalar_one_or_none()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    game.status = "active"
    await db.commit()
    await manager.broadcast(code.upper(), {"event": "game_started"})
    return {"status": "started"}

@router.post("/{code}/finish")
async def finish_game(code: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Game).where(Game.code == code.upper()))
    game = result.scalar_one_or_none()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    game.status = "finished"
    await db.commit()
    result = await db.execute(select(Player).where(Player.game_id == game.id))
    players = result.scalars().all()
    player_list = [{"id": p.id, "name": p.name, "score": p.score} for p in players]
    await manager.broadcast(code.upper(), {
        "event": "game_finished",
        "players": player_list
    })
    return {"status": "finished", "players": player_list}

@router.post("/{code}/answer")
async def submit_answer(code: str, req: dict, db: AsyncSession = Depends(get_db)):
    from app.models import Question
    result = await db.execute(select(Game).where(Game.code == code.upper()))
    game = result.scalar_one_or_none()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    result = await db.execute(select(Player).where(Player.id == req.get("player_id")))
    player = result.scalar_one_or_none()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    result = await db.execute(select(Question).where(Question.id == req.get("question_id")))
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    correct = req.get("answer") == question.correct_answer
    if correct:
        time_taken_ms = req.get("time_taken_ms", 30000)
        speed_bonus = max(0, int((30000 - time_taken_ms) / 1000))
        player.score += 100 + speed_bonus
    await db.commit()
    if not correct:
        pass
    else:
        await db.commit()
    
    await manager.broadcast(code.upper(), {
        "event": "answer_result",
        "player_id": player.id,
        "player_name": player.name,
        "correct": correct,
        "correct_answer": question.correct_answer,
        "score": player.score
    })

    # Check if all players have answered
    all_players_result = await db.execute(
        select(Player).where(Player.game_id == game.id)
    )
    all_players = all_players_result.scalars().all()

    # Count answers for this question by checking scores changed
    # Simple approach: broadcast all_answered when answer count matches player count
    # We track this via a simple in-memory counter using the manager
    room_key = f"{code.upper()}_q{question.order_index}"
    if not hasattr(manager, 'answer_counts'):
        manager.answer_counts = {}
    manager.answer_counts[room_key] = manager.answer_counts.get(room_key, 0) + 1

    if manager.answer_counts[room_key] >= len(all_players):
        await manager.broadcast(code.upper(), {
            "event": "all_answered",
            "correct_answer": question.correct_answer,
        })
        manager.answer_counts[room_key] = 0

    return {"correct": correct, "score": player.score, "correct_answer": question.correct_answer}

@router.post("/{code}/question/{index}")
async def set_question_index(code: str, index: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Game).where(Game.code == code.upper()))
    game = result.scalar_one_or_none()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    # Reset answer count for previous question
    if hasattr(manager, 'answer_counts'):
        old_key = f"{code.upper()}_q{game.current_question_index}"
        manager.answer_counts[old_key] = 0
    game.current_question_index = index
    await db.commit()
    return {"status": "ok", "current_question_index": index}

@router.post("/{code}/reset")
async def reset_game(code: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Game).where(Game.code == code.upper()))
    game = result.scalar_one_or_none()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    # Delete old questions
    from sqlalchemy import delete
    await db.execute(delete(Question).where(Question.game_id == game.id))
    
    # Reset game state
    game.status = "lobby"
    game.current_question_index = 0
    await db.commit()
    
    # Reset player scores
    result = await db.execute(select(Player).where(Player.game_id == game.id))
    players = result.scalars().all()
    for player in players:
        player.score = 0
    await db.commit()
    
    await manager.broadcast(code.upper(), {"event": "game_reset"})
    return {"status": "reset"}