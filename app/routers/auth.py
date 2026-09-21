"""
Google sign-in for hosts.

Flow: /google/start sends the host to Google, Google sends them back to
/google/callback with a code, we trade that code for tokens over TLS using the
client secret, and the identity in the response becomes a session.

Players never come through here. Joining a game needs a code and a nickname.
"""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
import jwt
import os

from app.auth import ALGORITHM, _secret, current_user, issue_session
from app.database import get_db
from app.models import User

router = APIRouter()

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}

#: The state parameter is a signed, short-lived token rather than a random
#: value in a server-side store — it only has to prove the callback belongs to
#: a flow we started, and it expires quickly.
STATE_MINUTES = 10


def _cfg(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise HTTPException(status_code=503, detail=f"{name} is not configured")
    return value


def _issue_state() -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"k": "oauth-state", "exp": int((now + timedelta(minutes=STATE_MINUTES)).timestamp())},
        _secret(), algorithm=ALGORITHM,
    )


def _state_is_ours(state: str) -> bool:
    """Reject a callback we didn't start — without this, an attacker can hand
    a victim a crafted callback URL and log them into the attacker's account."""
    try:
        payload = jwt.decode(state, _secret(), algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return False
    return payload.get("k") == "oauth-state"


@router.get("/google/start")
async def google_start():
    params = {
        "client_id": _cfg("GOOGLE_CLIENT_ID"),
        "redirect_uri": _cfg("GOOGLE_REDIRECT_URI"),
        "response_type": "code",
        "scope": "openid email profile",
        "state": _issue_state(),
        # Hosts sign in rarely; asking Google to pick an account beats
        # silently reusing whichever one the browser happens to hold.
        "prompt": "select_account",
    }
    return RedirectResponse(f"{GOOGLE_AUTH}?{urlencode(params)}", status_code=307)


async def _exchange_code(code: str) -> dict:
    data = {
        "code": code,
        "client_id": _cfg("GOOGLE_CLIENT_ID"),
        "client_secret": _cfg("GOOGLE_CLIENT_SECRET"),
        "redirect_uri": _cfg("GOOGLE_REDIRECT_URI"),
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(GOOGLE_TOKEN, data=data)
    if r.status_code != 200:
        print(f"[AUTH] google token exchange failed: {r.status_code} {r.text[:200]}")
        raise HTTPException(status_code=401, detail="Google sign-in failed")
    return r.json()


def _identity_from(token_response: dict) -> dict:
    """
    Pull the identity out of Google's id_token.

    The signature is not verified here, and that is deliberate rather than an
    oversight: this token came straight back from Google's token endpoint over
    TLS, authenticated with our client secret, so its provenance is already
    established by the channel. Google documents this exact case.

    That reasoning does NOT extend to an id_token handed to us by a client —
    if this ever accepts one from a request body, it must verify the signature
    against Google's JWKS first.

    aud and iss are still checked, cheaply, so a token minted for a different
    application cannot be replayed into this one.
    """
    raw = token_response.get("id_token")
    if not raw:
        raise HTTPException(status_code=401, detail="Google sign-in failed")
    try:
        claims = jwt.decode(raw, options={"verify_signature": False})
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Google sign-in failed")

    if claims.get("aud") != os.getenv("GOOGLE_CLIENT_ID", ""):
        raise HTTPException(status_code=401, detail="Google sign-in failed")
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise HTTPException(status_code=401, detail="Google sign-in failed")
    if not claims.get("sub") or not claims.get("email"):
        raise HTTPException(status_code=401, detail="Google sign-in failed")
    return claims


async def upsert_user(db: AsyncSession, claims: dict) -> User:
    """
    Match on Google's subject id, never on email: an email can be changed or
    reassigned, the sub cannot, and matching on email is how one person ends
    up inside another person's account.
    """
    result = await db.execute(select(User).where(User.google_sub == claims["sub"]))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(google_sub=claims["sub"], email=claims["email"], plan="free")
        db.add(user)
    user.email = claims["email"]
    user.name = claims.get("name")
    user.picture_url = claims.get("picture")
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/google/callback")
async def google_callback(
    code: str = Query(...),
    state: str = Query(""),
    db: AsyncSession = Depends(get_db),
):
    if not _state_is_ours(state):
        raise HTTPException(status_code=400, detail="Invalid sign-in state")

    claims = _identity_from(await _exchange_code(code))
    user = await upsert_user(db, claims)
    print(f"[AUTH] signed in {user.email} plan={user.plan}")

    frontend = os.getenv("FRONTEND_URL", "").rstrip("/")
    response = RedirectResponse(frontend or "/", status_code=307)
    response.set_cookie(
        "session",
        issue_session(user),
        max_age=60 * 60 * 24 * 30,
        httponly=True,            # not readable by page scripts
        secure=True,              # only over HTTPS
        samesite="lax",           # survives the redirect back from Google
        path="/",
    )
    return response


@router.get("/me")
async def me(user: User = Depends(current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "picture_url": user.picture_url,
        "plan": user.plan,
    }


@router.post("/logout")
async def logout():
    response = RedirectResponse(os.getenv("FRONTEND_URL", "/"), status_code=303)
    response.delete_cookie("session", path="/")
    return response
