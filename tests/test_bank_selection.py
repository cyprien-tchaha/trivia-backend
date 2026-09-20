"""
The bank-vs-AI decision.

`try_bank` is the single all-or-nothing decision point: it returns questions
when the bank can serve the whole game, and None when the caller should fall
through to live AI generation.
"""
from app.services.question_bank_service import try_bank


async def _fill(db, bank_row, *, topic="One Piece", difficulty=1, n=10):
    for i in range(n):
        db.add(bank_row(topic=topic, difficulty=difficulty, text=f"q{i}",
                        correct_answer=f"answer {i}"))
    await db.commit()


async def test_banked_topic_with_enough_rows_is_served_from_the_bank(db, bank_row):
    await _fill(db, bank_row, n=12)

    drawn = await try_bank(db, "One Piece", 1, 10)

    assert drawn is not None
    assert len(drawn) == 10


async def test_alias_is_served_from_the_bank(db, bank_row):
    await _fill(db, bank_row, topic="Attack on Titan", n=10)

    drawn = await try_bank(db, "aot", 1, 10)

    assert drawn is not None
    assert len(drawn) == 10


async def test_banked_topic_with_too_few_rows_falls_through_to_ai(db, bank_row):
    """All-or-nothing. 7 banked rows cannot serve a 10-question game, and
    topping up from the AI would put questions outside the distinct-answer
    filter into the same game."""
    await _fill(db, bank_row, n=7)

    assert await try_bank(db, "One Piece", 1, 10) is None


async def test_unbanked_topic_falls_through_to_ai(db, bank_row):
    await _fill(db, bank_row, n=20)

    assert await try_bank(db, "Bleach", 1, 10) is None


async def test_multi_topic_game_falls_through_to_ai(db, bank_row):
    """A game spanning several titles is a custom game, even when each title
    is banked on its own."""
    await _fill(db, bank_row, topic="One Piece", n=20)
    await _fill(db, bank_row, topic="Naruto", n=20)

    assert await try_bank(db, "One Piece, Naruto", 1, 10) is None


async def test_empty_topic_falls_through_to_ai(db, bank_row):
    """A game with no topic is a whole-category game, which the bank does not
    serve."""
    await _fill(db, bank_row, n=20)

    assert await try_bank(db, "", 1, 10) is None


async def test_wrong_difficulty_falls_through_to_ai(db, bank_row):
    """The bank is seeded per (topic, difficulty). A banked topic at an
    unseeded difficulty is not a hit."""
    await _fill(db, bank_row, difficulty=1, n=20)

    assert await try_bank(db, "One Piece", 5, 10) is None


async def test_exact_count_is_a_hit(db, bank_row):
    await _fill(db, bank_row, n=10)

    drawn = await try_bank(db, "One Piece", 1, 10)

    assert drawn is not None
    assert len(drawn) == 10
