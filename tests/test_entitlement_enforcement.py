"""The 402 path, end to end through the create endpoint."""
import httpx
import pytest
from sqlalchemy import select

from app.auth import issue_session
from app.models import Game, User


@pytest.fixture(autouse=True)
def config(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-signing-key")
    monkeypatch.setenv("ENFORCE_ENTITLEMENTS", "true")


@pytest.fixture
def client(db):
    import main
    from app.database import get_db
    main.app.dependency_overrides[get_db] = lambda: db
    yield httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://t")
    main.app.dependency_overrides.clear()


def body(topics):
    return {"host_name": "H", "category": "anime", "difficulty": 1,
            "question_count": 10, "topics": topics}


async def test_a_free_host_is_refused_a_custom_topic_with_402(client, db):
    async with client as c:
        r = await c.post("/api/games/create", json=body("Bleach"))
    assert r.status_code == 402
    assert "Pro" in r.json()["detail"]
    # and nothing was created
    assert (await db.execute(select(Game))).scalars().all() == []


async def test_a_free_host_may_still_play_banked_topics(client):
    async with client as c:
        r = await c.post("/api/games/create", json=body("One Piece"))
    assert r.status_code == 200


async def test_a_pro_host_may_ask_for_anything(client, db):
    user = User(google_sub="g-p", email="pro@example.com", plan="pro")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    async with client as c:
        r = await c.post("/api/games/create",
                         headers={"Authorization": f"Bearer {issue_session(user)}"},
                         json=body("Bleach"))
    assert r.status_code == 200
    assert r.json()["plan"] == "pro"
