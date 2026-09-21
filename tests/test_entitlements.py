"""
The free/paid boundary.

It follows the cost boundary: banked topics are pre-generated and free to
serve, anything else means live AI generation per game.
"""
import pytest

from app.entitlements import may_create_game, topic_is_free
from app.models import User


def free_user():
    return User(id="u1", google_sub="g1", email="a@b.c", plan="free")


def pro_user():
    return User(id="u2", google_sub="g2", email="p@b.c", plan="pro")


@pytest.fixture
def enforcing(monkeypatch):
    monkeypatch.setenv("ENFORCE_ENTITLEMENTS", "true")


@pytest.fixture(autouse=True)
def default_off(monkeypatch):
    monkeypatch.delenv("ENFORCE_ENTITLEMENTS", raising=False)


# ------------------------------------------------------------ the default

def test_enforcement_is_off_unless_switched_on():
    """It must stay off until billing exists. Turning it on first would take a
    working feature away and offer nothing in exchange."""
    assert may_create_game(None, "Some Obscure Anime")[0] is True
    assert may_create_game(free_user(), "")[0] is True


# ------------------------------------------------------- what counts as free

def test_banked_topics_are_free(enforcing):
    for topic in ("One Piece", "one piece", "mha", "Attack on Titan (2013)"):
        assert topic_is_free(topic), topic
        assert may_create_game(free_user(), topic)[0] is True


def test_an_unbanked_topic_is_not_free(enforcing):
    assert topic_is_free("Bleach") is False
    allowed, reason = may_create_game(free_user(), "Bleach")
    assert allowed is False
    assert "Pro" in reason


def test_a_whole_category_game_is_not_free(enforcing):
    """No topic means live generation across the category, which is the
    expensive path — so an empty topic is not the free tier."""
    assert topic_is_free("") is False
    assert may_create_game(free_user(), "")[0] is False


def test_a_multi_topic_game_is_not_free(enforcing):
    """Several titles can't be drawn from the bank, so it costs generation."""
    assert may_create_game(free_user(), "One Piece, Naruto")[0] is False


# --------------------------------------------------------------- who can do what

def test_pro_may_ask_for_anything(enforcing):
    for topic in ("Bleach", "", "One Piece, Naruto", "Some 2019 Show"):
        assert may_create_game(pro_user(), topic)[0] is True, topic


def test_anonymous_hosting_still_works_for_banked_topics(enforcing):
    """Hosting without an account is the free tier, not a degraded state. What
    gates a game is the topic, not whether someone signed in."""
    assert may_create_game(None, "One Piece")[0] is True
    assert may_create_game(None, "Bleach")[0] is False


def test_the_paid_line_tracks_the_generator(enforcing):
    """topic_is_free must use the same matcher the question generator uses, or
    we start charging for things that cost nothing and vice versa."""
    from app.services.question_bank_service import match_bank_topic
    for topic in ("One Piece", "Bleach", "", "mcu", "One Piece (1999)"):
        assert topic_is_free(topic) == (match_bank_topic(topic) is not None)
