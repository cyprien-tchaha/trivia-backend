"""
Test harness.

The app builds its SQLAlchemy engine at import time from DATABASE_URL
(app/database.py, module scope), so the environment has to be set before any
`app.*` import happens. pytest imports conftest before test modules, so setting
it here is early enough — as long as this file imports nothing from `app` at
module level.

Tests run against in-memory SQLite rather than Postgres so `pytest` needs no
Docker. StaticPool keeps every connection pointed at the same in-memory
database; without it each connection gets a private, empty one.
"""
import os

# app/database.py builds its engine at import time with Postgres-only pool
# arguments (pool_size, max_overflow, pool_timeout), which SQLite's pool
# rejects — so this URL must be a Postgres one. Nothing ever connects through
# it: create_async_engine only constructs the object. Tests use the separate
# SQLite engine built in the `db` fixture below.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
# app/services/ai_service.py constructs an Anthropic client at import time and
# rejects a missing key. No test makes a real call.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest_asyncio.fixture
async def db():
    """A clean in-memory database per test, with all tables created."""
    from app.database import Base
    import app.models  # noqa: F401 — registers the tables on Base.metadata

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
def bank_row():
    """Build a QuestionBank row with sensible defaults."""
    from app.models import QuestionBank

    def _make(topic="one piece", difficulty=1, text="q?", correct_answer="a",
              options=None, category="anime"):
        return QuestionBank(
            topic=topic,
            category=category,
            difficulty=difficulty,
            text=text,
            options=options or [correct_answer, "b", "c", "d"],
            correct_answer=correct_answer,
        )

    return _make
