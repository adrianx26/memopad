"""Test the global IntegrityError -> HTTP 409 handler in memopad.api.app.

This is the server-side linchpin of the fix for the `assimilate` UNIQUE-constraint
failure: a duplicate-permalink insert used to surface as HTTP 500 with a message the
tool could not recognize as a conflict, so its update-in-place fallback never ran.
The handler maps IntegrityError -> 409 with a detail containing "already exists",
which the assimilate tool's conflict predicate matches. The handler is registered in
``memopad.api.app``; here we import the actual handler function and exercise it on a
minimal app (no DB stack) so the test is fast and stable.
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError

from memopad.api.app import integrity_exception_handler


def _make_app() -> FastAPI:
    """Minimal app with the real IntegrityError handler and a route that raises one."""
    app = FastAPI()
    app.add_exception_handler(IntegrityError, integrity_exception_handler)

    @app.get("/boom")
    async def _boom():
        # Mimic repository.create hitting a duplicate (permalink, project_id).
        raise IntegrityError(
            "INSERT INTO entity (permalink, project_id) VALUES (?, ?)",
            {},
            Exception("UNIQUE constraint failed: entity.permalink, entity.project_id"),
        )

    return app


@pytest.mark.asyncio
async def test_integrity_error_returns_409_with_already_exists():
    """A duplicate insert surfaces as 409, not 500, with 'already exists' in the detail."""
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/boom")

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    # The detail must contain a substring the assimilate tool's conflict predicate
    # recognizes ("already exists"), so its update-in-place fallback fires.
    assert "already exists" in detail.lower()
    # And it should still surface the underlying constraint message for diagnostics.
    assert "unique constraint failed" in detail.lower()


@pytest.mark.asyncio
async def test_integrity_error_detail_includes_orig_message():
    """The detail carries exc.orig so callers can see *what* conflicted."""
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/boom")
    assert resp.status_code == 409
    assert "entity.permalink" in resp.json()["detail"]