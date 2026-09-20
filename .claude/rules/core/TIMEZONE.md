# Timezone Rules

## One Rule: Everything is UTC

**Database stores UTC. API sends UTC. API receives UTC. No exceptions.**

The client is the only place that converts to local time — for display only.

```
Postgres (TIMESTAMPTZ) → Python (aware datetime, UTC) → JSON (ISO 8601 Z) → Client → Display (local)
                                                                          ← Send (local → UTC) ← Input
```

---

## Database

### Use `TIMESTAMPTZ` — Not `TIMESTAMP`

`TIMESTAMPTZ` converts to UTC on insert and back on read. If a connection has a
non-UTC session timezone (a DB tool, a pool quirk, a migration script), it still
stores the correct instant. Plain `TIMESTAMP` silently stores whatever it was
handed, and you can't tell afterwards that it's wrong.

```python
# CORRECT — this is what app/models.py already does
created_at = Column(DateTime(timezone=True), server_default=func.now())
disconnected_at = Column(DateTime(timezone=True), nullable=True, default=None)

# WRONG — naive column
created_at = Column(DateTime, default=datetime.now)
```

In hand-written SQL in `migrate.py`:

```sql
-- CORRECT
created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()

-- WRONG
created_at TIMESTAMP DEFAULT NOW()
```

Both types are 8 bytes. There is no storage reason to prefer the naive one.

### Prefer `server_default=func.now()` Over a Python Default

`func.now()` becomes `NOW()` in Postgres, so the timestamp comes from one clock
— the database's. A Python default means every app instance stamps rows from its
own container clock, and those drift.

---

## Python

### Never Use a Naive `datetime`

```python
# CORRECT — aware, unambiguous
from datetime import datetime, timezone, timedelta

now = datetime.now(timezone.utc)
expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

# WRONG — naive: no timezone, comparisons against aware datetimes raise TypeError
now = datetime.now()
now = datetime.utcnow()   # also naive, despite the name. Deprecated in 3.12.
```

`datetime.utcnow()` is the trap: it returns UTC wall-clock with `tzinfo=None`,
so it *looks* right and then blows up the first time you compare it to a value
read back from a `TIMESTAMPTZ` column.

### Comparing and Subtracting

A value read from a `TIMESTAMPTZ` column comes back **aware**. Compare it only
against other aware values.

```python
# CORRECT
if player.disconnected_at and datetime.now(timezone.utc) - player.disconnected_at > timedelta(seconds=60):
    ...

# WRONG — TypeError: can't subtract offset-naive and offset-aware datetimes
if datetime.now() - player.disconnected_at > timedelta(seconds=60):
    ...
```

### Local-Time Arithmetic

Only at a real computation boundary (scheduling something for a user's local
9 AM, a report bucketed by local day). Use `zoneinfo`, then convert straight
back to UTC.

```python
from zoneinfo import ZoneInfo

def next_local_9am(tz_name: str) -> datetime:
    local = datetime.now(ZoneInfo(tz_name)).replace(hour=9, minute=0, second=0, microsecond=0)
    return local.astimezone(timezone.utc)   # convert back before it leaves this function
```

Never let a non-UTC datetime into a model, a schema or a response.

### Durations Are Integers, Not Datetimes

This codebase measures elapsed time in milliseconds as a plain `int`
(`time_taken_ms`). Keep it that way — don't convert a duration into a datetime.

---

## API Contract

All datetime fields are ISO 8601 UTC with a `Z` (or `+00:00`) suffix.

```json
{
  "created_at": "2024-01-15T10:30:00Z",
  "disconnected_at": null
}
```

Pydantic v2 serializes an aware `datetime` to ISO 8601 with the offset already.
Because the value is UTC, that offset is `+00:00` — correct and unambiguous.

Query parameters are UTC too:

```
GET /api/games?from=2024-01-01T00:00:00Z
```

### No Timezone Field Beside a Timestamp

```json
// WRONG
{ "created_at": "2024-01-15T10:30:00Z", "timezone": "America/New_York" }

// CORRECT
{ "created_at": "2024-01-15T10:30:00Z" }
```

The exception is a *user preference* (a notification hour), which is a separate
IANA-name column plus a local time — not a timestamp. Compute the UTC fire time
dynamically, because DST shifts the offset: "9 AM New York" is 14:00 UTC in
winter and 13:00 UTC in summer.

**Never use a fixed offset as a timezone identifier.** `+05:30` is an offset, not
a timezone. Use IANA names: `Asia/Kolkata`, `America/New_York`.

---

## Containers and Logs

Set `TZ=UTC` in the container so that anything falling back to local time still
lands on UTC:

```dockerfile
ENV TZ=UTC
```

On Railway, set `TZ=UTC` as an environment variable.

Logging in this project is `print()` with a bracketed tag. If you add timestamps
to it, or move to `logging`, format them UTC:

```python
logging.Formatter.converter = time.gmtime
```

---

## Quick Reference

| Layer | Type | Example |
|-------|------|---------|
| Postgres column | `TIMESTAMPTZ` | `2024-01-15 10:30:00+00` |
| SQLAlchemy | `DateTime(timezone=True)` | `server_default=func.now()` |
| Python | aware `datetime` | `datetime.now(timezone.utc)` |
| API JSON | ISO 8601 + Z | `"2024-01-15T10:30:00Z"` |
| Duration | `int` milliseconds | `time_taken_ms: 4200` |
| User preference | IANA name column | `America/New_York` |

## What NOT to Do

- Don't use `DateTime` without `timezone=True`
- Don't use `datetime.now()` or `datetime.utcnow()` — both are naive
- Don't compare a naive datetime to one read from the database
- Don't put a non-UTC datetime in a model, schema or response
- Don't use fixed offsets (`+05:30`) as timezone identifiers — use IANA names
- Don't store a timezone alongside a past-event timestamp
- Don't convert to local time on the server — that's the client's job
- Don't assume the host timezone is UTC — set `TZ=UTC` explicitly
