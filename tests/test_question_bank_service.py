"""
Characterization tests for the question bank service.

This code was written but never wired in, so these tests lock in what it
actually does before anything starts depending on it.
"""
import pytest

from app.services.question_bank_service import (
    _norm_answer,
    bank_has_enough,
    draw_from_bank,
    match_bank_topic,
)


# ---------------------------------------------------------------- matching

@pytest.mark.parametrize("topics,expected", [
    ("One Piece", "One Piece"),
    ("one piece", "One Piece"),
    ("  ONE PIECE  ", "One Piece"),
    ("one-piece", "One Piece"),            # punctuation normalises to a space
    ("Attack on Titan", "Attack on Titan"),
    ("Marvel Cinematic Universe", "Marvel Cinematic Universe"),
])
def test_match_returns_the_stored_casing(topics, expected):
    """The return value is fed straight into a `topic ==` query, so it has to
    match the casing seed_bank.py wrote, not the user's casing."""
    assert match_bank_topic(topics) == expected


@pytest.mark.parametrize("topics,expected", [
    ("One Piece (1999)", "One Piece"),
    ("Naruto (2002)", "Naruto"),
    ("Attack on Titan (2013)", "Attack on Titan"),
    ("Breaking Bad (2008)", "Breaking Bad"),
    ("one piece (1999)", "One Piece"),
    ("Marvel Cinematic Universe (2008)", "Marvel Cinematic Universe"),
])
def test_title_picker_year_suffix_still_matches(topics, expected):
    """The host UI's title picker sends "Name (Year)" — see host/page.tsx,
    which appends t.year from the search proxy. Without stripping that suffix
    the bank never matches a game created through the picker, which is the
    primary path, so every such game would silently pay for AI generation."""
    assert match_bank_topic(topics) == expected


def test_a_year_inside_the_title_is_not_stripped():
    """Only a trailing parenthesised year is a picker artifact. A year that is
    part of the title itself must survive normalisation."""
    from app.services.question_bank_service import _norm_topic
    assert _norm_topic("Blade Runner 2049") == "blade runner 2049"
    assert _norm_topic("2012") == "2012"


@pytest.mark.parametrize("alias,expected", [
    ("mha", "My Hero Academia"),
    ("jjk", "Jujutsu Kaisen"),
    ("aot", "Attack on Titan"),
    ("dbz", "Dragon Ball"),
    ("Dragon Ball Z", "Dragon Ball"),
    ("mcu", "Marvel Cinematic Universe"),
    ("ghibli", "Studio Ghibli"),
])
def test_aliases_resolve(alias, expected):
    assert match_bank_topic(alias) == expected


@pytest.mark.parametrize("topics", [
    "",
    "   ",
    None,
    "Bleach",                    # real show, not banked
    "one piece, naruto",         # multi-topic is a custom game
    "One Piece,Naruto",
])
def test_non_matches_fall_through_to_ai(topics):
    assert match_bank_topic(topics) is None


# ---------------------------------------------------------------- counting

async def test_bank_has_enough_is_inclusive_at_the_boundary(db, bank_row):
    for i in range(3):
        db.add(bank_row(topic="One Piece", difficulty=1, text=f"q{i}", correct_answer=f"a{i}"))
    await db.commit()

    assert await bank_has_enough(db, "One Piece", 1, 3) is True
    assert await bank_has_enough(db, "One Piece", 1, 4) is False


async def test_bank_has_enough_is_scoped_to_topic_and_difficulty(db, bank_row):
    db.add(bank_row(topic="One Piece", difficulty=1, text="a", correct_answer="1"))
    db.add(bank_row(topic="One Piece", difficulty=2, text="b", correct_answer="2"))
    db.add(bank_row(topic="Naruto", difficulty=1, text="c", correct_answer="3"))
    await db.commit()

    # Only the one row at (One Piece, 1) counts — not the other difficulty,
    # not the other topic.
    assert await bank_has_enough(db, "One Piece", 1, 1) is True
    assert await bank_has_enough(db, "One Piece", 1, 2) is False


async def test_empty_bank_has_nothing(db):
    assert await bank_has_enough(db, "One Piece", 1, 1) is False


# ---------------------------------------------------------------- drawing

async def test_draw_returns_exactly_the_requested_count(db, bank_row):
    for i in range(30):
        db.add(bank_row(topic="One Piece", difficulty=1, text=f"q{i}", correct_answer=f"a{i}"))
    await db.commit()

    drawn = await draw_from_bank(db, "One Piece", 1, 10)
    assert len(drawn) == 10


async def test_draw_shape_matches_generate_questions(db, bank_row):
    """The caller stores bank rows and AI rows through the same code path, so
    the dicts have to carry the same keys."""
    db.add(bank_row(topic="One Piece", difficulty=3, text="q", correct_answer="a",
                    options=["a", "b", "c", "d"], category="anime"))
    await db.commit()

    drawn = await draw_from_bank(db, "One Piece", 3, 1)
    assert drawn[0] == {
        "text": "q",
        "options": ["a", "b", "c", "d"],
        "correct_answer": "a",
        "difficulty": 3,
        "category": "anime",
    }


async def test_draw_never_repeats_a_correct_answer(db, bank_row):
    """The whole reason this service exists: two phrasings of one question
    share an answer, and a single game must not show both."""
    db.add(bank_row(topic="One Piece", difficulty=1,
                    text="What is Luffy's dream?",
                    correct_answer="To become King of the Pirates"))
    db.add(bank_row(topic="One Piece", difficulty=1,
                    text="What does Luffy want to become?",
                    correct_answer="The King of the Pirates"))
    for i in range(8):
        db.add(bank_row(topic="One Piece", difficulty=1, text=f"filler{i}",
                        correct_answer=f"distinct answer {i}"))
    await db.commit()

    drawn = await draw_from_bank(db, "One Piece", 1, 9)

    normalised = [_norm_answer(q["correct_answer"]) for q in drawn]
    assert len(normalised) == len(set(normalised)), (
        f"two drawn questions share a correct answer: {normalised}"
    )


async def test_draw_tops_up_from_leftovers_rather_than_returning_short(db, bank_row):
    """If the distinct-answer pool is smaller than the ask, returning fewer
    questions than the host configured would be worse than allowing a repeat."""
    for i in range(6):
        # every row normalises to the same answer
        db.add(bank_row(topic="One Piece", difficulty=1, text=f"q{i}",
                        correct_answer="The King of the Pirates"))
    await db.commit()

    drawn = await draw_from_bank(db, "One Piece", 1, 4)
    assert len(drawn) == 4


async def test_draw_is_scoped_to_topic_and_difficulty(db, bank_row):
    db.add(bank_row(topic="One Piece", difficulty=1, text="wanted", correct_answer="a"))
    db.add(bank_row(topic="One Piece", difficulty=2, text="wrong difficulty", correct_answer="b"))
    db.add(bank_row(topic="Naruto", difficulty=1, text="wrong topic", correct_answer="c"))
    await db.commit()

    drawn = await draw_from_bank(db, "One Piece", 1, 5)
    assert [q["text"] for q in drawn] == ["wanted"]
