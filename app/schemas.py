from pydantic import BaseModel

class CreateGameRequest(BaseModel):
    host_name: str
    category: str = "anime"
    difficulty: int = 1
    question_count: int = 10
    topics: str = ""

class JoinGameRequest(BaseModel):
    player_name: str

class SubmitAnswerRequest(BaseModel):
    player_id: str
    question_id: str
    answer: str
    time_taken_ms: int