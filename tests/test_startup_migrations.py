"""
Schema drift at deploy.

The deploy start command is `uvicorn main:app` — nothing ran migrate.py, so a
release that added a column shipped code referencing a column the database did
not have. Every game creation answered 500, the clock failed on every tick, and
because Starlette's error handler sits outside CORSMiddleware the 500 came back
with no Access-Control-Allow-Origin — so the browser reported a CORS failure
and the real cause never reached anyone.
"""
import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_importing_migrate_does_not_run_it():
    """It has to be importable from the app's startup path; a module-level
    asyncio.run() would fire on import, inside the running event loop."""
    import migrate
    importlib.reload(migrate)
    assert callable(migrate.migrate)
    assert migrate.MIGRATIONS


def test_startup_applies_migrations(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://playfanatic.gg")
    import main
    importlib.reload(main)

    called = []

    async def fake_migrate():
        called.append(True)

    monkeypatch.setattr(main, "apply_migrations", fake_migrate)
    with TestClient(main.app):
        pass
    assert called, "startup must bring the schema up to date before serving"


def test_an_unhandled_error_still_carries_cors_headers(monkeypatch):
    """Without this every server error is misreported as a CORS problem and
    the status code never reaches the client."""
    monkeypatch.setenv("FRONTEND_URL", "https://playfanatic.gg")
    import main
    importlib.reload(main)

    @main.app.get("/_boom")
    async def boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(main, "apply_migrations", lambda: _noop())

    with TestClient(main.app, raise_server_exceptions=False) as client:
        r = client.get("/_boom", headers={"Origin": "https://playfanatic.gg"})

    assert r.status_code == 500
    assert r.headers.get("access-control-allow-origin") == "https://playfanatic.gg"
    assert r.json()["detail"]


async def _noop():
    return None
