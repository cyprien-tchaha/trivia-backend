# Naming Conventions

## Overview

This project is Python/FastAPI with a PostgreSQL database. The convention is
simple because there is only one convention: **`snake_case` at every layer.**

## Convention Summary

| Layer | Convention | Example |
|-------|-----------|---------|
| **Database (SQL)** | `snake_case` | `correct_answer`, `game_id`, `created_at` |
| **Python code** | `snake_case` | `correct_answer`, `game_id`, `created_at` |
| **API JSON (over wire)** | `snake_case` | `"correct_answer"`, `"game_id"` |
| **Query params** | `snake_case` | `?question_id=...` |

## There Is No Conversion Layer

Python is `snake_case` natively and Pydantic serializes field names verbatim.
The database columns are `snake_case`. So a field is spelled the same in the
model, the schema, the JSON body and the SQL column — end to end, no aliasing.

**Do not add one.** No `alias_generator`, no `to_camel`, no manual conversion in
route handlers. If a frontend needs `camelCase`, that is the frontend's job.

```python
# CORRECT — one spelling, everywhere
class SubmitAnswerRequest(BaseModel):
    player_id: str
    question_id: str
    answer: str
    time_taken_ms: int

# The JSON body is exactly:
# {"player_id": "...", "question_id": "...", "answer": "A", "time_taken_ms": 4200}
```

```python
# WRONG — inventing a conversion layer this project does not have
class SubmitAnswerRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    player_id: str
```

### When to Use `Field(alias=...)`

Only when mapping an **external** API whose field names you don't control —
TMDB, Jikan, an OAuth provider. Never for this project's own endpoints.

```python
# ONLY for external APIs
class TmdbResult(BaseModel):
    poster_path: str | None = Field(default=None, alias="poster_path")
    release_date: str | None = Field(default=None, alias="release_date")
```

## Python Naming

| Thing | Convention | Example |
|---|---|---|
| Module / file | `snake_case.py` | `question_bank_service.py` |
| Function, variable, argument | `snake_case` | `draw_from_bank`, `game_code` |
| Class (models, schemas) | `PascalCase` | `QuestionBank`, `CreateGameRequest` |
| Constant | `UPPER_SNAKE` | `BANK_TOPICS`, `_CACHE_TTL_SECONDS` |
| Module-private helper | leading `_` | `_norm_answer`, `_cache_get` |

## SQL Naming

```sql
-- Tables: plural, snake_case
CREATE TABLE games (...)
CREATE TABLE question_bank (...)   -- exception: a "bank" is singular by meaning

-- Columns: snake_case
id             VARCHAR PRIMARY KEY
correct_answer VARCHAR NOT NULL
created_at     TIMESTAMPTZ DEFAULT NOW()

-- Indexes: ix_<table>_<columns>
CREATE INDEX ix_question_bank_topic_diff ON question_bank (topic, difficulty);

-- Unique constraints: uq_<table>_<columns>
CREATE UNIQUE INDEX uq_question_bank_topic_text ON question_bank (topic, text);
```

SQLAlchemy `Column` attribute names match the database column names exactly —
no `Column("correct_answer", ...)` renaming.

## Route Naming

Routers are mounted with a prefix in `main.py`; paths inside a router are
relative and `kebab-case` where multi-word.

```python
# CORRECT — matches existing routes
@router.get("/{code}/player-answer/{player_id}/{question_id}")
@router.post("/{code}/question/{index}")
@router.post("/validate-topics")

# WRONG
@router.get("/{code}/playerAnswer/{playerId}")
@router.get("/{code}/player_answer/{player_id}")   # underscores in a URL path
```

Path parameter *names* stay `snake_case` (they're Python arguments); the path
*segments* are `kebab-case`.

## Rules

### DO

1. Use `snake_case` in Python, JSON, SQL and query params — the same spelling
   in all four
2. Use `PascalCase` for classes only
3. Use `kebab-case` for multi-word URL path segments
4. Name SQLAlchemy columns exactly as the database spells them

### DON'T

1. **DON'T add a camelCase conversion layer** — no `alias_generator`, no manual
   conversion in handlers
2. **DON'T use `Field(alias=...)`** except for external API payloads
3. **DON'T use `camelCase`** anywhere in Python
4. **DON'T mix conventions within a layer**
5. **DON'T rename a column between the model and the database**

## Code Review Checklist

- [ ] No `camelCase` in Python identifiers
- [ ] No alias generator or manual case conversion on this project's own schemas
- [ ] `Field(alias=...)` only on external-API models
- [ ] Database columns and tables are `snake_case`
- [ ] SQLAlchemy `Column` names match the database exactly
- [ ] New endpoint takes a Pydantic schema, not a bare `req: dict`
