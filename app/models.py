from sqlalchemy import (
    Column, String, Integer, DateTime, ForeignKey, JSON, Boolean, UniqueConstraint
)
from sqlalchemy.sql import func
from app.database import Base
import uuid

def gen_uuid():
    return str(uuid.uuid4())

class Game(Base):
    __tablename__ = "games"
    id                     = Column(String, primary_key=True, default=gen_uuid)
    code                   = Column(String(6), unique=True, nullable=False, index=True)
    host_name              = Column(String, nullable=False)
    status                 = Column(String, default="lobby")
    category               = Column(String, default="anime")
    difficulty             = Column(Integer, default=1)
    topics                 = Column(String, default="")
    question_count         = Column(Integer, default=10)
    current_question_index = Column(Integer, default=0)
    created_at             = Column(DateTime(timezone=True), server_default=func.now())

class Player(Base):
    __tablename__ = "players"
    id              = Column(String, primary_key=True, default=gen_uuid)
    game_id         = Column(String, ForeignKey("games.id"), nullable=False)
    name            = Column(String, nullable=False)
    score           = Column(Integer, default=0)
    disconnected_at = Column(DateTime(timezone=True), nullable=True, default=None)

class Question(Base):
    __tablename__ = "questions"
    id             = Column(String, primary_key=True, default=gen_uuid)
    game_id        = Column(String, ForeignKey("games.id"), nullable=False)
    text           = Column(String, nullable=False)
    options        = Column(JSON)
    correct_answer = Column(String, nullable=False)
    difficulty     = Column(Integer, default=1)
    category       = Column(String, default="anime")
    order_index    = Column(Integer, default=0)

class Answer(Base):
    __tablename__ = "answers"
    id          = Column(String, primary_key=True, default=gen_uuid)
    game_id     = Column(String, ForeignKey("games.id"), nullable=False)
    player_id   = Column(String, ForeignKey("players.id"), nullable=False)
    question_id = Column(String, ForeignKey("questions.id"), nullable=False)
    answer      = Column(String, nullable=False)
    correct     = Column(Boolean, default=False)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

class QuestionBank(Base):
    """
    Pre-generated questions for the free tier.

    Unlike Question, these are not tied to a game. When a free game starts we
    sample rows matching (topic, difficulty) and COPY them into Question rows
    for that game, so the rest of the game flow is unchanged.

    Uniqueness is per TOPIC, not global. A global unique index on text would
    make two difficulties of the same topic collide with each other when they
    are seeded concurrently, which is not a real duplicate problem.
    """
    __tablename__ = "question_bank"
    __table_args__ = (
        UniqueConstraint("topic", "text", name="uq_question_bank_topic_text"),
    )

    id             = Column(String, primary_key=True, default=gen_uuid)
    topic          = Column(String, nullable=False, index=True)
    category       = Column(String, nullable=False, default="anime")
    difficulty     = Column(Integer, nullable=False, index=True)
    text           = Column(String, nullable=False)
    options        = Column(JSON)
    correct_answer = Column(String, nullable=False)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())