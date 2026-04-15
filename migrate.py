import asyncio
from app.database import engine

async def migrate():
    async with engine.begin() as conn:
        # Add disconnected_at column if it doesn't exist
        await conn.execute(
            __import__('sqlalchemy').text("""
                ALTER TABLE players 
                ADD COLUMN IF NOT EXISTS disconnected_at TIMESTAMP WITH TIME ZONE DEFAULT NULL;
            """)
        )
        print("Migration complete.")

asyncio.run(migrate())