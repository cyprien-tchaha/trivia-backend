# trivia-backend

Real-time multiplayer trivia game API. Players join a game with a 6-character
code, answer AI-generated questions about anime / TV / movies, and see scores
update live over a WebSocket.

## Stack

| Layer | Choice |
|---|---|
| Framework | FastAPI 0.135 (async) |
| Server | uvicorn |
| ORM | SQLAlchemy 2.0, **async** (`AsyncSession` + asyncpg) |
| Validation | Pydantic v2 |
| Database | PostgreSQL |
| Realtime | FastAPI WebSockets, in-process connection manager |
| AI | Anthropic SDK (`AsyncAnthropic`) |
| External APIs | TMDB (movies/TV), Jikan (anime) |
| Deploy | Railway (nixpacks) |

`redis`, `openai` and `alembic` are in `requirements.txt` but **unused** — no
code imports them. Don't assume Redis is available; there is no cache layer.

## Commands

```bash
# Local dependencies (Postgres on 5432, Redis on 6379 — Redis is unused)
docker compose up -d

# Run the API (reload enabled)
python main.py
# or
uvicorn main:app --reload --port 8000

# Schema
python create_tables.py     # create all tables from models.py
python migrate.py           # apply the hand-written migration list

# Question bank (free tier) — costs Anthropic tokens
python seed_bank.py --dry-run        # show what it would generate, spend nothing
python seed_bank.py --topic "Naruto"
python seed_bank.py --target 20
python show_bank.py                  # inspect what's banked

python seed_questions.py <game_id>   # seed questions for one game
```

There is **no test suite and no test runner.** See "Known gaps" below.

## Environment

| Var | Required | Used by |
|---|---|---|
| `DATABASE_URL` | yes | `app/database.py` |
| `ANTHROPIC_API_KEY` | yes | `app/services/ai_service.py` |
| `TMDB_API_KEY` | no | `app/routers/search.py` (movies/TV autocomplete; anime uses Jikan, no key) |

`app/database.py` rewrites `postgresql://` and `postgres://` to
`postgresql+asyncpg://` because Railway hands out the sync form.

## Architecture

Flat and layered. Routers talk to the database directly — **there is no
repository layer**, and adding one is not the convention here.

```
HTTP   Client → main.py → app/routers/{games,questions,search}.py
                            → get_db() AsyncSession → app/models.py → Postgres
                            → app/services/{ai_service,question_bank_service}.py
                              → Anthropic / TMDB / Jikan

WS     Client → /api/games/{code}/ws → ConnectionManager.rooms{code: [sockets]}
                → broadcast to everyone in the room
```

**Data model:** `Game` (6-char `code`, `status` lobby→active→finished,
`current_question_index`) owns `Player`, `Question` and `Answer`. `QuestionBank`
is standalone — pre-generated rows keyed by `(topic, difficulty)`, copied into
`Question` rows when a game draws from it.

All IDs are `String` UUIDs generated in Python (`gen_uuid()`), not native
Postgres `uuid`.

## Design decisions that are deliberate — don't "fix" these

- **The host client drives the game loop, not the server.** There is no
  server-side timer. Clients POST `/api/games/{code}/question/{index}` to
  advance, and the server only persists the index and broadcasts. Moving the
  loop server-side is a real redesign, not a refactor.
- **The WebSocket endpoint is a dumb relay.** `main.py` re-broadcasts what
  clients send, with light per-event shaping. It is not the source of truth —
  the REST endpoints are.
- **`POST /{code}/leave` intentionally does nothing destructive.** It broadcasts
  `player_left` and stops. An earlier version voided the player's answer, which
  caused premature `all_answered` and showed reconnecting players a phantom
  "wrong" result. A refresh is indistinguishable from a disconnect server-side.
- **The search proxy fails soft** — upstream timeout or error returns HTTP 200
  with an empty list, so autocomplete degrades to "no suggestions" instead of an
  error toast.
- **Question-bank uniqueness is per-topic, not global.** A global unique index on
  `text` made two difficulties of the same topic collide during concurrent
  seeding. The constraint is `(topic, text)`.
- **Bank de-duplication keys on the *answer*, not the question text.** "What is
  Luffy's dream?" and "What does Luffy want to become?" share almost no words but
  the same answer. Normalise the answer and compare that.
- **`app/database.py` sets `echo=False` on purpose** — query logging tanked
  performance under load. The pool settings above it are tuned for Railway's
  idle timeout.

## Conventions

- **`snake_case` everywhere** — Python, JSON over the wire, and SQL columns all
  match. There is no case-conversion layer and none is wanted. Field names in
  Pydantic models are the JSON field names.
- **Async all the way down.** Every route handler is `async def`; every DB call
  is `await db.execute(...)`. Never use a sync SQLAlchemy session.
- **Timestamps are `TIMESTAMPTZ`** — `DateTime(timezone=True)` in models,
  `server_default=func.now()`. Store UTC, let the client format.
- **Route bodies:** prefer a Pydantic model from `app/schemas.py`. Several
  existing endpoints take a bare `req: dict` — that's drift, not the standard.
  New endpoints get a schema.
- **Logging is `print()` with a bracketed tag** — `[WS-CONNECT]`, `[ANSWER]`,
  `[RESUME]`, `[ADVANCE]`. Match that style rather than introducing `logging`
  piecemeal; if you switch to `logging`, switch all of it.
- **Game codes are uppercase.** Always `code.upper()` before comparing or using
  as a room key.

## Known gaps

Real, and worth knowing before you touch nearby code:

- **No tests.** Scoring (`100 + speed_bonus`), the duplicate-answer guard, and
  the `all_answered` counting race are the parts that most need them.
- **`app/services/question_bank_service.py` is not wired in.** It's complete
  (175 lines) and the `question_bank` table is seeded, but nothing imports it,
  so every game falls through to paid live AI generation. Connecting it to
  `create_ai_questions` in `app/routers/questions.py` is the free-tier feature.
- **`app/services/game_service.py` is an empty file.**
- **Migrations are hand-written SQL** in `migrate.py`, applied top to bottom on
  every run via `IF NOT EXISTS`. Alembic is installed but not initialised. Add
  new DDL to that list, idempotently.
- **`requirements.txt` is UTF-16 LE with CRLF** (written by PowerShell). pip
  handles the BOM, but it's unreadable in diffs and breaks non-pip tooling.
- **CORS is `allow_origins=["*"]`**, and `/{code}/admin`, `/{code}/reset` and
  `/{code}/players/{id}/remove` have no authentication — anyone holding a game
  code can reset a live game.

## Git

Feature branches off `main`. See `.claude/CLAUDE.md` for the Spartan workflow
commands available in this repo.
