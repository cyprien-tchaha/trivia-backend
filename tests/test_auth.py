"""Session issuing and reading."""
import os
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app import auth
from app.models import User


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-signing-key")


def _user(uid="user-1"):
    return User(id=uid, google_sub="g-1", email="h@example.com", plan="free")


def test_a_session_round_trips():
    assert auth.read_session(auth.issue_session(_user())) == "user-1"


def test_a_token_signed_with_another_key_is_rejected():
    """The whole point of signing: nobody can mint a session for an account
    they don't own."""
    forged = jwt.encode({"sub": "user-1"}, "not-the-key", algorithm="HS256")
    assert auth.read_session(forged) is None


def test_an_expired_session_is_rejected():
    past = datetime.now(timezone.utc) - timedelta(days=1)
    expired = jwt.encode(
        {"sub": "user-1", "exp": int(past.timestamp())},
        "test-signing-key", algorithm="HS256",
    )
    assert auth.read_session(expired) is None


def test_garbage_is_rejected_without_raising():
    for junk in ("", "not-a-token", "a.b.c"):
        assert auth.read_session(junk) is None


def test_an_unsigned_token_is_rejected():
    """alg=none is the classic JWT bypass; PyJWT must not accept it."""
    unsigned = jwt.encode({"sub": "user-1"}, key="", algorithm="none")
    assert auth.read_session(unsigned) is None


def test_signing_without_a_secret_refuses_rather_than_defaulting(monkeypatch):
    """A predictable key would let anyone mint a session for any account, so
    this must fail loudly instead of falling back."""
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        auth.issue_session(_user())
