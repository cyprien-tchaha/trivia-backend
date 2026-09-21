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
| Realtime | FastAPI WebSockets + Redis pub/sub fanout across instances |
| AI | Anthropic SDK (`AsyncAnthropic`) |
| Auth | Google OAuth for hosts; JWT sessions (PyJWT, HS256) |
| External APIs | TMDB (movies/TV), Jikan (anime) |
| Deploy | Railway (nixpacks) |

`openai` and `alembic` are in `requirements.txt` but **unused** — no code
imports them. `redis` backs WebSocket fanout (see below); it is not a cache,
and nothing else uses it.

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

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest                      # 120 tests; needs a local redis for the fanout suite
```

## Environment

| Var | Required | Used by |
|---|---|---|
| `DATABASE_URL` | yes | `app/database.py` |
| `SECRET_KEY` | yes (for auth) | `app/auth.py`. Signs host sessions. Absent, signing **refuses** rather than falling back to a default — a predictable key lets anyone mint a session for any account. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI` | for sign-in | `app/routers/auth.py`. Without them `/api/auth/google/*` returns 503 and everything else still works. |
| `FRONTEND_URL` | for sign-in | Where the OAuth callback sends the host back to. |
| `ENFORCE_ENTITLEMENTS` | no | `app/entitlements.py`, **default off**. See the entitlements note below. Do not turn on before billing ships. |
| `ANTHROPIC_API_KEY` | yes | `app/services/ai_service.py` |
| `TMDB_API_KEY` | no | `app/routers/search.py` (all three categories; anime is filtered out of TMDB by genre + language) |
| `QUESTION_SECONDS` | no | `app/game_loop.py`, default 60. Matches the countdown the client renders. |
| `RESULT_SECONDS` | no | `app/game_loop.py`, default 8. How long the answer reveal holds. |
| `GAME_TICK_SECONDS` | no | `app/game_loop.py`, default 1.0. Bounds how late a transition can be. |
| `REDIS_URL` | no | `app/websocket/manager.py`. Unset means single-instance mode: broadcasts stay in-process and the service **must not** be scaled past one replica. |

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
                → broadcast publishes to Redis → every instance's subscriber
                → each delivers to its own local sockets
```

**Data model:** `User` is a **host** account (Google-backed). `Game` (6-char `code`, `status` lobby→active→finished,
`current_question_index`, plus `phase`/`phase_ends_at` for the server clock) owns `Player`, `Question` and `Answer`, and carries a nullable `user_id` for the host who created it. `QuestionBank`
is standalone — pre-generated rows keyed by `(topic, difficulty)`, copied into
`Question` rows when a game draws from it.

All IDs are `String` UUIDs generated in Python (`gen_uuid()`), not native
Postgres `uuid`.

## Design decisions that are deliberate — don't "fix" these

- **Players are not users; only hosts have accounts.** Joining takes a code
  and a nickname. Requiring a signup to answer trivia at a party is how you
  lose the party, and `Player` is anonymous by design.
- **Hosting anonymously is the free tier, not a degraded state.**
  `games.user_id` is nullable and `current_user_optional` is the default
  dependency. What gates a game is the topic, not whether anyone signed in.
- **`ENFORCE_ENTITLEMENTS` defaults to off and must stay off until billing
  ships.** The check is written and tested so the flip is one variable, but
  enforcing a paywall with no way to pay would take a working feature away
  from every existing host and offer nothing back.
- **The free/paid line is the cost line.** `entitlements.topic_is_free()`
  calls the same `match_bank_topic()` the generator uses to decide bank vs
  AI — so what we charge for and what actually costs us cannot drift apart.
  There is a test asserting they agree.
- **Users are matched on Google's `sub`, never on email.** An email can be
  changed or reassigned; matching on it is how one person ends up inside
  another person's account.
- **The Google `id_token` signature is not verified, deliberately.** It comes
  straight back from Google's token endpoint over TLS authenticated with our
  client secret, so provenance is established by the channel — Google
  documents this case. `aud` and `iss` are still checked. That reasoning does
  **not** extend to an `id_token` supplied by a client: if one is ever
  accepted from a request body, it must be verified against Google's JWKS.

- **The server owns the game clock; the host's controls are an override.**
  `app/game_loop.py` ticks once a second on every instance, finds active games
  whose `phase_ends_at` has passed, and moves them on — question → result →
  next question → finished. The host POSTing
  `/api/games/{code}/question/{index}` still works and simply beats the clock
  to that transition. This replaced a purely host-driven loop where closing
  the host's tab froze the game permanently for everyone else.
- **Transitions are coordinated by a claim, not a leader.** Every instance
  ticks, and `manager.claim_once()` keys on `(game, phase, index)` — so a
  given move happens exactly once however many instances try it. There is no
  leader to elect and no split-brain; a crash mid-transition costs one TTL
  before another instance retries. Don't replace this with leader election.
- **A null `phase_ends_at` means the clock leaves the game alone.** Games
  already in flight when the clock shipped have no deadline and stay
  host-driven to the end, rather than having a timer appear under their
  players. `/start` and `/reset` set the deadline; `/finish` clears it.
- **Broadcasts go through Redis, never straight to local sockets.** A socket
  lives in one process, so writing locally reaches only the players who share
  a process with the publisher — which capped the service at one instance.
  `broadcast()` publishes and the subscriber delivers, on every instance
  including the publisher's, so nobody gets an event twice. With no
  `REDIS_URL`, or Redis down, it falls back to local delivery: correct for one
  instance and better than a game that stops working because the cache is
  down. `GET /health` reports `ws_fanout` so a misconfigured `REDIS_URL`
  doesn't silently re-impose the one-instance cap.
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

## Question generation

`POST /api/questions/{game_id}/generate` queues `create_ai_questions` as a
background task. It tries three sources in order:

1. **Question bank** — `try_bank()` serves the whole game from pre-generated
   rows when the game's topic is banked and holds at least `question_count`
   rows at that difficulty. All-or-nothing: a thin bank falls through rather
   than topping up from the AI, because AI questions never pass the
   distinct-correct-answer filter that `draw_from_bank` guarantees.
2. **Live AI generation** — the paid path, excluding the 50 most recently asked
   questions for the same topic/difficulty/category.
3. **Hardcoded fallback** — a small static set, so a game is never left empty.

Stored `Question.category` and `Question.difficulty` always come from the
**game**, never from the source row, so they can't diverge from `Game`. Seed
the bank with `python seed_bank.py`; inspect it with `python show_bank.py`.

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

- **No billing yet.** Accounts, plans and the entitlement check exist; there
  is no payment integration, so nothing can move a user from `free` to `pro`
  except a manual `UPDATE users SET plan='pro'`.
- **Test coverage is uneven.** 120 tests, covering the question bank, search,
  WebSocket fanout, the game clock and auth. Scoring
  (`100 + speed_bonus`), the duplicate-answer guard in `/answer`, and the
  `all_answered` counting race are still untested and are the parts that most
  need it.
- **`app/services/game_service.py` is an empty file.**
- **Migrations are hand-written SQL** in `migrate.py`, applied top to bottom on
  every run via `IF NOT EXISTS`. Alembic is installed but not initialised. Add
  new DDL to that list, idempotently.
- **CORS is `allow_origins=["*"]`**, and `/{code}/admin`, `/{code}/reset` and
  `/{code}/players/{id}/remove` have no authentication — anyone holding a game
  code can reset a live game.

## Git

Feature branches off `main`. See `.claude/CLAUDE.md` for the Spartan workflow
commands available in this repo.
