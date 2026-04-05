import anthropic
import json
import re
from dotenv import load_dotenv
import os

load_dotenv()

client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

CATEGORY_PROMPTS = {
    "anime": "Japanese anime series, manga, and anime films",
    "tv": "Western TV shows, series, and television programs",
}

DIFFICULTY_DESCRIPTIONS = {
    1: """VERY EASY. Questions a first-time viewer would know after watching 1-2 episodes.
Examples of acceptable questions:
- What is the main character's name?
- What color is [character]'s hair?
- What is the name of the main group/crew/team?""",

    2: """EASY. Questions a casual fan who finished the show would know.
Examples of acceptable questions:
- What is [character]'s special ability or power?
- Who is the main villain in season 1?
- What is the relationship between [character A] and [character B]?""",

    3: """MEDIUM. Questions that require having paid close attention to the full series.
Examples of acceptable questions:
- What was [character]'s motivation for betraying the group?
- What is the name of [character]'s hometown or origin?
- What technique/move did [character] use to defeat [villain]?""",

    4: """HARD. Questions only a dedicated fan who watched everything carefully would know.
Examples of acceptable questions:
- What specific condition triggers [character]'s hidden power?
- What was the name of [minor character]'s faction or organization?
- What did [character] say to [character] before [specific plot event]?
AVOID anything a casual fan would know.""",

    5: """EXTREMELY HARD — EXPERT LEVEL ONLY. These must be genuinely difficult questions that would stump most fans.
Examples of acceptable questions:
- What is the full name of [minor supporting character]?
- What secret was revealed about [character] in the final arc that contradicts earlier events?
- What is the name of the ancient technique/artifact/location only mentioned once?
RULES FOR LEVEL 5:
- Do NOT ask about main characters' basic traits or abilities
- Do NOT ask anything that would be on the show's Wikipedia summary
- Ask about minor characters, specific dialogue, obscure lore, and details easy to miss
- Every question should make even hardcore fans think hard"""
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
- IMPORTANT: You MUST strictly follow the difficulty level
- CRITICAL: Only include facts you are 100% certain are accurate. If you are not sure about a detail, do not use it.

Respond with ONLY a JSON array, no other text, no markdown, no backticks.
Format:
[
  {{
    "text": "Question text here?",
    "options": ["Option A", "Option B", "Option C", "Option D"],
    "correct_answer": "Option A"
  }}
]"""

    response = await client.messages.create(
        model="claude-opus-4-5",
        max_tokens=4000,
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],
    )

    raw = response.content[0].text.strip()
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

    # Pass 2 — verify factual accuracy
    verified = await verify_questions(validated, topics or category_desc)
    return verified[:count]


async def verify_questions(questions: list[dict], subject: str) -> list[dict]:
    if not questions:
        return questions

    questions_text = json.dumps([{"text": q["text"], "correct_answer": q["correct_answer"]} for q in questions], indent=2)

    prompt = f"""You are a fact-checker for trivia questions about {subject}.

Here are trivia questions to verify:
{questions_text}

For each question, determine:
1. Is the correct_answer 100% verified and accurate?
2. Is the question based on something that definitively happened in the show/film (not speculation or very obscure fan theory)?

Respond with ONLY a JSON array of the question indices (0-based) that PASS verification — meaning they are factually accurate and fair. Do not include indices for questions you are uncertain about.

Example response if questions 0, 2, and 3 pass: [0, 2, 3]
Respond with only the JSON array, nothing else."""

    response = await client.messages.create(
        model="claude-opus-4-5",
        max_tokens=200,
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"^```\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    passing_indices = json.loads(raw)
    verified = [questions[i] for i in passing_indices if i < len(questions)]
    print(f"Verification: {len(verified)}/{len(questions)} questions passed")
    return verified


async def validate_topics(topics: str) -> dict:
    if not topics.strip():
        return {"valid": True, "corrected": "", "unknown": []}

    topic_list = [t.strip() for t in topics.split(",") if t.strip()]

    prompt = f"""For each title in this list, check if it is a real anime, manga, TV show, or movie.

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

    response = await client.messages.create(
        model="claude-opus-4-5",
        max_tokens=500,
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],
    )

    raw = response.content[0].text.strip()
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