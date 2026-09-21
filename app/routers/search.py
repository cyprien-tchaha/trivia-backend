"""
Title-search proxy: fronts external APIs and returns a normalized result shape
so the frontend can autocomplete topic selection without caring about the
upstream source.

All three categories are served by TMDB. Anime originally came from Jikan
(MyAnimeList), which stopped returning results from the production host while
TMDB kept working — Jikan rate-limits cloud IP ranges. TMDB already carries
anime as both series and films, so anime is filtered out of TMDB's catalogue
by genre and original language (see _filter_anime), with Jikan kept only as a
short-timeout fallback for titles TMDB has never heard of.

Topics the question bank can serve are suggested locally and merged ahead of
any upstream result, so the picker still works when every external API is
down.

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

from app.services.question_bank_service import suggest_banked_topics
from typing import Literal, Optional
import asyncio
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

# How long we wait on upstream before giving up. This was 3s on the theory
# that a slow response is worse than none while the user types. In practice
# Jikan regularly takes longer than that, so the picker returned "No matches"
# for every query and the host could not set a topic at all. A few seconds of
# spinner beats a picker that never works.
_HTTP_TIMEOUT_SECONDS = 8.0

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


# TMDB's genre id for Animation, and the language code for Japanese. Together
# they are what separates anime from the rest of TMDB's catalogue.
_ANIMATION_GENRE_ID = 16
_JAPANESE = "ja"


def _is_animation(item: dict) -> bool:
    return _ANIMATION_GENRE_ID in (item.get("genre_ids") or [])


def _is_japanese(item: dict) -> bool:
    return (item.get("original_language") or "").lower() == _JAPANESE


def _filter_anime(items: list[dict]) -> list[dict]:
    """
    Narrow TMDB results down to anime, degrading rather than emptying.

    Japanese animation first, which excludes both the live-action One Piece
    and South Park. If nothing qualifies on both counts, accept either signal
    — TMDB's metadata is thin on obscure titles and a slightly wrong
    suggestion beats the empty dropdown that started this. If nothing has any
    metadata at all, return everything.
    """
    strict = [i for i in items if _is_animation(i) and _is_japanese(i)]
    if strict:
        return strict
    loose = [i for i in items if _is_animation(i) or _is_japanese(i)]
    if loose:
        return loose
    return items


async def _tmdb_raw(q: str, kind: str) -> list[dict]:
    """Unnormalised TMDB results, so callers can filter on fields the public
    shape drops (genre_ids, original_language, popularity)."""
    if not TMDB_API_KEY:
        print("[SEARCH] tmdb skipped: TMDB_API_KEY is not set")
        # No key configured; nothing we can do. Soft-fail.
        return []
    url = f"{TMDB_BASE}/search/{kind}"
    params = {"api_key": TMDB_API_KEY, "query": q, "include_adult": "false", "page": 1}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        print(f"[SEARCH] tmdb failed for q={q!r} kind={kind}: {type(e).__name__}: {e}")
        return []
    return data.get("results", [])


def _normalize_tmdb(item: dict, kind: str) -> dict | None:
    title = item.get("name") if kind == "tv" else item.get("title")
    if not title:
        return None
    date = item.get("first_air_date") if kind == "tv" else item.get("release_date")
    year = None
    if date and len(date) >= 4:
        try:
            year = int(date[:4])
        except ValueError:
            year = None
    poster = item.get("poster_path")
    return {
        "id": f"tmdb_{kind}_{item.get('id')}",
        "name": title,
        "year": year,
        "image_url": f"{TMDB_IMG_BASE}{poster}" if poster else None,
    }


async def _search_tmdb(q: str, kind: str) -> list[dict]:
    """kind is 'tv' or 'movie' — TMDB has separate endpoints."""
    raw = await _tmdb_raw(q, kind)
    out = [n for n in (_normalize_tmdb(i, kind) for i in raw[:8]) if n]
    return out


async def _search_anime(q: str) -> list[dict]:
    """
    Anime titles, from TMDB.

    Jikan is MyAnimeList's API and was the original source, but it stopped
    returning anything from the production host while TMDB kept working —
    Jikan rate-limits cloud IP ranges aggressively. TMDB already carries anime
    as TV series and films, and the key is already configured, so this needs
    no new dependency.

    Series and films are searched together because anime spans both: One Piece
    is a TV series, Spirited Away is a film. Results are ordered by TMDB's own
    popularity so the obvious answer lands first.
    """
    tv_raw, movie_raw = await asyncio.gather(
        _tmdb_raw(q, "tv"), _tmdb_raw(q, "movie")
    )

    tagged = [(i, "tv") for i in tv_raw] + [(i, "movie") for i in movie_raw]
    keep = _filter_anime([i for i, _ in tagged])
    kept_ids = {id(i) for i in keep}
    chosen = [(i, kind) for i, kind in tagged if id(i) in kept_ids]
    chosen.sort(key=lambda pair: pair[0].get("popularity") or 0, reverse=True)

    out = [n for n in (_normalize_tmdb(i, kind) for i, kind in chosen[:8]) if n]

    if not out:
        # TMDB knows nothing; give MyAnimeList a short shot at the long tail.
        # Deliberately a tighter timeout than the primary path — this is a
        # bonus attempt and the host is waiting on an autocomplete.
        print(f"[SEARCH] tmdb had no anime for q={q!r}, trying jikan")
        out = await _search_jikan(q, timeout=3.0)
    return out


async def _search_jikan(q: str, timeout: float | None = None) -> list[dict]:
    """Jikan wraps MyAnimeList. No API key required."""
    url = f"{JIKAN_BASE}/anime"
    params = {"q": q, "limit": 8, "sfw": "true"}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        # Soft-fail is deliberate (see the module docstring) but silent
        # soft-fail is not: without this line an empty picker is
        # indistinguishable from "no such anime".
        print(f"[SEARCH] jikan failed for q={q!r}: {type(e).__name__}: {e}")
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


def _merge(banked: list[dict], upstream: list[dict]) -> list[dict]:
    """Banked topics first, then upstream results that aren't duplicates.

    Dedupe is on the normalised name: upstream returns "One Piece" too, and
    showing it twice would let a host pick the non-banked copy of a topic we
    could have served for free.
    """
    seen = {b["name"].strip().lower() for b in banked}
    out = list(banked)
    for item in upstream:
        if item.get("name", "").strip().lower() in seen:
            continue
        out.append(item)
    return out[:12]


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
    # Topics we can serve from the bank, matched locally. These come first and
    # need no network, so the picker still offers valid choices when upstream
    # is unreachable — which previously left the dropdown empty and the host
    # unable to choose a topic at all. They also cost nothing to serve.
    banked = [
        {
            "id": f"bank_{name.lower().replace(' ', '_')}",
            "name": name,
            "year": None,
            "image_url": None,
            "banked": True,
        }
        for name in suggest_banked_topics(category, q_normalized)
    ]

    # Upstream needs a couple of characters to return anything sensible; the
    # local list does not, so a single character still shows banked topics.
    if len(q_normalized) < 2:
        return {"results": banked}

    cached = _cache_get(category, q_normalized)
    if cached is not None:
        return {"results": _merge(banked, cached)}

    if category == "anime":
        results = await _search_anime(q_normalized)
    elif category == "tv_shows":
        results = await _search_tmdb(q_normalized, "tv")
    elif category == "movies":
        results = await _search_tmdb(q_normalized, "movie")
    else:
        results = []

    # Cache only the upstream half. Banked suggestions are cheap to recompute
    # and would otherwise go stale in the cache after a re-seed.
    _cache_put(category, q_normalized, results)
    return {"results": _merge(banked, results)}