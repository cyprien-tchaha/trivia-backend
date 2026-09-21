"""
Host sessions.

Identity comes from Google, so there is no password here to store, leak or
reset — which also means no email provider is needed just to let someone back
into their account.

A session is a short JWT signed with SECRET_KEY. It is deliberately not a
server-side session table: the only thing it carries is which user it is, and
re-reading the user from the database on every request keeps plan changes
(an upgrade, a downgrade) effective immediately rather than at the next login.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import jwt
import os

from app.database import get_db
from app.models import User

ALGORITHM = "HS256"
#: Long enough that a host isn't signed out mid-party, short enough that a
#: leaked token isn't forever. There is no refresh flow yet; signing in again
#: is one Google click.
SESSION_DAYS = 30


def _secret() -> str:
    secret = os.getenv("SECRET_KEY", "")
    if not secret:
        # Refuse rather than fall back to a default: a predictable signing key
        # means anyone can mint a session for any account.
        raise RuntimeError("SECRET_KEY is not set; refusing to sign sessions")
    return secret


def issue_session(user: User) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": user.id,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=SESSION_DAYS)).timestamp()),
        },
        _secret(),
        algorithm=ALGORITHM,
    )


def read_session(token: str) -> Optional[str]:
    """Return the user id in a valid session token, or None."""
    try:
        payload = jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) else None


def _bearer(request: Request) -> Optional[str]:
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token.strip()
    # Fall back to a cookie so the OAuth redirect can land the session without
    # the frontend having to catch a token out of a URL fragment.
    return request.cookies.get("session") or None


async def current_user_optional(
    request: Request, db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    """
    The signed-in host, or None.

    Optional by default on purpose: hosting anonymously is the free tier, not
    an error. Endpoints that genuinely need an account use current_user.
    """
    token = _bearer(request)
    if not token:
        return None
    user_id = read_session(token)
    if not user_id:
        return None
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def current_user(
    user: Optional[User] = Depends(current_user_optional),
) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    return user
