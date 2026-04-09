from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")

# Railway provides postgresql:// but we need postgresql+asyncpg://
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,          # was True — logging every query tanks performance under load
    pool_size=10,        # number of persistent connections in the pool
    max_overflow=20,     # extra connections allowed beyond pool_size under burst load
    pool_timeout=30,     # seconds to wait for a connection before raising an error
    pool_recycle=1800,   # recycle connections every 30 min to avoid Railway's idle timeout
    pool_pre_ping=True,  # test connections before using them to avoid stale connection errors
)

AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)
Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()