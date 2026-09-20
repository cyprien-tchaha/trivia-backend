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
                → broadcast to the room
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
| Free-tier bank matching/drawing (UNWIRED) | `app/services/question_bank_service.py` |
| In-process socket rooms | `app/websocket/manager.py` |

## Data model

`Game` (6-char `code`, `status` lobby→active→finished, `current_question_index`)
owns `Player`, `Question`, `Answer`. `QuestionBank` is standalone: pre-generated
rows keyed by `(topic, difficulty)`, copied into `Question` rows on draw.

IDs are `String` UUIDs generated in Python via `gen_uuid()`, not native Postgres
`uuid` columns.

## Game loop ownership

**The host client drives the loop, not the server.** There is no server-side
timer. The client POSTs `/api/games/{code}/question/{index}` to advance; the
server persists the index and broadcasts. The WebSocket endpoint in `main.py` is
a dumb relay that re-broadcasts what clients send — the REST endpoints are the
source of truth. Moving the loop server-side is a redesign, not a refactor.

## External services

- Anthropic (`AsyncAnthropic`) — question generation, topic validation, commentary
- TMDB — movie/TV title autocomplete (needs `TMDB_API_KEY`)
- Jikan — anime title autocomplete (no key)

Redis and OpenAI are in `requirements.txt` and Redis is in `docker-compose.yml`,
but **no code imports either**. There is no cache layer.
