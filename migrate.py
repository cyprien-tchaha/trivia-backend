import asyncio
from sqlalchemy import text
from app.database import engine

MIGRATIONS = [
    # existing
    """
    ALTER TABLE players
    ADD COLUMN IF NOT EXISTS disconnected_at TIMESTAMP WITH TIME ZONE DEFAULT NULL;
    """,

    # free-tier question bank
    """
    CREATE TABLE IF NOT EXISTS question_bank (
        id             VARCHAR PRIMARY KEY,
        topic          VARCHAR NOT NULL,
        category       VARCHAR NOT NULL DEFAULT 'anime',
        difficulty     INTEGER NOT NULL,
        text           VARCHAR NOT NULL,
        options        JSON,
        correct_answer VARCHAR NOT NULL,
        created_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_question_bank_topic_diff
    ON question_bank (topic, difficulty);
    """,

    # A global unique index on text made two difficulties of the SAME topic
    # collide while seeding concurrently. Uniqueness belongs per topic.
    """
    ALTER TABLE question_bank
    DROP CONSTRAINT IF EXISTS question_bank_text_key;
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_question_bank_topic_text
    ON question_bank (topic, text);
    """,
]


async def migrate():
    async with engine.begin() as conn:
        for sql in MIGRATIONS:
            await conn.execute(text(sql))
    print("Migration complete.")


asyncio.run(migrate())