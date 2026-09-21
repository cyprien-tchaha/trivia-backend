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

    # Server-side game clock. Nullable with no backfill on purpose: an
    # in-flight game gets a null deadline, the loop ignores it, and the host
    # keeps driving it to the end. Only games started after this deploy are
    # server-driven.
    """
    ALTER TABLE games
    ADD COLUMN IF NOT EXISTS phase VARCHAR DEFAULT 'question';
    """,
    """
    ALTER TABLE games
    ADD COLUMN IF NOT EXISTS phase_ends_at TIMESTAMP WITH TIME ZONE DEFAULT NULL;
    """,
    # The loop polls on exactly this predicate every tick.
    """
    CREATE INDEX IF NOT EXISTS ix_games_active_deadline
    ON games (status, phase_ends_at)
    WHERE status = 'active';
    """,

    # Host accounts. Players stay anonymous.
    """
    CREATE TABLE IF NOT EXISTS users (
        id            VARCHAR PRIMARY KEY,
        google_sub    VARCHAR NOT NULL UNIQUE,
        email         VARCHAR NOT NULL,
        name          VARCHAR,
        picture_url   VARCHAR,
        plan          VARCHAR NOT NULL DEFAULT 'free',
        created_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        last_login_at TIMESTAMP WITH TIME ZONE
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_users_email ON users (email);
    """,
    # Nullable with no backfill: every existing game was hosted anonymously
    # and stays that way. Anonymous hosting is the free tier, not a gap.
    """
    ALTER TABLE games
    ADD COLUMN IF NOT EXISTS user_id VARCHAR REFERENCES users(id);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_games_user_id ON games (user_id);
    """,
]


async def migrate():
    async with engine.begin() as conn:
        for sql in MIGRATIONS:
            await conn.execute(text(sql))
    print("Migration complete.")


asyncio.run(migrate())