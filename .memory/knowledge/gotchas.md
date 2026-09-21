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

- **`app/services/game_service.py` is a 0-byte file.**
- **Test coverage is narrow.** `pytest` exists now (42 tests, in-memory SQLite)
  but covers only the question bank and its wiring. Still untested: scoring
  (`100 + speed_bonus`), the duplicate-answer guard in `/answer`, and the
  `all_answered` counting race.
- **The SQLite harness cannot prove transaction-abort behavior.** Postgres
  leaves a transaction aborted after a failed statement and refuses everything
  until rollback; aiosqlite is far more forgiving. So the `db.rollback()` calls
  in `create_ai_questions`' error paths are correct-by-reasoning, not
  covered-by-test. Keep them.
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

## Question bank (wired in as of the bank-serving change)

- **`try_bank()` is the only bank-vs-AI decision point.** It is all-or-nothing:
  a banked topic holding fewer rows than the game asks for falls through to
  full AI generation. Topping up from the AI would put questions that never
  passed the distinct-answer filter into the same game — the exact duplicate
  the bank exists to prevent.
- **A game's topic can be banked under a different category than the game.**
  The bank seeds "Naruto" as anime; nothing stops a host creating a
  `category="movies"` game with `topics="Naruto"`. Stored questions take
  `category` and `difficulty` from the **game**, never from the bank row, so
  `Question.category` never silently diverges from `Game.category`.
- **The bank and the AI have separate `except` blocks on purpose.** A bank
  failure logs `[BANK] ... falling through to AI` and lets the AI serve the
  game. Merging them would report a bank fault as an AI fault, sending whoever
  reads the log to the wrong file.

## Accounts and entitlements

- **`ENFORCE_ENTITLEMENTS` is off by default and must stay off until billing
  exists.** Flipping it paywalls custom topics with no way to pay.
- **`topic_is_free()` must keep calling `match_bank_topic()`.** It is what
  keeps the billing boundary identical to the cost boundary. A test asserts
  they agree for the same inputs; if that test fails, the pricing has drifted
  from the economics.
- **Match users on `google_sub`, never email.** Emails get reassigned.
- **The Google id_token signature is intentionally unverified** because it
  comes from the token endpoint over TLS with client auth. `aud`/`iss` are
  checked. Never reuse that shortcut for a token a client hands us.
- **The session cookie must stay HttpOnly + Secure + SameSite=lax.** Lax is
  load-bearing: Strict drops the cookie on the redirect back from Google.
- **`SECRET_KEY` missing raises instead of defaulting.** A predictable signing
  key means anyone can mint a session for any account.
- **`current_user_optional` is the default**, not `current_user`. Anonymous
  hosting is a supported tier.

## Server game clock

- **Every instance ticks; a claim decides who acts.** `claim_once()` keys on
  `(game, phase, index)`, so the same transition cannot fire twice even with
  every instance ticking in the same second. Verified with two instances: the
  work distributed across both and the question index never skipped.
- **A failed claim (Redis error) returns True, deliberately.** Allowing the
  transition risks a duplicate advance; blocking it risks the game never
  advancing. A duplicate is recoverable, a freeze is the bug the clock exists
  to prevent.
- **`phase_ends_at IS NULL` means hands off.** That is how games in flight
  during the deploy stay host-driven. Don't backfill it.
- **`/answer` flips the phase itself when everyone has answered**, rather than
  shortening the deadline, so the reveal is immediate. The clock's own
  question→result claim for that index then no-ops. Verified: advances in
  ~2.3s with RESULT_SECONDS=2 instead of waiting out the question.
- **The host's `/question/{index}` resets the deadline.** Without that the old
  deadline fires immediately after the host's manual advance and double-skips.
- **The client still runs its own countdown.** It is display only; the server
  deadline is authoritative. They can drift, which is harmless because the
  server is the backstop, but don't assume the client's number is the truth.

## WebSocket fanout

- **`rooms` is per-process and always will be.** A socket belongs to one
  server process; it cannot be shared. Fanout works by publishing the message
  and letting each instance write to its own sockets.
- **`broadcast()` does not deliver locally when fanout is on.** Delivery
  happens only in the subscriber, including on the instance that published.
  Adding a local write "to be safe" double-sends to that instance's clients.
- **One channel for all games**, filtered by code on receive. A channel per
  game would cut idle traffic but means subscribing/unsubscribing as rooms
  churn, which races against reconnects. Revisit only if broadcast volume
  becomes the bottleneck.
- **`local_connections()` is a floor, not the room size.** With fanout on, a
  room has members on other instances this count cannot see. `/{code}/admin`
  reports it as-is.
- **Redis going down degrades, it does not break.** The listener retries with
  backoff, `fanout_active` flips false, and broadcasts deliver locally — so a
  single-instance deployment keeps playing. Verified by killing redis
  mid-game. Don't "simplify" that fallback away.
- **`GET /health` reports `ws_fanout`.** False in production means broadcasts
  are staying in-process and the service must not run more than one replica.

## Conventions that bite

- **`await db.rollback()` expires every ORM object in the session**, regardless
  of `expire_on_commit=False`. Touching an attribute on one afterwards triggers
  a lazy refresh, which raises `MissingGreenlet` in async code. Bites when a
  test shares one session across a rollback boundary — read the ids you need
  before the call. Production is unaffected: `create_ai_questions` owns its own
  session and only ever holds `game_id` as a string.
- **Always `code.upper()`** before comparing a game code or using it as a room key.
- **Async all the way down** — every handler `async def`, every DB call awaited.
  A sync session will deadlock the loop.
- **Logging is `print()` with a bracketed tag** (`[WS-CONNECT]`, `[ANSWER]`,
  `[RESUME]`, `[ADVANCE]`, `[BANK]`, `[AI-GEN]`, `[FALLBACK]`). Match it or
  convert all of it — don't mix in `logging` piecemeal. `app/routers/search.py`
  and parts of `games.py` still have untagged prints.
