"""
Anime title search via TMDB.

Jikan (MyAnimeList) stopped returning results from the production host while
TMDB kept working, so anime search moved to TMDB — which already carries
anime as TV series (One Piece) and films (Spirited Away). The filtering below
is what separates anime from the rest of TMDB's catalogue.
"""
from app.routers.search import _filter_anime, _is_animation, _is_japanese

# Shapes trimmed from real TMDB /search responses.
ONE_PIECE_TV = {
    "id": 37854, "name": "One Piece", "original_language": "ja",
    "genre_ids": [10759, 16], "popularity": 180.5, "first_air_date": "1999-10-20",
}
ONE_PIECE_LIVE = {
    "id": 111110, "name": "One Piece", "original_language": "en",
    "genre_ids": [10759, 18], "popularity": 90.0, "first_air_date": "2023-08-31",
}
SPIRITED_AWAY = {
    "id": 129, "title": "Spirited Away", "original_language": "ja",
    "genre_ids": [16, 10751], "popularity": 120.0, "release_date": "2001-07-20",
}
SHOGUN = {  # Japanese-language, but live action
    "id": 726779, "name": "Shōgun", "original_language": "ja",
    "genre_ids": [18, 10759], "popularity": 60.0, "first_air_date": "2024-02-27",
}
SOUTH_PARK = {  # animation, but not Japanese
    "id": 2190, "name": "South Park", "original_language": "en",
    "genre_ids": [16, 35], "popularity": 70.0, "first_air_date": "1997-08-13",
}


def test_signals():
    assert _is_animation(ONE_PIECE_TV) and _is_japanese(ONE_PIECE_TV)
    assert _is_animation(SOUTH_PARK) and not _is_japanese(SOUTH_PARK)
    assert _is_japanese(SHOGUN) and not _is_animation(SHOGUN)


def test_prefers_japanese_animation_over_everything_else():
    """The live-action One Piece must not outrank the anime in an anime
    category — that is the exact confusion this filter exists to prevent."""
    got = _filter_anime([ONE_PIECE_LIVE, ONE_PIECE_TV, SHOGUN, SOUTH_PARK])
    assert got == [ONE_PIECE_TV]


def test_anime_films_qualify():
    assert SPIRITED_AWAY in _filter_anime([SPIRITED_AWAY, SHOGUN])


def test_relaxes_when_nothing_is_both():
    """TMDB's metadata is thin on obscure titles. Rather than return nothing,
    fall back to either signal — a wrong-ish suggestion beats an empty
    dropdown, which is the failure that started all this."""
    got = _filter_anime([SHOGUN, SOUTH_PARK])
    assert SHOGUN in got and SOUTH_PARK in got


def test_falls_back_to_everything_rather_than_empty():
    bare = {"id": 1, "name": "Some Obscure Show", "genre_ids": [], "popularity": 1.0}
    assert _filter_anime([bare]) == [bare]


def test_empty_stays_empty():
    assert _filter_anime([]) == []


def test_missing_metadata_does_not_raise():
    assert _filter_anime([{"id": 2, "name": "No Fields"}]) == [{"id": 2, "name": "No Fields"}]
