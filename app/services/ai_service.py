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
    exclude_questions: list[str] = [],
) -> list[dict]:
    category_desc = CATEGORY_PROMPTS.get(category, "anime")
    difficulty_desc = DIFFICULTY_DESCRIPTIONS.get(difficulty, "medium difficulty")

    if topics.strip():
        topic_list = [t.strip() for t in topics.split(",") if t.strip()]
        topics_desc = ", ".join(topic_list)
        subject = f"specifically about these titles: {topics_desc}"
    else:
        subject = f"about {category_desc}"

    # Build exclusion block
    exclusion_block = ""
    if exclude_questions:
        exclusion_list = "\n".join(f"- {q}" for q in exclude_questions[:50])
        exclusion_block = f"""
PREVIOUSLY ASKED QUESTIONS — DO NOT REPEAT OR CLOSELY PARAPHRASE ANY OF THESE:
{exclusion_list}

You MUST generate entirely new questions that test different facts, characters, or events.
"""

    all_verified = []
    attempts = 0
    max_attempts = 3

    while len(all_verified) < count and attempts < max_attempts:
        needed = count - len(all_verified)
        request_count = needed + max(3, needed // 2)

        prompt = f"""Generate {request_count} trivia questions {subject}.

Difficulty level: {difficulty}/5 — {difficulty_desc}
{exclusion_block}
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
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"^```\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        raw = raw.strip()

        questions = json.loads(raw)

        valid = []
        existing_texts = {q["text"] for q in all_verified}
        for q in questions:
            if (
                isinstance(q, dict)
                and "text" in q
                and "options" in q
                and "correct_answer" in q
                and len(q["options"]) == 4
                and q["correct_answer"] in q["options"]
                and q["text"] not in existing_texts
            ):
                valid.append({
                    "text": q["text"],
                    "options": q["options"],
                    "correct_answer": q["correct_answer"],
                    "difficulty": difficulty,
                    "category": category,
                })
                existing_texts.add(q["text"])

        verified_batch = await verify_questions(valid, topics or category_desc)
        all_verified.extend(verified_batch)
        attempts += 1
        print(f"Attempt {attempts}: {len(verified_batch)}/{len(valid)} passed, total: {len(all_verified)}/{count}")

    if len(all_verified) < count:
        print(f"Warning: only {len(all_verified)} verified questions, filling with fallback")
        fallback = _get_fallback_questions(category, difficulty)
        existing_texts = {q["text"] for q in all_verified}
        for q in fallback:
            if len(all_verified) >= count:
                break
            if q["text"] not in existing_texts:
                all_verified.append(q)

    return all_verified[:count]


def _get_fallback_questions(category: str, difficulty: int) -> list[dict]:
    fallback = {
        "anime": [
            {"text": "In Naruto, what is the name of Naruto's signature jutsu?", "options": ["Chidori", "Rasengan", "Shadow Clone", "Eight Gates"], "correct_answer": "Rasengan", "difficulty": difficulty, "category": category},
            {"text": "Which anime features the Survey Corps fighting Titans?", "options": ["Demon Slayer", "One Piece", "Attack on Titan", "Bleach"], "correct_answer": "Attack on Titan", "difficulty": difficulty, "category": category},
            {"text": "What is the name of the main character in Death Note?", "options": ["Light Yagami", "L Lawliet", "Near", "Mello"], "correct_answer": "Light Yagami", "difficulty": difficulty, "category": category},
            {"text": "Which studio animated Spirited Away?", "options": ["Toei Animation", "Madhouse", "Studio Ghibli", "Gainax"], "correct_answer": "Studio Ghibli", "difficulty": difficulty, "category": category},
            {"text": "What sword style does Roronoa Zoro use?", "options": ["One Sword Style", "Two Sword Style", "Three Sword Style", "Four Sword Style"], "correct_answer": "Three Sword Style", "difficulty": difficulty, "category": category},
        ],
        "tv": [
            {"text": "In Breaking Bad, what is Walter White's drug pseudonym?", "options": ["The Cook", "Heisenberg", "Blue Sky", "Mr. White"], "correct_answer": "Heisenberg", "difficulty": difficulty, "category": category},
            {"text": "What city is The Office (US) set in?", "options": ["Philadelphia", "Pittsburgh", "Scranton", "Allentown"], "correct_answer": "Scranton", "difficulty": difficulty, "category": category},
            {"text": "In Game of Thrones, what is the sigil of House Stark?", "options": ["Lion", "Dragon", "Direwolf", "Stag"], "correct_answer": "Direwolf", "difficulty": difficulty, "category": category},
            {"text": "What is the name of the coffee shop in Friends?", "options": ["Central Perk", "The Grind", "Java Joe's", "Perks"], "correct_answer": "Central Perk", "difficulty": difficulty, "category": category},
            {"text": "Who plays Eleven in Stranger Things?", "options": ["Sadie Sink", "Millie Bobby Brown", "Natalia Dyer", "Finn Wolfhard"], "correct_answer": "Millie Bobby Brown", "difficulty": difficulty, "category": category},
        ],
    }
    return fallback.get(category, fallback["anime"])

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

async def generate_commentary(
    question_text: str,
    correct_answer: str,
    topics: str,
    correct_count: int,
    total_count: int,
) -> str:
    subject = topics.strip() if topics.strip() else "this show"

    prompt = f"""You are the host of a live trivia game about {subject}. A round just ended.

Question: {question_text}
Correct answer: {correct_answer}
Players who got it right: {correct_count} out of {total_count}

Write ONE short, punchy commentary line reacting to the results. Rules:
- Max 12 words
- Slightly savage, like a sports commentator or a Reddit comment
- Reference the show or topic if it makes the line funnier
- React to the score — brutal if nobody got it, impressed if everyone did, spicy if it split the room
- No emojis, no hashtags, no quotation marks
- Return only the single line, nothing else"""

    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=60,
        messages=[{"role": "user", "content": prompt}],
    )

    return response.content[0].text.strip()