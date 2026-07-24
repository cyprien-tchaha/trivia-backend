"""
Print questions from the bank so you can eyeball quality.

    python show_bank.py                          # counts per topic/difficulty
    python show_bank.py --topic "One Piece" -d 1 # read the d1 questions
    python show_bank.py --topic "One Piece" -d 5 --limit 10

Read-only. Never writes anything.
"""

import argparse
import asyncio

from sqlalchemy import select, func

from app.database import AsyncSessionLocal
from app.models import QuestionBank


async def summary(db):
    rows = await db.execute(
        select(
            QuestionBank.topic,
            QuestionBank.difficulty,
            func.count(QuestionBank.id),
        ).group_by(QuestionBank.topic, QuestionBank.difficulty)
         .order_by(QuestionBank.topic, QuestionBank.difficulty)
    )
    data = rows.fetchall()
    if not data:
        print("Bank is empty.")
        return

    print(f"{'topic':30} {'d':>2}  {'count':>5}")
    print("-" * 42)
    total = 0
    for topic, diff, n in data:
        print(f"{topic:30} {diff:>2}  {n:>5}")
        total += n
    print("-" * 42)
    print(f"{'TOTAL':30}     {total:>5}")


async def show(db, topic, difficulty, limit):
    stmt = select(QuestionBank).where(QuestionBank.topic == topic)
    if difficulty:
        stmt = stmt.where(QuestionBank.difficulty == difficulty)
    stmt = stmt.limit(limit)

    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        print(f"Nothing banked for {topic}" + (f" d{difficulty}" if difficulty else ""))
        return

    for i, q in enumerate(rows, 1):
        print(f"\n{i}. [d{q.difficulty}] {q.text}")
        for opt in q.options:
            mark = " <-- correct" if opt == q.correct_answer else ""
            print(f"     - {opt}{mark}")
    print(f"\n({len(rows)} shown)")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default=None)
    ap.add_argument("-d", "--difficulty", type=int, default=None)
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    async with AsyncSessionLocal() as db:
        if args.topic:
            await show(db, args.topic, args.difficulty, args.limit)
        else:
            await summary(db)


if __name__ == "__main__":
    asyncio.run(main())