import asyncio
import sys
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import Game, Question

ANIME_QUESTIONS = [
    {
        "text": "In Naruto, what is the name of Naruto's signature jutsu?",
        "options": ["Chidori", "Rasengan", "Shadow Clone", "Eight Gates"],
        "correct_answer": "Rasengan",
        "difficulty": 1,
        "category": "anime",
    },
    {
        "text": "Which anime features the Survey Corps fighting Titans?",
        "options": ["Demon Slayer", "One Piece", "Attack on Titan", "Bleach"],
        "correct_answer": "Attack on Titan",
        "difficulty": 1,
        "category": "anime",
    },
    {
        "text": "What is the name of the main character in Death Note?",
        "options": ["Light Yagami", "L Lawliet", "Near", "Mello"],
        "correct_answer": "Light Yagami",
        "difficulty": 1,
        "category": "anime",
    },
    {
        "text": "In Dragon Ball Z, what level of Super Saiyan did Gohan first achieve?",
        "options": ["Super Saiyan 1", "Super Saiyan 2", "Super Saiyan 3", "Super Saiyan God"],
        "correct_answer": "Super Saiyan 2",
        "difficulty": 2,
        "category": "anime",
    },
    {
        "text": "What is the Straw Hat crew's ship called in One Piece?",
        "options": ["Thousand Sunny", "Going Merry", "Red Force", "Moby Dick"],
        "correct_answer": "Thousand Sunny",
        "difficulty": 2,
        "category": "anime",
    },
    {
        "text": "In Fullmetal Alchemist, what did Edward Elric sacrifice to bring his brother back?",
        "options": ["His memories", "His right arm", "His eyesight", "His soul"],
        "correct_answer": "His right arm",
        "difficulty": 2,
        "category": "anime",
    },
    {
        "text": "Which studio animated Spirited Away?",
        "options": ["Toei Animation", "Madhouse", "Studio Ghibli", "Gainax"],
        "correct_answer": "Studio Ghibli",
        "difficulty": 1,
        "category": "anime",
    },
    {
        "text": "In Hunter x Hunter, what is Gon's nen type?",
        "options": ["Manipulator", "Emitter", "Enhancer", "Transmuter"],
        "correct_answer": "Enhancer",
        "difficulty": 3,
        "category": "anime",
    },
    {
        "text": "What is the name of the sword style used by Roronoa Zoro?",
        "options": ["One Sword Style", "Two Sword Style", "Three Sword Style", "Four Sword Style"],
        "correct_answer": "Three Sword Style",
        "difficulty": 1,
        "category": "anime",
    },
    {
        "text": "In Demon Slayer, what is Tanjiro's breathing style?",
        "options": ["Water Breathing", "Flame Breathing", "Sun Breathing", "Wind Breathing"],
        "correct_answer": "Water Breathing",
        "difficulty": 2,
        "category": "anime",
    },
]

TV_QUESTIONS = [
    {
        "text": "In Breaking Bad, what is Walter White's drug pseudonym?",
        "options": ["The Cook", "Heisenberg", "Blue Sky", "Mr. White"],
        "correct_answer": "Heisenberg",
        "difficulty": 1,
        "category": "tv",
    },
    {
        "text": "What city is The Office (US) set in?",
        "options": ["Philadelphia", "Pittsburgh", "Scranton", "Allentown"],
        "correct_answer": "Scranton",
        "difficulty": 2,
        "category": "tv",
    },
    {
        "text": "In Game of Thrones, what is the sigil of House Stark?",
        "options": ["Lion", "Dragon", "Direwolf", "Stag"],
        "correct_answer": "Direwolf",
        "difficulty": 1,
        "category": "tv",
    },
    {
        "text": "How many episodes are in the first season of Stranger Things?",
        "options": ["6", "8", "10", "12"],
        "correct_answer": "8",
        "difficulty": 2,
        "category": "tv",
    },
    {
        "text": "What is the name of the coffee shop in Friends?",
        "options": ["Central Perk", "The Grind", "Java Joe's", "Perks"],
        "correct_answer": "Central Perk",
        "difficulty": 1,
        "category": "tv",
    },
    {
        "text": "In The Wire, what city does the show take place in?",
        "options": ["New York", "Chicago", "Baltimore", "Philadelphia"],
        "correct_answer": "Baltimore",
        "difficulty": 1,
        "category": "tv",
    },
    {
        "text": "Who plays Eleven in Stranger Things?",
        "options": ["Sadie Sink", "Millie Bobby Brown", "Natalia Dyer", "Finn Wolfhard"],
        "correct_answer": "Millie Bobby Brown",
        "difficulty": 1,
        "category": "tv",
    },
    {
        "text": "In Succession, what is the name of the media company?",
        "options": ["Waystar Royco", "Logan Corp", "Pierce Media", "ATN Network"],
        "correct_answer": "Waystar Royco",
        "difficulty": 2,
        "category": "tv",
    },
    {
        "text": "What is the name of the meth lab in Breaking Bad?",
        "options": ["The Lab", "The Cook", "The Superlab", "The Basement"],
        "correct_answer": "The Superlab",
        "difficulty": 3,
        "category": "tv",
    },
    {
        "text": "In The Last of Us, what fungus turns people into infected?",
        "options": ["Aspergillus", "Cordyceps", "Candida", "Fusarium"],
        "correct_answer": "Cordyceps",
        "difficulty": 2,
        "category": "tv",
    },
]

async def seed(game_code: str):
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Game).where(Game.code == game_code.upper()))
        game = result.scalar_one_or_none()
        if not game:
            print(f"Game {game_code} not found")
            return
        questions = ANIME_QUESTIONS if game.category == "anime" else TV_QUESTIONS
        for i, q in enumerate(questions[:game.question_count]):
            question = Question(
                game_id=game.id,
                text=q["text"],
                options=q["options"],
                correct_answer=q["correct_answer"],
                difficulty=q["difficulty"],
                category=q["category"],
                order_index=i,
            )
            db.add(question)
        await db.commit()
        print(f"Seeded {min(len(questions), game.question_count)} questions for game {game_code}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python seed_questions.py GAMECODE")
        sys.exit(1)
    asyncio.run(seed(sys.argv[1]))