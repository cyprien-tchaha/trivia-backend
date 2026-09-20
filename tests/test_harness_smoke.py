from sqlalchemy import select


async def test_harness_creates_tables_and_roundtrips(db, bank_row):
    from app.models import QuestionBank

    db.add(bank_row(text="smoke?", correct_answer="yes"))
    await db.commit()

    result = await db.execute(select(QuestionBank))
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].options == ["yes", "b", "c", "d"]  # JSON column roundtrips
