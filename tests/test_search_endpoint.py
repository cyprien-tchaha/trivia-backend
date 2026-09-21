"""
End-to-end through the /api/search route with TMDB stubbed, since the real
API is not reachable from the test environment.
"""
import httpx
import pytest

import app.routers.search as search


TV = [
    {"id": 37854, "name": "One Piece", "original_language": "ja",
     "genre_ids": [10759, 16], "popularity": 180.5, "first_air_date": "1999-10-20",
     "poster_path": "/op.jpg"},
    {"id": 111110, "name": "One Piece", "original_language": "en",
     "genre_ids": [10759, 18], "popularity": 900.0, "first_air_date": "2023-08-31",
     "poster_path": "/live.jpg"},
]
MOVIES = [
    {"id": 129, "title": "One Piece Film: Red", "original_language": "ja",
     "genre_ids": [16], "popularity": 50.0, "release_date": "2022-08-06",
     "poster_path": "/red.jpg"},
]


@pytest.fixture(autouse=True)
def no_cache():
    search._CACHE.clear()
    yield
    search._CACHE.clear()


async def _get(params):
    transport = httpx.ASGITransport(app=__import__("main").app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/search", params=params)
        assert r.status_code == 200, r.text
        return r.json()["results"]


async def test_anime_search_uses_tmdb_and_excludes_live_action(monkeypatch):
    async def fake(q, kind):
        return TV if kind == "tv" else MOVIES
    monkeypatch.setattr(search, "_tmdb_raw", fake)
    monkeypatch.setattr(search, "TMDB_API_KEY", "test-key")

    names = [r["name"] for r in await _get({"category": "anime", "q": "one piece"})]

    # The live-action series is far more "popular" in TMDB's ranking but is
    # not animation, so it must not appear in an anime search.
    assert "One Piece Film: Red" in names
    assert names.count("One Piece") == 1, f"live action leaked in: {names}"


async def test_banked_topic_comes_first_and_is_not_duplicated(monkeypatch):
    async def fake(q, kind):
        return TV if kind == "tv" else []
    monkeypatch.setattr(search, "_tmdb_raw", fake)
    monkeypatch.setattr(search, "TMDB_API_KEY", "test-key")

    results = await _get({"category": "anime", "q": "one piece"})

    assert results[0]["name"] == "One Piece"
    assert results[0]["banked"] is True
    # TMDB's own "One Piece" must be deduped away, or a host could pick the
    # non-banked copy of a topic the bank could serve for free.
    assert [r["name"] for r in results].count("One Piece") == 1


async def test_falls_back_to_jikan_when_tmdb_knows_nothing(monkeypatch):
    calls = []

    async def empty(q, kind):
        return []

    async def fake_jikan(q, timeout=None):
        calls.append(timeout)
        return [{"id": "mal_1", "name": "Obscure OVA", "year": 1994, "image_url": None}]

    monkeypatch.setattr(search, "_tmdb_raw", empty)
    monkeypatch.setattr(search, "_search_jikan", fake_jikan)
    monkeypatch.setattr(search, "TMDB_API_KEY", "test-key")

    names = [r["name"] for r in await _get({"category": "anime", "q": "obscure ova"})]

    assert "Obscure OVA" in names
    assert calls == [3.0], "the fallback must use the short timeout, not the full one"


async def test_tv_and_movies_are_unaffected(monkeypatch):
    async def fake(q, kind):
        return TV if kind == "tv" else MOVIES
    monkeypatch.setattr(search, "_tmdb_raw", fake)
    monkeypatch.setattr(search, "TMDB_API_KEY", "test-key")

    # tv_shows keeps TMDB's own ordering and does no anime filtering
    names = [r["name"] for r in await _get({"category": "tv_shows", "q": "one piece"})]
    assert names.count("One Piece") == 2
