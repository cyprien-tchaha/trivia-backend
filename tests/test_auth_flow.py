"""
The Google sign-in round trip, with Google's token endpoint stubbed since it
is not reachable from the test environment.
"""
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from sqlalchemy import select

import app.routers.auth as auth_router
from app.auth import issue_session
from app.models import Game, User

CLIENT_ID = "test-client.apps.googleusercontent.com"


@pytest.fixture(autouse=True)
def config(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-signing-key")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://api.example.com/api/auth/google/callback")
    monkeypatch.setenv("FRONTEND_URL", "https://app.example.com")


def google_id_token(**over):
    claims = {
        "sub": "google-sub-123", "email": "host@example.com",
        "name": "A Host", "picture": "https://img/x.png",
        "aud": CLIENT_ID, "iss": "https://accounts.google.com",
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
    }
    claims.update(over)
    # Google signs this; we never check the signature (see _identity_from).
    return jwt.encode(claims, "google-private-key-stand-in", algorithm="HS256")


@pytest.fixture
def client(db, monkeypatch):
    import main
    from app.database import get_db
    main.app.dependency_overrides[get_db] = lambda: db
    transport = httpx.ASGITransport(app=main.app)
    yield httpx.AsyncClient(transport=transport, base_url="http://t", follow_redirects=False)
    main.app.dependency_overrides.clear()


def stub_google(monkeypatch, **over):
    async def fake(code):
        return {"id_token": google_id_token(**over), "access_token": "at"}
    monkeypatch.setattr(auth_router, "_exchange_code", fake)


# ------------------------------------------------------------------ start

async def test_start_sends_the_host_to_google(client):
    async with client as c:
        r = await c.get("/api/auth/google/start")
    assert r.status_code == 307
    assert r.headers["location"].startswith(auth_router.GOOGLE_AUTH)
    assert "state=" in r.headers["location"]
    assert CLIENT_ID in r.headers["location"]


# --------------------------------------------------------------- callback

async def test_a_successful_callback_creates_the_user_and_a_session(client, db, monkeypatch):
    stub_google(monkeypatch)
    async with client as c:
        r = await c.get("/api/auth/google/callback",
                        params={"code": "abc", "state": auth_router._issue_state()})
    assert r.status_code == 307
    assert r.headers["location"] == "https://app.example.com"

    cookie = r.headers.get("set-cookie", "")
    assert "session=" in cookie
    # A session cookie readable by page scripts, or sent over plain HTTP, is
    # a session anyone on the network or in an XSS can take.
    assert "HttpOnly" in cookie and "Secure" in cookie

    user = (await db.execute(select(User))).scalar_one()
    assert (user.email, user.plan) == ("host@example.com", "free")


async def test_signing_in_again_updates_rather_than_duplicates(client, db, monkeypatch):
    stub_google(monkeypatch)
    async with client as c:
        await c.get("/api/auth/google/callback",
                    params={"code": "a", "state": auth_router._issue_state()})
    stub_google(monkeypatch, email="renamed@example.com", name="Renamed")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=__import__("main").app),
        base_url="http://t", follow_redirects=False,
    ) as c:
        await c.get("/api/auth/google/callback",
                    params={"code": "b", "state": auth_router._issue_state()})

    users = (await db.execute(select(User))).scalars().all()
    assert len(users) == 1, "matched on something other than the Google sub"
    assert users[0].email == "renamed@example.com"


async def test_a_callback_we_did_not_start_is_rejected(client, monkeypatch):
    """Without this an attacker hands a victim a crafted callback URL and logs
    them into the attacker's account."""
    stub_google(monkeypatch)
    async with client as c:
        r = await c.get("/api/auth/google/callback", params={"code": "abc", "state": "forged"})
    assert r.status_code == 400


async def test_a_token_for_another_application_is_rejected(client, monkeypatch):
    """A valid Google token minted for a different client must not be
    replayable into this one."""
    stub_google(monkeypatch, aud="someone-elses-client-id")
    async with client as c:
        r = await c.get("/api/auth/google/callback",
                        params={"code": "abc", "state": auth_router._issue_state()})
    assert r.status_code == 401


async def test_a_token_from_the_wrong_issuer_is_rejected(client, monkeypatch):
    stub_google(monkeypatch, iss="https://evil.example.com")
    async with client as c:
        r = await c.get("/api/auth/google/callback",
                        params={"code": "abc", "state": auth_router._issue_state()})
    assert r.status_code == 401


# --------------------------------------------------------------------- me

async def test_me_requires_a_session(client):
    async with client as c:
        r = await c.get("/api/auth/me")
    assert r.status_code == 401


async def test_me_returns_the_signed_in_host(client, db):
    user = User(google_sub="g-9", email="me@example.com", name="Me", plan="pro")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    async with client as c:
        r = await c.get("/api/auth/me",
                        headers={"Authorization": f"Bearer {issue_session(user)}"})
    assert r.status_code == 200
    assert r.json()["email"] == "me@example.com"
    assert r.json()["plan"] == "pro"


# ------------------------------------------------------- ownership on games

async def test_a_game_created_while_signed_in_is_owned(client, db):
    user = User(google_sub="g-10", email="owner@example.com", plan="free")
    db.add(user)
    await db.commit()
    await db.refresh(user)

    async with client as c:
        r = await c.post("/api/games/create",
                         headers={"Authorization": f"Bearer {issue_session(user)}"},
                         json={"host_name": "Owner", "category": "anime",
                               "difficulty": 1, "question_count": 10, "topics": "One Piece"})
    assert r.status_code == 200
    assert r.json()["hosted_by"] == "owner@example.com"
    game = (await db.execute(select(Game))).scalar_one()
    assert game.user_id == user.id


async def test_a_game_created_anonymously_still_works(client, db):
    """Anonymous hosting is the free tier, not an error."""
    async with client as c:
        r = await c.post("/api/games/create",
                         json={"host_name": "Anon", "category": "anime",
                               "difficulty": 1, "question_count": 10, "topics": "One Piece"})
    assert r.status_code == 200
    assert r.json()["hosted_by"] is None
    game = (await db.execute(select(Game))).scalar_one()
    assert game.user_id is None


async def test_the_session_cookie_is_secure_by_default(client, monkeypatch):
    """Secure must be the default. A session cookie sent over plain HTTP is
    readable by anyone on the network."""
    monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
    stub_google(monkeypatch)
    async with client as c:
        r = await c.get("/api/auth/google/callback",
                        params={"code": "abc", "state": auth_router._issue_state()})
    assert "Secure" in r.headers.get("set-cookie", "")


async def test_config_reports_whether_sign_in_is_available(client, monkeypatch):
    async with client as c:
        assert (await c.get("/api/auth/config")).json()["google_enabled"] is True
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    import httpx as _httpx
    async with _httpx.AsyncClient(
        transport=_httpx.ASGITransport(app=__import__("main").app), base_url="http://t"
    ) as c:
        assert (await c.get("/api/auth/config")).json()["google_enabled"] is False


async def test_start_without_credentials_sends_the_host_back_not_to_json(client, monkeypatch):
    """A host clicking Sign in must never land on a raw JSON error page."""
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    async with client as c:
        r = await c.get("/api/auth/google/start")
    assert r.status_code == 307
    assert r.headers["location"] == "https://app.example.com/?error=signin_unavailable"
