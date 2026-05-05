"""
Title-search proxy: fronts external APIs (TMDB for movies/TV, Jikan for anime)
and returns a normalized result shape so the frontend can autocomplete topic
selection without caring about the upstream source.

Why proxy instead of calling from the frontend:
  - Hides the TMDB API key (Jikan needs no key but TMDB does)
  - Lets us swap providers later without a frontend change
  - Centralises caching so identical queries don't hit upstream twice

Failure mode is soft: on upstream timeout/error we return an empty result list
with HTTP 200, so the autocomplete UI degrades to "no suggestions" rather than
showing an error toast. The user can still type and submit; they just won't
see picker hints. That's the right tradeoff for autocomplete.
"""
from fastapi import APIRouter, Query
from typing import Literal, Optional
import httpx
import os
import time

router = APIRouter()

TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMG_BASE = "https://image.tmdb.org/t/p/w185"  # small poster, autocomplete-sized
JIKAN_BASE = "https://api.jikan.moe/v4"

# In-memory cache. Key: (category, normalized_query). Value: (timestamp, results).
# Sized small because autocomplete queries are short and bounded; a 200-entry
# cap with eviction-on-insert is enough for our scale.
_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes
_CACHE_MAX_ENTRIES = 200

# How long we wait on upstream before giving up. Keep this short — the user
# is typing and a slow response is worse than no response.
_HTTP_TIMEOUT_SECONDS = 3.0

Category = Literal["anime", "tv_shows", "movies"]


def _cache_get(category: str, q: str) -> Optional[list[dict]]:
    key = (category, q)
    entry = _CACHE.get(key)
    if entry is None:
        return None
    ts, results = entry
    if time.time() - ts > _CACHE_TTL_SECONDS:
        _CACHE.pop(key, None)
        return None
    return results


def _cache_put(category: str, q: str, results: list[dict]) -> None:
    if len(_CACHE) >= _CACHE_MAX_ENTRIES:
        # Cheap eviction: drop the oldest entry. Not LRU but good enough.
        oldest_key = min(_CACHE, key=lambda k: _CACHE[k][0])
        _CACHE.pop(oldest_key, None)
    _CACHE[(category, q)] = (time.time(), results)


async def _search_tmdb(q: str, kind: str) -> list[dict]:
    """kind is 'tv' or 'movie' — TMDB has separate endpoints."""
    if not TMDB_API_KEY:
        # No key configured; nothing we can do. Soft-fail.
        return []
    url = f"{TMDB_BASE}/search/{kind}"
    params = {"api_key": TMDB_API_KEY, "query": q, "include_adult": "false", "page": 1}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError):
        return []

    out: list[dict] = []
    for item in data.get("results", [])[:8]:
        title = item.get("name") if kind == "tv" else item.get("title")
        if not title:
            continue
        date = item.get("first_air_date") if kind == "tv" else item.get("release_date")
        year = None
        if date and len(date) >= 4:
            try:
                year = int(date[:4])
            except ValueError:
                year = None
        poster = item.get("poster_path")
        out.append({
            "id": f"tmdb_{kind}_{item.get('id')}",
            "name": title,
            "year": year,
            "image_url": f"{TMDB_IMG_BASE}{poster}" if poster else None,
        })
    return out


async def _search_jikan(q: str) -> list[dict]:
    """Jikan wraps MyAnimeList. No API key required."""
    url = f"{JIKAN_BASE}/anime"
    params = {"q": q, "limit": 8, "sfw": "true"}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError):
        return []

    out: list[dict] = []
    for item in data.get("data", []):
        title = item.get("title_english") or item.get("title")
        if not title:
            continue
        year = item.get("year")
        # Jikan's images are nested.
        images = item.get("images", {})
        webp = images.get("webp", {}) or {}
        jpg = images.get("jpg", {}) or {}
        image_url = webp.get("small_image_url") or jpg.get("small_image_url")
        out.append({
            "id": f"mal_{item.get('mal_id')}",
            "name": title,
            "year": year,
            "image_url": image_url,
        })
    return out


@router.get("")
async def search_titles(
    category: Category = Query(..., description="One of: anime, tv_shows, movies"),
    q: str = Query("", min_length=0, max_length=100, description="Partial title query"),
):
    """Returns up to ~8 normalized matches. Empty results on short query or
    upstream failure; never raises 5xx for upstream issues."""
    q_normalized = q.strip().lower()

    # Don't even ask upstream for very short queries — most APIs return
    # garbage and it wastes the budget. The frontend should also debounce,
    # but we defend here too.
    if len(q_normalized) < 2:
        return {"results": []}

    cached = _cache_get(category, q_normalized)
    if cached is not None:
        return {"results": cached}

    if category == "anime":
        results = await _search_jikan(q_normalized)
    elif category == "tv_shows":
        results = await _search_tmdb(q_normalized, "tv")
    elif category == "movies":
        results = await _search_tmdb(q_normalized, "movie")
    else:
        results = []

    _cache_put(category, q_normalized, results)
    return {"results": results}