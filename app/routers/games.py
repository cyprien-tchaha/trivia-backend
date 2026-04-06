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
    # Check if player with same name already exists in this game
    existing_player_result = await db.execute(
        select(Player).where(
            Player.game_id == game.id,
            Player.name == req.player_name
        )
    )
    existing = existing_player_result.scalar_one_or_none()
    if existing:
        return {"player_id": existing.id, "game_id": game.id, "code": code.upper()}

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
    from app.models import Question, Answer
    from sqlalchemy import and_

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

    # Check if player already answered this question
    existing = await db.execute(
        select(Answer).where(
            and_(
                Answer.player_id == player.id,
                Answer.question_id == question.id,
            )
        )
    )
    if existing.scalar_one_or_none():
        # Already answered — return their existing score silently
        return {"correct": False, "score": player.score, "correct_answer": question.correct_answer, "duplicate": True}

    # Record the answer
    correct = req.get("answer") == question.correct_answer
    answer_record = Answer(
        game_id=game.id,
        player_id=player.id,
        question_id=question.id,
        answer=req.get("answer", ""),
        correct=correct,
    )
    db.add(answer_record)

    if correct:
        time_taken_ms = req.get("time_taken_ms", 60000)
        speed_bonus = max(0, int((60000 - time_taken_ms) / 1000))
        player.score += 100 + speed_bonus

    await db.commit()

    await manager.broadcast(code.upper(), {
        "event": "answer_result",
        "player_id": player.id,
        "player_name": player.name,
        "correct": correct,
        "correct_answer": question.correct_answer,
        "score": player.score
    })

    # Check if all players have answered this question
    all_players_result = await db.execute(
        select(Player).where(Player.game_id == game.id)
    )
    all_players = all_players_result.scalars().all()

    answered_result = await db.execute(
        select(Answer).where(
            and_(
                Answer.game_id == game.id,
                Answer.question_id == question.id,
            )
        )
    )
    answered_count = len(answered_result.scalars().all())

    if answered_count >= len(all_players):
        await manager.broadcast(code.upper(), {
            "event": "all_answered",
            "correct_answer": question.correct_answer,
        })

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