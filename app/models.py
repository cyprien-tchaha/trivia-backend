from sqlalchemy import (
    Column, String, Integer, DateTime, ForeignKey, JSON, Boolean, UniqueConstraint
)
from sqlalchemy.sql import func
from app.database import Base
import uuid

def gen_uuid():
    return str(uuid.uuid4())

class User(Base):
    """
    A host. Players are deliberately not users — joining a game takes a code
    and a nickname and nothing else, because making people sign up to answer
    trivia at a party is how you lose the party.

    Identity comes from Google, so there is no password here to store, leak or
    reset.
    """
    __tablename__ = "users"
    id            = Column(String, primary_key=True, default=gen_uuid)
    #: Google's stable subject id. The email can change; this cannot, so it is
    #: what we match on.
    google_sub    = Column(String, unique=True, nullable=False, index=True)
    email         = Column(String, nullable=False, index=True)
    name          = Column(String, nullable=True)
    picture_url   = Column(String, nullable=True)
    #: "free" or "pro". Free hosts play banked topics, which cost nothing to
    #: serve; pro hosts can generate questions for any title, which does.
    plan          = Column(String, nullable=False, default="free")
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    last_login_at = Column(DateTime(timezone=True), nullable=True)


class Game(Base):
    __tablename__ = "games"
    id                     = Column(String, primary_key=True, default=gen_uuid)
    code                   = Column(String(6), unique=True, nullable=False, index=True)
    host_name              = Column(String, nullable=False)
    #: Null for games created without signing in. Those still work — anonymous
    #: hosting is the free tier, not a degraded state.
    user_id                = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    status                 = Column(String, default="lobby")
    category               = Column(String, default="anime")
    difficulty             = Column(Integer, default=1)
    topics                 = Column(String, default="")
    question_count         = Column(Integer, default=10)
    current_question_index = Column(Integer, default=0)
    # Server-owned clock. `phase` is "question" or "result"; `phase_ends_at`
    # is when the server should move the game on. Null means no server
    # deadline — a game created before this column existed, or one still in
    # the lobby — and the loop leaves those alone so the host stays in
    # control of them.
    phase                  = Column(String, default="question")
    phase_ends_at          = Column(DateTime(timezone=True), nullable=True, default=None)
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