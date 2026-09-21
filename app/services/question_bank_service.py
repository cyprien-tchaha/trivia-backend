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
    # The host UI's title picker sends "Name (Year)" — it appends the year the
    # search proxy returned, so "One Piece" arrives as "One Piece (1999)".
    # That suffix has to go before punctuation is flattened, or the year
    # survives as a bare token ("one piece 1999") and never matches a banked
    # topic. Only a TRAILING parenthesised year is a picker artifact; a year
    # inside the title ("Blade Runner 2049") is part of the name and stays.
    s = re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", s)
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

async def try_bank(db: AsyncSession, topics: str, difficulty: int, count: int):
    """
    Return `count` questions drawn from the bank, or None if this game should
    fall through to live AI generation.

    This is the single decision point for bank-vs-AI, and it is all-or-nothing.
    A topic we have banked but which holds fewer than `count` rows at this
    difficulty returns None rather than a partial draw: topping the shortfall
    up from the AI would put questions that never passed the distinct-answer
    filter into the same game, which is the duplicate the bank exists to
    prevent.

    Falls through to AI when the topic is empty (a whole-category game), lists
    several titles (a custom game), is not banked, or is banked but thin at
    this difficulty.
    """
    topic = match_bank_topic(topics)
    if topic is None:
        return None

    if not await bank_has_enough(db, topic, difficulty, count):
        return None

    return await draw_from_bank(db, topic, difficulty, count)


def suggest_banked_topics(category: str, query: str, limit: int = 8) -> list[str]:
    """
    Topic names we can serve from the bank, matching `query`, in the exact
    casing seed_bank.py stored.

    This exists so the host's title picker never depends on an upstream API
    being reachable. The external title search soft-fails by design, and when
    it does the dropdown goes empty — which left hosts with no way to choose a
    valid topic at all. These suggestions need no network, and they steer
    hosts toward the topics that cost nothing to serve.

    Matching is deliberately loose: a prefix or substring of the canonical
    name, or of any alias, since hosts type "mha" and "aot" as often as the
    full title. An empty query lists the category so the picker can show
    what's available before anything is typed.
    """
    n = _norm_topic(query)

    out: list[str] = []
    for display, canon in _CANONICAL_BY_NORM.items():
        if BANK_TOPICS.get(canon) != category:
            continue
        if n:
            # the canonical name plus every alias that points at it
            haystacks = [canon] + [a for a, target in ALIASES.items() if target == canon]
            if not any(n in h for h in haystacks):
                continue
        out.append(display)

    return sorted(out)[:limit]
