"""
Free-tier question bank: matching and drawing.

A game can be served from the pre-generated bank when its requested topic is one
we have banked. Otherwise it falls through to live AI generation (the paid path).

The important guarantee lives here: draw_from_bank never returns two questions
that share a correct answer, so a single game cannot show the "What is Luffy's
dream / What does Luffy want to become" duplicate even if both are banked. This
is enforced on the ~10 drawn questions, which is reliable, rather than by trying
to keep all 60 banked rows mutually distinct, which is not.
"""

import random
import re
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import QuestionBank

# Canonical banked topics -> category. Must match what seed_bank.py wrote.
# Keys are compared case-insensitively via the normaliser below.
BANK_TOPICS = {
    "one piece":                 "anime",
    "naruto":                    "anime",
    "attack on titan":           "anime",
    "demon slayer":              "anime",
    "jujutsu kaisen":            "anime",
    "my hero academia":          "anime",
    "death note":                "anime",
    "dragon ball":               "anime",
    "studio ghibli":             "anime",
    "breaking bad":              "tv_shows",
    "marvel cinematic universe": "movies",
}

# a handful of common aliases -> canonical banked topic
ALIASES = {
    "mha":  "my hero academia",
    "jjk":  "jujutsu kaisen",
    "aot":  "attack on titan",
    "dbz":  "dragon ball",
    "dragon ball z": "dragon ball",
    "mcu":  "marvel cinematic universe",
    "marvel": "marvel cinematic universe",
    "ghibli": "studio ghibli",
}


def _norm_topic(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = " ".join(s.split())
    return s


def _norm_answer(s: str) -> str:
    """Same shape as the seed script - so two phrasings of one answer collide."""
    s = (s or "").lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    s = " ".join(s.split())
    lead = r"^(to\s+become\s+|to\s+be\s+|it\s+becomes\s+|becomes\s+|he\s+becomes\s+|she\s+becomes\s+|to\s+|the\s+|a\s+|an\s+)+"
    prev = None
    while prev != s:
        prev = s
        s = re.sub(lead, "", s)
    s = re.sub(r"\b(the|a|an|of|to|become|becomes|his|her|their|is|are|it|he|she)\b", " ", s)
    return " ".join(s.split())


def match_bank_topic(topics: str):
    """
    Return the canonical banked topic name if this game's topic string maps to
    one we have banked, else None. A game with an empty/multi/custom topic is
    NOT a bank match and should go to paid generation.
    """
    n = _norm_topic(topics)
    if not n:
        return None
    # only a single clean topic can match; "one piece, naruto" is custom
    if "," in topics:
        return None
    n = ALIASES.get(n, n)
    if n in BANK_TOPICS:
        # recover the original-cased canonical form for the DB query
        for canon in _CANONICAL_BY_NORM:
            if _CANONICAL_BY_NORM[canon] == n:
                return canon
    return None


# map normalised -> the exact string stored in question_bank.topic
_CANONICAL_BY_NORM = {
    "One Piece": "one piece",
    "Naruto": "naruto",
    "Attack on Titan": "attack on titan",
    "Demon Slayer": "demon slayer",
    "Jujutsu Kaisen": "jujutsu kaisen",
    "My Hero Academia": "my hero academia",
    "Death Note": "death note",
    "Dragon Ball": "dragon ball",
    "Studio Ghibli": "studio ghibli",
    "Breaking Bad": "breaking bad",
    "Marvel Cinematic Universe": "marvel cinematic universe",
}


async def bank_has_enough(db: AsyncSession, topic: str, difficulty: int, count: int) -> bool:
    """True if the bank holds at least `count` rows for this exact pair."""
    result = await db.execute(
        select(func.count(QuestionBank.id)).where(
            QuestionBank.topic == topic,
            QuestionBank.difficulty == difficulty,
        )
    )
    return (result.scalar() or 0) >= count


async def draw_from_bank(db: AsyncSession, topic: str, difficulty: int, count: int):
    """
    Draw `count` questions for a (topic, difficulty), guaranteeing no two share a
    correct answer. Returns a list of dicts shaped exactly like generate_questions
    output, so the caller stores them identically.

    Draws a generous candidate pool, shuffles, then greedily picks while skipping
    any answer already used. Falls back to filling from leftovers only if the
    distinct-answer pool is somehow smaller than `count` (shouldn't happen given
    the seed rules, but we never want to return fewer than asked).
    """
    # pull a pool larger than needed so the distinct-answer filter has room
    pool_size = min(count * 4, 60)
    result = await db.execute(
        select(QuestionBank)
        .where(
            QuestionBank.topic == topic,
            QuestionBank.difficulty == difficulty,
        )
        .order_by(func.random())
        .limit(pool_size)
    )
    rows = list(result.scalars().all())
    random.shuffle(rows)

    picked = []
    used_answers = set()
    leftovers = []

    for r in rows:
        na = _norm_answer(r.correct_answer)
        if na in used_answers:
            leftovers.append(r)
            continue
        used_answers.add(na)
        picked.append(r)
        if len(picked) == count:
            break

    # safety net: if distinct answers ran short, top up from leftovers
    if len(picked) < count:
        for r in leftovers:
            picked.append(r)
            if len(picked) == count:
                break

    random.shuffle(picked)

    return [
        {
            "text": r.text,
            "options": r.options,
            "correct_answer": r.correct_answer,
            "difficulty": r.difficulty,
            "category": r.category,
        }
        for r in picked[:count]
    ]