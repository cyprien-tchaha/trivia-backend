"""
CORS policy.

A misconfiguration here breaks every API call with no HTTP response at all,
and the frontend can only report a generic connection error — so the symptom
points nowhere near the cause. These pin the shape.
"""
import importlib

import pytest


def config(monkeypatch, **env):
    for k in ("FRONTEND_URL", "ALLOWED_ORIGINS", "ENVIRONMENT"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import main
    importlib.reload(main)
    return main._cors_config()


def test_unset_frontend_url_keeps_the_open_credential_less_policy(monkeypatch):
    """Deploying before sign-in is configured must not change anything."""
    cfg = config(monkeypatch)
    assert cfg["allow_origins"] == ["*"]
    assert cfg["allow_credentials"] is False


def test_apex_and_www_are_both_accepted(monkeypatch):
    """Serving at www. with the apex configured (or the reverse) would
    otherwise block every request."""
    cfg = config(monkeypatch, FRONTEND_URL="https://playfanatic.gg")
    assert "https://playfanatic.gg" in cfg["allow_origins"]
    assert "https://www.playfanatic.gg" in cfg["allow_origins"]

    cfg = config(monkeypatch, FRONTEND_URL="https://www.playfanatic.gg")
    assert "https://playfanatic.gg" in cfg["allow_origins"]
    assert "https://www.playfanatic.gg" in cfg["allow_origins"]


def test_credentials_are_enabled_only_with_explicit_origins(monkeypatch):
    """The spec forbids credentials with a wildcard, so the two must move
    together."""
    cfg = config(monkeypatch, FRONTEND_URL="https://playfanatic.gg")
    assert cfg["allow_credentials"] is True
    assert "*" not in cfg["allow_origins"]


def test_extra_origins_are_honoured(monkeypatch):
    cfg = config(monkeypatch, FRONTEND_URL="https://playfanatic.gg",
                 ALLOWED_ORIGINS="https://staging.playfanatic.gg")
    assert "https://staging.playfanatic.gg" in cfg["allow_origins"]


def test_localhost_is_not_trusted_in_production(monkeypatch):
    """A credentialed policy trusting localhost lets anything a user happens
    to be running locally read authenticated responses."""
    cfg = config(monkeypatch, FRONTEND_URL="https://playfanatic.gg", ENVIRONMENT="production")
    assert not any("localhost" in o for o in cfg["allow_origins"])

    cfg = config(monkeypatch, FRONTEND_URL="https://playfanatic.gg", ENVIRONMENT="development")
    assert any("localhost" in o for o in cfg["allow_origins"])
