# Architecture

FastAPI + async SQLAlchemy + Postgres. 15 Python files, ~2,150 lines.
Flat and layered: routers query models directly. **No repository layer** — that
is the convention, not an omission.

```
HTTP   Client → main.py → app/routers/{games,questions,search}.py
                            → get_db() AsyncSession → app/models.py → Postgres
                            → app/services/{ai_service,question_bank_service}.py
                              → Anthropic / TMDB / Jikan

WS     Client → /api/games/{code}/ws → ConnectionManager.rooms{code: [sockets]}
                → broadcast → Redis pub/sub → every instance's subscriber
                → each writes to its own local sockets
```

## Where things live

| Concern | File |
|---|---|
| App wiring, CORS, WebSocket endpoint | `main.py` |
| Engine, pool, `get_db()` | `app/database.py` |
| All ORM models | `app/models.py` |
| Request schemas (incomplete — see gotchas) | `app/schemas.py` |
| Game lifecycle, join, answer, resume, admin | `app/routers/games.py` (429 ln, largest) |
| Question generation, commentary, fallbacks | `app/routers/questions.py` |
| TMDB/Jikan title autocomplete proxy | `app/routers/search.py` |
| Anthropic prompts + generation loop | `app/services/ai_service.py` |
| Free-tier bank matching/drawing | `app/services/question_bank_service.py` |
| Socket rooms + Redis fanout | `app/websocket/manager.py` |

## Data model

`Game` (6-char `code`, `status` lobby→active→finished, `current_question_index`)
owns `Player`, `Question`, `Answer`. `QuestionBank` is standalone: pre-generated
rows keyed by `(topic, difficulty)`, copied into `Question` rows on draw.

IDs are `String` UUIDs generated in Python via `gen_uuid()`, not native Postgres
`uuid` columns.

## Game loop ownership

**The server drives the loop** (`app/game_loop.py`), and the host's controls
are an override rather than the mechanism.

Every instance ticks once a second, selects active games whose `phase_ends_at`
has passed, and moves them along: question → result → next question →
finished. Which instance acts is decided by `manager.claim_once()` keyed on
`(game, phase, index)`, so a move happens exactly once no matter how many
instances tick together.

The host POSTing `/api/games/{code}/question/{index}` still advances the game
and simply beats the clock to that transition, so the existing frontend needs
no change. A game with `phase_ends_at IS NULL` is ignored by the clock and
stays host-driven — that is how games in flight during the deploy were left
alone.

The WebSocket endpoint in `main.py` is still a dumb relay for client-sent
events; the REST endpoints and the clock are the source of truth.

## Question sources

`create_ai_questions` (a background task) tries the bank first via `try_bank()`,
then live AI generation, then a hardcoded fallback. See gotchas.md for the
all-or-nothing rule and the category invariant.

## External services

- Anthropic (`AsyncAnthropic`) — question generation, topic validation, commentary
- TMDB — movie/TV title autocomplete (needs `TMDB_API_KEY`)
- Jikan — anime title autocomplete (no key)

Redis and OpenAI are in `requirements.txt` and Redis is in `docker-compose.yml`,
but **no code imports either**. There is no cache layer.
