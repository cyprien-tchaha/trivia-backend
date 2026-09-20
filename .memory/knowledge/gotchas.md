# Gotchas

Things that look wrong but are deliberate, and things that are genuinely wrong.

## Deliberate — don't "fix" these

- **`POST /{code}/leave` does nothing destructive.** It broadcasts `player_left`
  and returns. An earlier version voided the player's answer; that caused
  premature `all_answered` and showed reconnecting players a phantom "wrong"
  result for a question they never answered. A refresh is indistinguishable from
  a disconnect server-side. The host's 60s timer is the backstop.
- **Question-bank uniqueness is `(topic, text)`, not global on `text`.** A global
  unique index made two difficulties of the *same* topic collide during
  concurrent seeding — which is not a real duplicate.
- **Bank de-duplication keys on the answer, not the question text.** "What is
  Luffy's dream?" and "What does Luffy want to become?" share almost no words but
  the same answer. `_norm_answer()` strips leading filler ("to become", "the")
  before comparing. The per-game guarantee is enforced at *draw* time over ~10
  questions, not by keeping all ~60 banked rows mutually distinct.
- **The search proxy returns HTTP 200 with an empty list on upstream failure.**
  Autocomplete degrades to "no suggestions" rather than an error toast. Correct
  tradeoff for a type-ahead.
- **`echo=False` on the engine is intentional** — query logging tanked
  performance under load. Pool settings (`pool_recycle=1800`, `pool_pre_ping`)
  are tuned for Railway's idle timeout.
- **`app/database.py` rewrites the DB URL scheme** because Railway hands out
  `postgresql://` and asyncpg needs `postgresql+asyncpg://`.

## Genuinely wrong / incomplete

- **`app/services/question_bank_service.py` is dead code.** 175 lines, complete,
  with a seeded table behind it — and **nothing imports it**. The free-tier bank
  path was built but never wired into `create_ai_questions` in
  `app/routers/questions.py`. Every game currently pays for live AI generation.
  Wiring it up is the highest-value small task in the repo.
- **`app/services/game_service.py` is a 0-byte file.**
- **No tests, no test runner.** The risky logic: scoring (`100 + speed_bonus`),
  the duplicate-answer guard in `/answer`, and the `all_answered` counting race.
- **`all_answered` counts every `Player` row as active.** A player who left is
  still counted (by design — see `/leave` above), so `all_answered` only fires
  once everyone including the departed answers, or never. The host timer covers
  it. Worth knowing before touching that block.
- **Schema drift.** `SubmitAnswerRequest` exists in `app/schemas.py` but
  `/answer`, `/validate-topics`, `/report` and `/commentary` all take a bare
  `req: dict` with no validation.
- **`POST /{question_id}/report` doesn't persist anything** — it `print()`s and
  returns `{"status": "reported"}`. The comment says "we'll add the column later".
- **Migrations are a hand-written list** in `migrate.py`, re-applied top to
  bottom on every run via `IF NOT EXISTS`. Alembic is in requirements but never
  initialised — there is no `alembic/` dir or `alembic.ini`. Add DDL to that
  list, idempotently.
- **`requirements.txt` is UTF-16 LE with CRLF** (PowerShell `pip freeze`). pip
  tolerates the BOM, but `grep`/diff can't read it.

## Security, known and accepted for beta

- CORS is `allow_origins=["*"]` with `allow_credentials=False`.
- `/{code}/admin`, `/{code}/reset` and `/{code}/players/{id}/remove` have **no
  auth** — anyone with a 6-character game code can reset a live game or kick a
  player. Game codes are 6 uppercase letters from `random.choices` (not
  `secrets`), so they're guessable at ~309M combinations but not crypto-random.

## Conventions that bite

- **Always `code.upper()`** before comparing a game code or using it as a room key.
- **Async all the way down** — every handler `async def`, every DB call awaited.
  A sync session will deadlock the loop.
- **Logging is `print()` with a bracketed tag** (`[WS-CONNECT]`, `[ANSWER]`,
  `[RESUME]`, `[ADVANCE]`). Match it or convert all of it — don't mix in
  `logging` piecemeal.
