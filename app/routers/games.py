from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.database import get_db
from app.models import Game, Player, Question, Answer
from app.schemas import CreateGameRequest, JoinGameRequest
from app.websocket.manager import manager
import random, string, asyncio

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

    existing_player_result = await db.execute(
        select(Player).where(
            Player.game_id == game.id,
            Player.name == req.player_name
        )
    )
    existing = existing_player_result.scalar_one_or_none()
    if existing:
        manager.remove_from_grace_window(existing.id)
        await manager.broadcast(code.upper(), {
            "event": "player_rejoined",
            "player": {"id": existing.id, "name": existing.name, "score": existing.score}
        })
        return {"player_id": existing.id, "game_id": game.id, "code": code.upper(), "rejoined": True}

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
        "current_question_index": game.current_question_index,
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

    existing = await db.execute(
        select(Answer).where(
            and_(
                Answer.player_id == player.id,
                Answer.question_id == question.id,
            )
        )
    )
    if existing.scalar_one_or_none():
        return {"correct": False, "score": player.score, "correct_answer": question.correct_answer, "duplicate": True}

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
        "score": player.score,
        "question_id": question.id,
    })

    active_players_result = await db.execute(
        select(Player).where(Player.game_id == game.id)
    )
    all_players = active_players_result.scalars().all()
    active_players = [p for p in all_players if not manager.is_in_grace_window(p.id)]
    active_player_count = len(active_players)

    answered_result = await db.execute(
        select(Answer).where(
            and_(
                Answer.game_id == game.id,
                Answer.question_id == question.id,
            )
        )
    )
    answered_count = len(answered_result.scalars().all())

    print(f"[ANSWER] player={player.name} total={len(all_players)} active={active_player_count} grace={len(all_players)-active_player_count} answered={answered_count}")

    if active_player_count > 0 and answered_count >= active_player_count:
        correct_result = await db.execute(
            select(Answer).where(
                and_(
                    Answer.game_id == game.id,
                    Answer.question_id == question.id,
                    Answer.correct == True,
                )
            )
        )
        correct_count = len(correct_result.scalars().all())
        print(f"[ANSWER] all_answered firing — correct_count={correct_count}")
        await manager.broadcast(code.upper(), {
            "event": "all_answered",
            "correct_answer": question.correct_answer,
            "correct_count": correct_count,
        })

    return {"correct": correct, "score": player.score, "correct_answer": question.correct_answer}

@router.post("/{code}/question/{index}")
async def set_question_index(code: str, index: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Game).where(Game.code == code.upper()))
    game = result.scalar_one_or_none()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
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

    await db.execute(delete(Answer).where(Answer.game_id == game.id))
    await db.execute(delete(Question).where(Question.game_id == game.id))

    game.status = "active"
    game.current_question_index = 0
    await db.commit()

    result = await db.execute(select(Player).where(Player.game_id == game.id))
    players = result.scalars().all()
    for player in players:
        player.score = 0
    await db.commit()

    return {"status": "reset"}

@router.get("/{code}/player-answer/{player_id}/{question_id}")
async def get_player_answer(code: str, player_id: str, question_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Answer).where(
            Answer.player_id == player_id,
            Answer.question_id == question_id,
        )
    )
    answer = result.scalar_one_or_none()
    if not answer:
        return {"answered": False}

    player_result = await db.execute(select(Player).where(Player.id == player_id))
    player = player_result.scalar_one_or_none()

    q_result = await db.execute(select(Question).where(Question.id == question_id))
    question = q_result.scalar_one_or_none()

    return {
        "answered": True,
        "answer": answer.answer,
        "correct": answer.correct,
        "correct_answer": question.correct_answer if question else "",
        "score": player.score if player else 0,
    }

@router.get("/{code}/resume/{player_id}")
async def resume_game(code: str, player_id: str, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import and_

    result = await db.execute(select(Game).where(Game.code == code.upper()))
    game = result.scalar_one_or_none()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    result = await db.execute(select(Player).where(Player.id == player_id))
    player = result.scalar_one_or_none()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    manager.remove_from_grace_window(player_id)
    print(f"[RESUME] player={player.name} removed from grace window")

    result = await db.execute(
        select(Question)
        .where(Question.game_id == game.id)
        .where(Question.order_index == game.current_question_index)
    )
    current_question = result.scalar_one_or_none()

    answered = None
    if current_question:
        result = await db.execute(
            select(Answer).where(
                and_(
                    Answer.player_id == player_id,
                    Answer.question_id == current_question.id,
                )
            )
        )
        answered = result.scalar_one_or_none()

    return {
        "game_status": game.status,
        "current_question_index": game.current_question_index,
        "player_score": player.score,
        "already_answered": answered is not None,
        "answer": answered.answer if answered else None,
        "correct": answered.correct if answered else None,
        "correct_answer": current_question.correct_answer if current_question else None,
        "question_id": current_question.id if current_question else None,
    }

@router.post("/{code}/leave")
async def leave_game(code: str, request: Request, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import and_
    try:
        body = await request.json()
        player_id = body.get("player_id")
    except Exception:
        return {"status": "ok"}

    if not player_id:
        return {"status": "ok"}

    result = await db.execute(select(Player).where(Player.id == player_id))
    player = result.scalar_one_or_none()
    if not player:
        return {"status": "ok"}

    # Ignore duplicate leave calls — already in grace window
    if manager.is_in_grace_window(player_id):
        print(f"[LEAVE] player={player.name} already in grace window, ignoring duplicate")
        return {"status": "ok"}

    game_id = player.game_id
    game_result = await db.execute(select(Game).where(Game.id == game_id))
    game = game_result.scalar_one_or_none()

    manager.add_to_grace_window(player_id, code.upper(), seconds=30)
    print(f"[LEAVE] player={player.name} id={player_id} added to grace window")

    await manager.broadcast(code.upper(), {
        "event": "player_left",
        "player_id": player_id,
    })

    async def delayed_delete():
        await asyncio.sleep(30)
        if manager.grace_window_expired(player_id):
            print(f"[LEAVE] grace window expired for player={player_id}, deleting")
            from app.database import AsyncSessionLocal
            async with AsyncSessionLocal() as delete_db:
                try:
                    p_result = await delete_db.execute(select(Player).where(Player.id == player_id))
                    p = p_result.scalar_one_or_none()
                    if p:
                        # Delete answers first to avoid foreign key violation
                        await delete_db.execute(delete(Answer).where(Answer.player_id == player_id))
                        await delete_db.delete(p)
                        await delete_db.commit()
                        print(f"[LEAVE] player={player_id} deleted after grace window")
                        if game:
                            active_result = await delete_db.execute(
                                select(Player).where(Player.game_id == game_id)
                            )
                            active_players = active_result.scalars().all()
                            if len(active_players) > 0 and game.status == "active":
                                q_result = await delete_db.execute(
                                    select(Question)
                                    .where(Question.game_id == game_id)
                                    .where(Question.order_index == game.current_question_index)
                                )
                                current_q = q_result.scalar_one_or_none()
                                if current_q:
                                    ans_result = await delete_db.execute(
                                        select(Answer).where(
                                            and_(
                                                Answer.game_id == game_id,
                                                Answer.question_id == current_q.id,
                                            )
                                        )
                                    )
                                    answered_count = len(ans_result.scalars().all())
                                    if answered_count >= len(active_players):
                                        correct_result = await delete_db.execute(
                                            select(Answer).where(
                                                and_(
                                                    Answer.game_id == game_id,
                                                    Answer.question_id == current_q.id,
                                                    Answer.correct == True,
                                                )
                                            )
                                        )
                                        correct_count = len(correct_result.scalars().all())
                                        await manager.broadcast(code.upper(), {
                                            "event": "all_answered",
                                            "correct_answer": current_q.correct_answer,
                                            "correct_count": correct_count,
                                        })
                except Exception as e:
                    print(f"[LEAVE] delayed delete error: {e}")
        else:
            print(f"[LEAVE] player={player_id} reconnected within grace window")

    asyncio.create_task(delayed_delete())

    return {"status": "ok"}

@router.get("/{code}/question-answers/{question_id}")
async def get_question_answers(code: str, question_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Answer).where(Answer.question_id == question_id)
    )
    answers = result.scalars().all()
    return {"count": len(answers)}

@router.get("/{code}/admin")
async def admin_game_status(code: str, db: AsyncSession = Depends(get_db)):
    """Health check endpoint for monitoring during beta."""
    result = await db.execute(select(Game).where(Game.code == code.upper()))
    game = result.scalar_one_or_none()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    result = await db.execute(select(Player).where(Player.game_id == game.id))
    players = result.scalars().all()

    ws_connections = len(manager.rooms.get(code.upper(), []))
    grace_players = [pid for pid in manager.grace_window if manager.is_in_grace_window(pid)]

    return {
        "game": {
            "code": game.code,
            "status": game.status,
            "current_question": game.current_question_index,
            "question_count": game.question_count,
        },
        "players": [{"id": p.id, "name": p.name, "score": p.score} for p in players],
        "websocket_connections": ws_connections,
        "players_in_grace_window": len(grace_players),
    }