"""
The server clock.

Each test drives tick() directly rather than waiting on wall-clock time, by
setting phase_ends_at into the past.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import game_loop
from app.models import Answer, Game, Player, Question


def past(seconds: int = 1) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


def future(seconds: int = 60) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def as_utc(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes even from a timezone=True column, so
    normalise before comparing. Postgres returns these aware."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def use_test_db(db, monkeypatch):
    """Point the loop's own sessions at the test database."""
    class Factory:
        def __call__(self): return self
        async def __aenter__(self): return db
        async def __aexit__(self, *exc): return False
    monkeypatch.setattr(game_loop, "AsyncSessionLocal", Factory())
    return db


@pytest.fixture
def broadcasts(monkeypatch):
    sent = []
    async def fake(code, message):
        sent.append((code, message))
    monkeypatch.setattr(game_loop.manager, "broadcast", fake)
    # single-instance behaviour: every claim succeeds
    async def claim(key, ttl_seconds=30): return True
    monkeypatch.setattr(game_loop.manager, "claim_once", claim)
    return sent


async def _game(db, *, index=0, phase="question", ends_at=None, count=3, status="active"):
    g = Game(code="ABCDEF", host_name="H", question_count=count,
             current_question_index=index, phase=phase, phase_ends_at=ends_at,
             status=status)
    db.add(g)
    # Game.id comes from a Python-side default applied at flush, so it is None
    # until the row is written — flush before using it as a foreign key.
    await db.flush()
    for i in range(count):
        db.add(Question(game_id=g.id, text=f"q{i}", options=["a", "b", "c", "d"],
                        correct_answer="a", order_index=i))
    await db.commit()
    await db.refresh(g)
    return g


# ------------------------------------------------------------ the core bug

async def test_an_expired_question_reveals_without_the_host(db, broadcasts):
    """The whole point: nobody touched the host's browser and the game still
    moved on."""
    g = await _game(db, ends_at=past())

    assert await game_loop.tick() == 1

    await db.refresh(g)
    assert g.phase == "result"
    assert as_utc(g.phase_ends_at) > datetime.now(timezone.utc)
    assert [m["event"] for _, m in broadcasts] == ["all_answered"]


async def test_an_expired_result_moves_to_the_next_question(db, broadcasts):
    g = await _game(db, index=0, phase="result", ends_at=past())

    assert await game_loop.tick() == 1

    await db.refresh(g)
    assert g.current_question_index == 1
    assert g.phase == "question"
    code, msg = broadcasts[-1]
    assert msg == {"event": "next_question", "question_index": 1}


async def test_the_last_question_finishes_the_game(db, broadcasts):
    g = await _game(db, index=2, phase="result", ends_at=past(), count=3)
    db.add(Player(game_id=g.id, name="Ada", score=300))
    await db.commit()

    await game_loop.tick()

    await db.refresh(g)
    assert g.status == "finished"
    assert g.phase_ends_at is None, "a finished game must stop being polled"
    _, msg = broadcasts[-1]
    assert msg["event"] == "game_finished"
    assert msg["players"] == [{"id": msg["players"][0]["id"], "name": "Ada", "score": 300}]


# ------------------------------------------------------ what it must not do

async def test_a_game_with_time_left_is_untouched(db, broadcasts):
    g = await _game(db, ends_at=future())
    assert await game_loop.tick() == 0
    await db.refresh(g)
    assert g.phase == "question"
    assert broadcasts == []


async def test_a_null_deadline_is_left_to_the_host(db, broadcasts):
    """Games already in flight when this shipped have no deadline. They must
    stay host-driven rather than having a clock appear under their players."""
    g = await _game(db, ends_at=None)
    assert await game_loop.tick() == 0
    assert broadcasts == []


async def test_lobby_and_finished_games_are_ignored(db, broadcasts):
    await _game(db, ends_at=past(), status="lobby")
    assert await game_loop.tick() == 0
    assert broadcasts == []


async def test_a_lost_claim_means_another_instance_is_doing_it(db, monkeypatch):
    """Two instances tick at once; only the claim winner may advance, or the
    question would be skipped for everyone."""
    sent = []
    async def fake(code, message): sent.append(message)
    monkeypatch.setattr(game_loop.manager, "broadcast", fake)
    async def lose(key, ttl_seconds=30): return False
    monkeypatch.setattr(game_loop.manager, "claim_once", lose)

    g = await _game(db, ends_at=past())

    assert await game_loop.tick() == 0
    await db.refresh(g)
    assert g.phase == "question", "lost the claim but advanced anyway"
    assert sent == []


async def test_the_reveal_counts_correct_answers(db, broadcasts):
    g = await _game(db, ends_at=past())
    q = (await db.execute(
        select(Question).where(Question.game_id == g.id, Question.order_index == 0)
    )).scalar_one()
    for name, ok in (("Ada", True), ("Grace", True), ("Alan", False)):
        p = Player(game_id=g.id, name=name)
        db.add(p)
        await db.commit()
        db.add(Answer(game_id=g.id, player_id=p.id, question_id=q.id,
                      answer="a" if ok else "b", correct=ok))
    await db.commit()

    await game_loop.tick()

    _, msg = broadcasts[-1]
    assert msg["correct_count"] == 2
    assert msg["correct_answer"] == "a"
