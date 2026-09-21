"""
Local topic suggestions.

The picker must offer real choices without an upstream call — when the title
API is down the dropdown was empty and hosts had no way to pick a valid topic
at all.
"""
import pytest

from app.services.question_bank_service import suggest_banked_topics


@pytest.mark.parametrize("query,expected", [
    ("one", "One Piece"),
    ("ONE PIE", "One Piece"),
    ("naru", "Naruto"),
    ("attack", "Attack on Titan"),
    ("demon", "Demon Slayer"),
])
def test_prefix_and_substring_match(query, expected):
    assert expected in suggest_banked_topics("anime", query)


@pytest.mark.parametrize("alias,expected", [
    ("mha", "My Hero Academia"),
    ("jjk", "Jujutsu Kaisen"),
    ("aot", "Attack on Titan"),
    ("dbz", "Dragon Ball"),
])
def test_aliases_are_searchable(alias, expected):
    """Hosts type the short form. It has to find the canonical title, because
    that canonical name is what the bank is keyed on."""
    assert expected in suggest_banked_topics("anime", alias)


def test_scoped_to_category():
    """Breaking Bad is banked under tv_shows; it must not surface while the
    host has Anime selected, or they'd pick a topic the category contradicts."""
    assert "Breaking Bad" not in suggest_banked_topics("anime", "breaking")
    assert "Breaking Bad" in suggest_banked_topics("tv_shows", "breaking")
    assert "Marvel Cinematic Universe" in suggest_banked_topics("movies", "marvel")


def test_empty_query_lists_the_category(): 
    """With nothing typed the picker should still be able to show what's
    available, so hosts discover the topics with curated questions."""
    anime = suggest_banked_topics("anime", "")
    assert "One Piece" in anime and "Naruto" in anime
    assert "Breaking Bad" not in anime


def test_no_match_returns_empty():
    assert suggest_banked_topics("anime", "zzzznotashow") == []


def test_respects_limit():
    assert len(suggest_banked_topics("anime", "", limit=3)) == 3


def test_returns_the_exact_casing_the_bank_is_keyed_on():
    """The name goes straight back as the game's topic, so it has to match
    what seed_bank.py wrote or match_bank_topic won't find it."""
    from app.services.question_bank_service import match_bank_topic
    for name in suggest_banked_topics("anime", ""):
        assert match_bank_topic(name) == name
