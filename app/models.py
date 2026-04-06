from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, JSON, Boolean
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
    id      = Column(String, primary_key=True, default=gen_uuid)
    game_id = Column(String, ForeignKey("games.id"), nullable=False)
    name    = Column(String, nullable=False)
    score   = Column(Integer, default=0)

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