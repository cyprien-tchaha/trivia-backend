"""
Wiring: create_ai_questions serves a banked game from the bank and never calls
the AI, and is otherwise unchanged.
"""
import pytest
from sqlalchemy import select

from app.models import Game, Question


class _FixedSessionFactory:
    """Stands in for AsyncSessionLocal so the background task uses the test
    session instead of opening its own against Postgres."""

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def use_test_session(db, monkeypatch):
    import app.database
    monkeypatch.setattr(app.database, "AsyncSessionLocal", _FixedSessionFactory(db))
    return db


@pytest.fixture
def ai_must_not_be_called(monkeypatch):
    import app.routers.questions as questions

    async def _boom(*args, **kwargs):
        raise AssertionError("AI generation was called for a banked game")

    monkeypatch.setattr(questions, "generate_questions", _boom)


async def _bank(db, bank_row, *, topic="One Piece", difficulty=1, n=12):
    for i in range(n):
        db.add(bank_row(topic=topic, difficulty=difficulty, text=f"banked q{i}",
                        correct_answer=f"answer {i}"))
    await db.commit()


async def _game(db, *, topics="One Piece", difficulty=1, count=10, category="anime"):
    game = Game(code="ABCDEF", host_name="host", topics=topics,
                difficulty=difficulty, question_count=count, category=category)
    db.add(game)
    await db.commit()
    await db.refresh(game)
    return game


async def test_banked_game_is_served_from_the_bank_without_calling_the_ai(
    db, bank_row, use_test_session, ai_must_not_be_called
):
    from app.routers.questions import create_ai_questions

    await _bank(db, bank_row)
    game = await _game(db)

    await create_ai_questions(game.id, "anime", 1, 10, "One Piece")

    result = await db.execute(select(Question).where(Question.game_id == game.id))
    stored = result.scalars().all()
    assert len(stored) == 10
    assert all(q.text.startswith("banked q") for q in stored)


async def test_banked_game_questions_are_ordered_and_complete(
    db, bank_row, use_test_session, ai_must_not_be_called
):
    """order_index drives the game loop, so a bank draw has to populate it the
    same way the AI path does."""
    from app.routers.questions import create_ai_questions

    await _bank(db, bank_row)
    game = await _game(db)

    await create_ai_questions(game.id, "anime", 1, 10, "One Piece")

    result = await db.execute(
        select(Question).where(Question.game_id == game.id).order_by(Question.order_index)
    )
    stored = result.scalars().all()
    assert [q.order_index for q in stored] == list(range(10))
    assert all(q.options and len(q.options) == 4 for q in stored)
    assert all(q.correct_answer in q.options for q in stored)
    assert all(q.game_id == game.id for q in stored)


async def test_thin_bank_falls_through_to_the_ai(db, bank_row, use_test_session, monkeypatch):
    import app.routers.questions as questions

    calls = []

    async def _fake_generate(category, difficulty, count, topics, exclude):
        calls.append((category, difficulty, count, topics))
        return [
            {"text": f"ai q{i}", "options": ["a", "b", "c", "d"],
             "correct_answer": "a", "difficulty": difficulty, "category": category}
            for i in range(count)
        ]

    monkeypatch.setattr(questions, "generate_questions", _fake_generate)

    await _bank(db, bank_row, n=7)          # not enough for a 10-question game
    game = await _game(db)

    await questions.create_ai_questions(game.id, "anime", 1, 10, "One Piece")

    assert calls == [("anime", 1, 10, "One Piece")]
    result = await db.execute(select(Question).where(Question.game_id == game.id))
    stored = result.scalars().all()
    assert len(stored) == 10
    assert all(q.text.startswith("ai q") for q in stored)


async def test_unbanked_topic_falls_through_to_the_ai(db, bank_row, use_test_session, monkeypatch):
    import app.routers.questions as questions

    calls = []

    async def _fake_generate(category, difficulty, count, topics, exclude):
        calls.append(topics)
        return [
            {"text": f"ai q{i}", "options": ["a", "b", "c", "d"],
             "correct_answer": "a", "difficulty": difficulty, "category": category}
            for i in range(count)
        ]

    monkeypatch.setattr(questions, "generate_questions", _fake_generate)

    await _bank(db, bank_row, n=20)
    game = await _game(db, topics="Bleach")

    await questions.create_ai_questions(game.id, "anime", 1, 10, "Bleach")

    assert calls == ["Bleach"]
    result = await db.execute(select(Question).where(Question.game_id == game.id))
    assert len(result.scalars().all()) == 10
