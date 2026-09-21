"""
Server-side game clock.

The game used to be driven entirely by the host's browser: it POSTed
/question/{index} to advance and every client ran its own countdown. So if
the host closed the tab, backgrounded it, or lost wifi, the game froze
permanently for everyone else — there was nothing else to move it on.

This is the backstop. Every instance ticks once a second, finds active games
whose current phase has run out, and moves them along. It does not replace
the host's controls: the host advancing early still works and simply beats
the clock to the transition.

Coordination is a claim per transition, not leader election. The claim key
names the exact move ("game X out of question 3"), so with any number of
instances ticking simultaneously the move happens exactly once, and a crash
mid-move costs one TTL before another instance retries. There is no leader to
elect, nothing to hand over, and no split-brain to reason about.

Games with a null phase_ends_at are left alone. That covers games already in
flight when this shipped — they stay host-driven to the end rather than
having a clock appear underneath their players.
"""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, and_
import asyncio
import os

from app.database import AsyncSessionLocal
from app.models import Game, Player, Question, Answer
from app.websocket.manager import manager

#: How long players get to answer. Matches the countdown the client already
#: renders, so the two agree without the client needing to change.
QUESTION_SECONDS = int(os.getenv("QUESTION_SECONDS", "60"))
#: How long the answer reveal stays up before the next question.
RESULT_SECONDS = int(os.getenv("RESULT_SECONDS", "8"))
#: Tick interval. One second is plenty: it bounds how late a transition can
#: be, and the query behind it is a single indexed predicate.
TICK_SECONDS = float(os.getenv("GAME_TICK_SECONDS", "1.0"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def question_deadline() -> datetime:
    return _now() + timedelta(seconds=QUESTION_SECONDS)


def result_deadline() -> datetime:
    return _now() + timedelta(seconds=RESULT_SECONDS)


async def _claim(game_id: str, phase: str, index: int) -> bool:
    """One winner per (game, phase, index) transition."""
    return await manager.claim_once(
        f"clock:{game_id}:{phase}:{index}",
        ttl_seconds=max(RESULT_SECONDS, QUESTION_SECONDS) + 30,
    )


async def _reveal(db, game: Game) -> None:
    """Question ran out: show the answer, then hold on the result phase."""
    result = await db.execute(
        select(Question)
        .where(Question.game_id == game.id)
        .where(Question.order_index == game.current_question_index)
    )
    question = result.scalar_one_or_none()

    correct_count = 0
    if question is not None:
        rows = await db.execute(
            select(Answer).where(
                and_(
                    Answer.game_id == game.id,
                    Answer.question_id == question.id,
                    Answer.correct == True,  # noqa: E712 — SQLAlchemy needs ==
                )
            )
        )
        correct_count = len(rows.scalars().all())

    game.phase = "result"
    game.phase_ends_at = result_deadline()
    await db.commit()

    print(f"[CLOCK] {game.code} q{game.current_question_index} -> result "
          f"(correct={correct_count})")
    await manager.broadcast(game.code, {
        "event": "all_answered",
        "correct_answer": question.correct_answer if question else "",
        "correct_count": correct_count,
        "question_id": question.id if question else None,
    })


async def _advance(db, game: Game) -> None:
    """Result phase ran out: next question, or finish the game."""
    next_index = game.current_question_index + 1

    total = await db.execute(select(Question).where(Question.game_id == game.id))
    question_count = len(total.scalars().all())
    # question_count on the game is what the host asked for; the number of
    # rows actually stored is what can be played. Use the smaller.
    last = min(question_count, game.question_count or question_count)

    if next_index >= last:
        game.status = "finished"
        game.phase_ends_at = None
        await db.commit()
        players = (await db.execute(
            select(Player).where(Player.game_id == game.id)
        )).scalars().all()
        print(f"[CLOCK] {game.code} finished after q{game.current_question_index}")
        await manager.broadcast(game.code, {
            "event": "game_finished",
            "players": [{"id": p.id, "name": p.name, "score": p.score} for p in players],
        })
        return

    game.current_question_index = next_index
    game.phase = "question"
    game.phase_ends_at = question_deadline()
    await db.commit()

    print(f"[CLOCK] {game.code} -> q{next_index}")
    await manager.broadcast(game.code, {
        "event": "next_question",
        "question_index": next_index,
    })


async def tick() -> int:
    """
    One pass. Returns how many games were moved, which is what the tests
    assert on and what makes this callable directly without a running loop.
    """
    moved = 0
    async with AsyncSessionLocal() as db:
        due = (await db.execute(
            select(Game).where(
                and_(
                    Game.status == "active",
                    Game.phase_ends_at.isnot(None),
                    Game.phase_ends_at <= _now(),
                )
            )
        )).scalars().all()

        for game in due:
            phase = game.phase or "question"
            if not await _claim(game.id, phase, game.current_question_index):
                # Another instance is handling this exact transition.
                continue
            try:
                if phase == "question":
                    await _reveal(db, game)
                else:
                    await _advance(db, game)
                moved += 1
            except Exception as e:
                # One bad game must not stop the clock for every other game
                # on this instance.
                print(f"[CLOCK] error advancing {game.code}: {type(e).__name__}: {e}")
                await db.rollback()
    return moved


async def run() -> None:
    """Tick forever. Started from the app lifespan."""
    print(f"[CLOCK] server game loop running "
          f"(question={QUESTION_SECONDS}s result={RESULT_SECONDS}s tick={TICK_SECONDS}s)")
    while True:
        try:
            await asyncio.sleep(TICK_SECONDS)
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Never let the clock die: a dead clock is the frozen game this
            # module exists to prevent.
            print(f"[CLOCK] tick failed: {type(e).__name__}: {e}")
            await asyncio.sleep(TICK_SECONDS)
