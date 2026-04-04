from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import Question, Game
from app.services.ai_service import generate_questions

router = APIRouter()

async def create_ai_questions(game_id: str, category: str, difficulty: int, count: int, topics: str = ""):
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        try:
            questions = await generate_questions(category, difficulty, count, topics)
            for i, q in enumerate(questions):
                question = Question(
                    game_id=game_id,
                    text=q["text"],
                    options=q["options"],
                    correct_answer=q["correct_answer"],
                    difficulty=q["difficulty"],
                    category=q["category"],
                    order_index=i,
                )
                db.add(question)
            await db.commit()
            print(f"Generated {len(questions)} AI questions for game {game_id}")
        except Exception as e:
            print(f"AI question generation failed: {e}")
            await seed_fallback_questions(db, game_id, category, difficulty, count)

async def seed_fallback_questions(db, game_id: str, category: str, difficulty: int, count: int):
    from app.services.ai_service import CATEGORY_PROMPTS
    fallback = FALLBACK_QUESTIONS.get(category, FALLBACK_QUESTIONS["anime"])
    for i, q in enumerate(fallback[:count]):
        question = Question(
            game_id=game_id,
            text=q["text"],
            options=q["options"],
            correct_answer=q["correct_answer"],
            difficulty=difficulty,
            category=category,
            order_index=i,
        )
        db.add(question)
    await db.commit()
    print(f"Seeded {min(len(fallback), count)} fallback questions for game {game_id}")

FALLBACK_QUESTIONS = {
    "anime": [
        {"text": "In Naruto, what is the name of Naruto's signature jutsu?", "options": ["Chidori", "Rasengan", "Shadow Clone", "Eight Gates"], "correct_answer": "Rasengan"},
        {"text": "Which anime features the Survey Corps fighting Titans?", "options": ["Demon Slayer", "One Piece", "Attack on Titan", "Bleach"], "correct_answer": "Attack on Titan"},
        {"text": "What is the name of the main character in Death Note?", "options": ["Light Yagami", "L Lawliet", "Near", "Mello"], "correct_answer": "Light Yagami"},
        {"text": "In Dragon Ball Z, what level did Gohan first achieve?", "options": ["Super Saiyan 1", "Super Saiyan 2", "Super Saiyan 3", "Super Saiyan God"], "correct_answer": "Super Saiyan 2"},
        {"text": "What is the Straw Hat crew's ship in One Piece?", "options": ["Thousand Sunny", "Going Merry", "Red Force", "Moby Dick"], "correct_answer": "Thousand Sunny"},
        {"text": "Which studio animated Spirited Away?", "options": ["Toei Animation", "Madhouse", "Studio Ghibli", "Gainax"], "correct_answer": "Studio Ghibli"},
        {"text": "In Hunter x Hunter, what is Gon's nen type?", "options": ["Manipulator", "Emitter", "Enhancer", "Transmuter"], "correct_answer": "Enhancer"},
        {"text": "What sword style does Roronoa Zoro use?", "options": ["One Sword Style", "Two Sword Style", "Three Sword Style", "Four Sword Style"], "correct_answer": "Three Sword Style"},
        {"text": "In Demon Slayer, what is Tanjiro's breathing style?", "options": ["Water Breathing", "Flame Breathing", "Sun Breathing", "Wind Breathing"], "correct_answer": "Water Breathing"},
        {"text": "What is the name of the organization in Fullmetal Alchemist?", "options": ["State Alchemists", "Homunculi", "Amestris Army", "Brotherhood"], "correct_answer": "State Alchemists"},
    ],
    "tv": [
        {"text": "In Breaking Bad, what is Walter White's drug pseudonym?", "options": ["The Cook", "Heisenberg", "Blue Sky", "Mr. White"], "correct_answer": "Heisenberg"},
        {"text": "What city is The Office (US) set in?", "options": ["Philadelphia", "Pittsburgh", "Scranton", "Allentown"], "correct_answer": "Scranton"},
        {"text": "In Game of Thrones, what is the sigil of House Stark?", "options": ["Lion", "Dragon", "Direwolf", "Stag"], "correct_answer": "Direwolf"},
        {"text": "What is the name of the coffee shop in Friends?", "options": ["Central Perk", "The Grind", "Java Joe's", "Perks"], "correct_answer": "Central Perk"},
        {"text": "Who plays Eleven in Stranger Things?", "options": ["Sadie Sink", "Millie Bobby Brown", "Natalia Dyer", "Finn Wolfhard"], "correct_answer": "Millie Bobby Brown"},
        {"text": "In The Wire, what city does the show take place in?", "options": ["New York", "Chicago", "Baltimore", "Philadelphia"], "correct_answer": "Baltimore"},
        {"text": "In Succession, what is the name of the media company?", "options": ["Waystar Royco", "Logan Corp", "Pierce Media", "ATN Network"], "correct_answer": "Waystar Royco"},
        {"text": "What fungus turns people into infected in The Last of Us?", "options": ["Aspergillus", "Cordyceps", "Candida", "Fusarium"], "correct_answer": "Cordyceps"},
        {"text": "How many episodes are in Stranger Things season 1?", "options": ["6", "8", "10", "12"], "correct_answer": "8"},
        {"text": "In The Sopranos, what is Tony's last name?", "options": ["Bada", "Soprano", "Gervasi", "Aprile"], "correct_answer": "Soprano"},
    ],
}

@router.get("/{game_id}")
async def get_questions(game_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Question)
        .where(Question.game_id == game_id)
        .order_by(Question.order_index)
    )
    questions = result.scalars().all()
    return [
        {"id": q.id, "text": q.text, "options": q.options, "order_index": q.order_index}
        for q in questions
    ]

@router.post("/{game_id}/generate")
async def generate_game_questions(
    game_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Game).where(Game.id == game_id))
    game = result.scalar_one_or_none()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    existing = await db.execute(select(Question).where(Question.game_id == game_id))
    if existing.scalars().first():
        return {"status": "questions already exist"}
    background_tasks.add_task(
        create_ai_questions,
        game_id,
        game.category,
        game.difficulty,
        game.question_count,
        game.topics,
    )
    return {"status": "generating", "message": "Questions are being generated"}

@router.post("/validate-topics")
async def validate_topics_endpoint(req: dict):
    from app.services.ai_service import validate_topics
    topics = req.get("topics", "")
    if not topics.strip():
        return {"valid": True, "corrected": "", "unknown": [], "found": []}
    try:
        result = await validate_topics(topics)
        return result
    except Exception as e:
        return {"valid": True, "corrected": topics, "unknown": [], "found": []}