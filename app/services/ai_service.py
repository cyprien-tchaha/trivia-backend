from openai import AsyncOpenAI
from dotenv import load_dotenv
import os
import json
import re

load_dotenv()

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

CATEGORY_PROMPTS = {
    "anime": "Japanese anime series, manga, and anime films",
    "tv": "Western TV shows, series, and television programs",
}

DIFFICULTY_DESCRIPTIONS = {
    1: "very easy. Ask about main character names, iconic catchphrases, or the most well-known plot points that anyone who watched the show would know.",
    2: "easy. Ask about supporting characters, basic plot events, and well-known facts that a casual fan would know after watching a full season.",
    3: "medium. Ask about specific episode events, character backstories, relationships between characters, and details that only someone who watched the full series would know.",
    4: "hard. Ask about obscure character names, specific episode details, minor plot points, exact quotes, and facts that only a dedicated fan who rewatched the series would know.",
    5: "extremely hard. Ask about the most obscure details — background characters, minor episode references, exact dialogue, production trivia, episode numbers, director names, and facts that only a true expert with encyclopedic knowledge would know. These questions should be very difficult even for hardcore fans.",
}

async def validate_topics(topics: str) -> dict:
    if not topics.strip():
        return {"valid": True, "corrected": "", "unknown": []}
    
    topic_list = [t.strip() for t in topics.split(",") if t.strip()]
    
    prompt = f"""You are a knowledge validator. For each title in this list, check if it is a real anime, manga, TV show, or movie.

List: {', '.join(topic_list)}

For each title:
1. If it exists, return the correctly spelled official title
2. If it is misspelled but you can identify it, return the correct spelling
3. If it does not exist or you cannot identify it, mark it as unknown

Respond ONLY with a JSON object, no other text:
{{
  "results": [
    {{"input": "original input", "found": true, "corrected": "Official Title"}},
    {{"input": "unknown show", "found": false, "corrected": ""}}
  ]
}}"""

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You validate whether TV shows and anime exist. Respond only with valid JSON."},
            {"role": "user", "content": prompt}
        ],
        temperature=0,
        max_tokens=500,
    )
    
    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"^```\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    
    data = json.loads(raw)
    results = data.get("results", [])
    
    unknown = [r["input"] for r in results if not r["found"]]
    corrected_list = [r["corrected"] for r in results if r["found"]]
    corrected = ", ".join(corrected_list)
    
    return {
        "valid": len(unknown) == 0,
        "corrected": corrected,
        "unknown": unknown,
        "found": corrected_list,
    }

async def generate_questions(
    category: str,
    difficulty: int,
    count: int = 10,
    topics: str = "",
) -> list[dict]:
    category_desc = CATEGORY_PROMPTS.get(category, "anime")
    difficulty_desc = DIFFICULTY_DESCRIPTIONS.get(difficulty, "medium difficulty")

    if topics.strip():
        topic_list = [t.strip() for t in topics.split(",") if t.strip()]
        topics_desc = ", ".join(topic_list)
        subject = f"specifically about these titles: {topics_desc}"
    else:
        subject = f"about {category_desc}"

    prompt = f"""Generate {count} trivia questions {subject}.

Difficulty level: {difficulty}/5 — {difficulty_desc}

STRICT RULES — follow these exactly:
- NEVER ask about episode numbers, episode titles, or episode counts
- NEVER ask about directors, animators, or production staff unless they are extremely famous (e.g. Hayao Miyazaki)
- NEVER ask about manga chapter numbers or volume numbers
- NEVER ask about release dates or airing schedules
- FOCUS questions on: characters, their personalities, abilities, relationships, story arcs, plot events, quotes, rivalries, transformations, factions, and world-building
- For TV shows it is acceptable to ask about real actor names or famous real-world facts about the show
- Each question must have exactly 4 answer options
- Only one answer must be correct
- Wrong answer options must be plausible — other characters or things from the same show
- Questions should be clear and unambiguous
- If multiple titles are given, spread questions evenly across all of them
- Do not repeat questions
- IMPORTANT: You MUST strictly follow the difficulty level. Level 1 = obvious to any casual viewer. Level 5 = only a superfan who has rewatched everything would know.

Respond with ONLY a JSON array, no other text, no markdown, no backticks.
Format:
[
  {{
    "text": "Question text here?",
    "options": ["Option A", "Option B", "Option C", "Option D"],
    "correct_answer": "Option A"
  }}
]"""

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "You are a trivia question generator. You respond only with valid JSON arrays containing trivia questions. Never include markdown, backticks, or any text outside the JSON array."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.8,
        max_tokens=3000,
    )

    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"^```\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    raw = raw.strip()

    questions = json.loads(raw)

    validated = []
    for q in questions:
        if (
            isinstance(q, dict)
            and "text" in q
            and "options" in q
            and "correct_answer" in q
            and len(q["options"]) == 4
            and q["correct_answer"] in q["options"]
        ):
            validated.append({
                "text": q["text"],
                "options": q["options"],
                "correct_answer": q["correct_answer"],
                "difficulty": difficulty,
                "category": category,
            })

    return validated[:count]