"""
Seed the free-tier question bank.

    python seed_bank.py              # seed everything to target
    python seed_bank.py --dry-run    # show what it WOULD generate, spend nothing
    python seed_bank.py --topic "One Piece"
    python seed_bank.py --target 20  # smaller run, useful for a first test

Safe to re-run and safe to kill. Every batch is committed as it completes, and
on startup the script counts what's already in the bank per (topic, difficulty)
and only generates the shortfall.

Duplicate handling
------------------
The model reliably produces near-duplicates despite being told not to:

    "What is Luffy's dream?"          -> To become King of the Pirates
    "What does Luffy want to become?" -> The King of the Pirates

Comparing question text does not catch these - the two share almost no words.
What they share is the ANSWER, and that is the reliable signal. So the rule is
one question per distinct answer within a (topic, difficulty) pair, with the
answer normalised first so leading filler cannot disguise a match.

Expect [partial] results. If a topic only yields 60 distinct easy answers then
60 is the honest number of distinct easy questions, and padding to 100 is what
produced duplicates in the first place.

This raises bank quality but is NOT the guarantee that a single game avoids
duplicates - that is enforced at draw time, where checking 10 questions is far
more reliable than keeping 100 mutually distinct.

Rows are inserted one at a time inside savepoints. A batch of ten that contains
one row violating the unique index must not take the other nine down with it.
"""

import argparse
import asyncio
import random
import re
import sys
import time
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import AsyncSessionLocal
from app.models import QuestionBank
from app.services.ai_service import generate_questions, _get_fallback_questions

# ---------------------------------------------------------------- config

TOPICS = [
    ("One Piece",                 "anime"),
    ("Naruto",                    "anime"),
    ("Attack on Titan",           "anime"),
    ("Demon Slayer",              "anime"),
    ("Jujutsu Kaisen",            "anime"),
    ("My Hero Academia",          "anime"),
    ("Death Note",                "anime"),
    ("Dragon Ball",               "anime"),
    ("Studio Ghibli",             "anime"),
    ("Breaking Bad",              "tv_shows"),
    ("Marvel Cinematic Universe", "movies"),
]

DIFFICULTIES     = [1, 2, 3, 4, 5]
TARGET_PER_PAIR  = 100
BATCH_SIZE       = 10
CONCURRENCY      = 3
MAX_EMPTY_ROUNDS = 4
MAX_PER_ANSWER   = 1

EST_COST_PER_CALL = 0.046

FALLBACK_TEXTS = set()
for _cat in ("anime", "tv_shows", "movies"):
    for _q in _get_fallback_questions(_cat, 1):
        FALLBACK_TEXTS.add(_q["text"])

_LEAD = r"^(to\s+become\s+|to\s+be\s+|it\s+becomes\s+|becomes\s+|he\s+becomes\s+|to\s+|the\s+|a\s+|an\s+)+"
_FILL = r"\b(the|a|an|of|to|become|becomes|his|her|their|is|are|it|he|she)\b"


def norm_answer(s: str) -> str:
    """
    Reduce an answer to a comparable core.

        "To become King of the Pirates" -> "king pirates"
        "The King of the Pirates"       -> "king pirates"
        "It becomes rubber"             -> "rubber"
        "He becomes rubber"             -> "rubber"
    """
    s = s.lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    s = " ".join(s.split())
    prev = None
    while prev != s:
        prev = s
        s = re.sub(_LEAD, "", s)
    s = re.sub(_FILL, " ", s)
    return " ".join(s.split())


def norm_text(s: str) -> str:
    """Light normalisation - catches near-identical rewordings only."""
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\b(the|a|an)\b", " ", s)
    return " ".join(s.split())


class Stats:
    def __init__(self):
        self.calls = 0
        self.inserted = 0
        self.rejected_fallback = 0
        self.rejected_text = 0
        self.rejected_answer = 0
        self.rejected_db = 0
        self.errors = 0
        self.lock = asyncio.Lock()


# ---------------------------------------------------------------- helpers

async def load_pair(db, topic: str, difficulty: int):
    """Return (texts, normalised texts, normalised answer counts) for this pair."""
    rows = await db.execute(
        select(QuestionBank.text, QuestionBank.correct_answer).where(
            QuestionBank.topic == topic,
            QuestionBank.difficulty == difficulty,
        )
    )
    texts, norms, answers = set(), set(), defaultdict(int)
    for text, ans in rows.fetchall():
        texts.add(text)
        norms.add(norm_text(text))
        answers[norm_answer(ans)] += 1
    return texts, norms, answers


async def seed_pair(topic, category, difficulty, target, stats, sem, dry_run):
    async with sem:
        async with AsyncSessionLocal() as db:
            have, have_norm, answers = await load_pair(db, topic, difficulty)

            if len(have) >= target:
                print(f"[skip] {topic} d{difficulty}: already {len(have)}/{target}")
                return

            need = target - len(have)
            print(f"[start] {topic} d{difficulty}: have {len(have)}, need {need}")

            if dry_run:
                async with stats.lock:
                    stats.calls += -(-need // BATCH_SIZE)
                return

            empty_rounds = 0
            gen_failures = 0

            while len(have) < target and empty_rounds < MAX_EMPTY_ROUNDS:
                want = min(BATCH_SIZE, target - len(have))

                # generate_questions truncates excludes to the first 50, so send
                # a random sample rather than the oldest 50.
                pool = list(have)
                excludes = random.sample(pool, min(50, len(pool))) if pool else []

                try:
                    batch = await generate_questions(
                        category=category,
                        difficulty=difficulty,
                        count=want,
                        topics=topic,
                        exclude_questions=excludes,
                    )
                except Exception as e:
                    async with stats.lock:
                        stats.errors += 1
                    gen_failures += 1
                    print(f"[error] {topic} d{difficulty}: {e}")
                    empty_rounds += 1
                    await asyncio.sleep(3)
                    continue

                async with stats.lock:
                    stats.calls += 1

                added = 0
                for q in batch:
                    txt = q["text"].strip()
                    nt = norm_text(txt)
                    na = norm_answer(q["correct_answer"])

                    if txt in FALLBACK_TEXTS:
                        async with stats.lock:
                            stats.rejected_fallback += 1
                        continue

                    if txt in have or nt in have_norm:
                        async with stats.lock:
                            stats.rejected_text += 1
                        continue

                    if answers[na] >= MAX_PER_ANSWER:
                        async with stats.lock:
                            stats.rejected_answer += 1
                        continue

                    # Insert inside a savepoint. If this row trips the unique
                    # index, only this row is rolled back - the rest survive.
                    try:
                        async with db.begin_nested():
                            db.add(QuestionBank(
                                topic=topic,
                                category=category,
                                difficulty=difficulty,
                                text=txt,
                                options=q["options"],
                                correct_answer=q["correct_answer"],
                            ))
                            await db.flush()
                    except IntegrityError:
                        async with stats.lock:
                            stats.rejected_db += 1
                        continue

                    have.add(txt)
                    have_norm.add(nt)
                    answers[na] += 1
                    added += 1

                await db.commit()

                if added:
                    async with stats.lock:
                        stats.inserted += added
                    empty_rounds = 0
                else:
                    empty_rounds += 1

                print(f"  {topic} d{difficulty}: +{added} -> {len(have)}/{target}")

            if len(have) >= target:
                print(f"[done] {topic} d{difficulty}: {len(have)}/{target}")
            elif gen_failures:
                print(f"[partial] {topic} d{difficulty}: stopped at {len(have)}/{target} "
                      f"after {gen_failures} generation errors - re-run to continue")
            else:
                print(f"[partial] {topic} d{difficulty}: stopped at {len(have)}/{target} "
                      f"(ran out of distinct answers - this is fine)")


# ---------------------------------------------------------------- main

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--topic", default=None)
    ap.add_argument("--target", type=int, default=TARGET_PER_PAIR)
    ap.add_argument("--concurrency", type=int, default=CONCURRENCY)
    args = ap.parse_args()

    topics = TOPICS
    if args.topic:
        topics = [t for t in TOPICS if t[0].lower() == args.topic.lower()]
        if not topics:
            print(f"Unknown topic: {args.topic}")
            print("Known:", ", ".join(t[0] for t in TOPICS))
            sys.exit(1)

    stats = Stats()
    sem = asyncio.Semaphore(args.concurrency)
    started = time.time()

    pairs = [(t, c, d) for (t, c) in topics for d in DIFFICULTIES]
    print(f"{len(pairs)} (topic, difficulty) pairs, target {args.target} each, "
          f"concurrency {args.concurrency}\n")

    await asyncio.gather(*[
        seed_pair(t, c, d, args.target, stats, sem, args.dry_run)
        for (t, c, d) in pairs
    ])

    mins = (time.time() - started) / 60
    print("\n" + "=" * 52)
    if args.dry_run:
        print(f"DRY RUN - would make ~{stats.calls} generation calls")
        print(f"estimated spend ~${stats.calls * EST_COST_PER_CALL:.2f}")
    else:
        print(f"inserted            {stats.inserted}")
        print(f"generation calls    {stats.calls}")
        print(f"rejected (fallback) {stats.rejected_fallback}")
        print(f"rejected (text)     {stats.rejected_text}")
        print(f"rejected (answer)   {stats.rejected_answer}")
        print(f"rejected (db)       {stats.rejected_db}")
        print(f"errors              {stats.errors}")
        print(f"elapsed             {mins:.1f} min")
        print(f"estimated spend     ~${stats.calls * EST_COST_PER_CALL:.2f}")
    print("=" * 52)


if __name__ == "__main__":
    asyncio.run(main())