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


def test_frontend_url_without_a_scheme_still_produces_a_usable_origin(monkeypatch):
    """A bare host in FRONTEND_URL never matches an Origin header, so every
    request is blocked while the variable looks correctly set."""
    cfg = config(monkeypatch, FRONTEND_URL="playfanatic.gg")
    assert "https://playfanatic.gg" in cfg["allow_origins"]
    assert "https://www.playfanatic.gg" in cfg["allow_origins"]


def test_origin_case_and_trailing_path_are_normalised(monkeypatch):
    """Origin headers are lowercase scheme+host with no path; anything else
    configured here silently matches nothing."""
    cfg = config(monkeypatch, FRONTEND_URL="HTTPS://PlayFanatic.GG/host")
    assert "https://playfanatic.gg" in cfg["allow_origins"]


def test_health_reports_the_effective_origins(monkeypatch):
    """Without this the only way to see what the server actually allows is
    the deploy log, which scrolls away."""
    import asyncio
    import importlib

    monkeypatch.setenv("FRONTEND_URL", "https://playfanatic.gg")
    import main
    importlib.reload(main)

    body = asyncio.run(main.health())
    assert "https://playfanatic.gg" in body["cors_origins"]
    assert body["cors_credentials"] is True


def test_http_configured_for_a_real_host_also_accepts_https(monkeypatch):
    """The site is served over https whatever the variable says, so an http
    value here blocks everything. Trusting the https form of the same host is
    strictly the safer transport, not a wider trust."""
    cfg = config(monkeypatch, FRONTEND_URL="http://playfanatic.gg")
    assert "https://playfanatic.gg" in cfg["allow_origins"]


def test_localhost_does_not_grow_an_https_twin(monkeypatch):
    cfg = config(monkeypatch, FRONTEND_URL="http://localhost:3000")
    assert "https://localhost:3000" not in cfg["allow_origins"]
