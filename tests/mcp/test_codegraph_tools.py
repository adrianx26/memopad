"""MCP tool-level tests for the CodeGraph tools (Tb G2).

The tools talk to the server over HTTP; here we monkeypatch the HTTP seam
(`get_client`, `get_active_project`, `KnowledgeClient`) so the tool *rendering*
logic is exercised without standing up a server. The service + routes + client
are covered by their own tests.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from memopad.mcp.tools import codegraph as cg


class _FakeProject:
    def __init__(self, name="test-project", external_id="ext-1"):
        self.name = name
        self.external_id = external_id


class _FakeKnowledgeClient:
    """Returns canned dicts for each CodeGraph method."""

    def __init__(self, client, external_id):
        self.client = client
        self.external_id = external_id

    async def index_codegraph(self, root, *, languages=None):
        return {"files": 2, "entities": 7, "relations": 7, "skipped": 0}

    async def find_symbol(self, name, *, exact=False):
        return {
            "symbols": [
                {
                    "permalink": "code://test-project/a.py::shared",
                    "title": "shared",
                    "entity_type": "function",
                    "qualified_name": "a.shared",
                    "file": "a.py",
                }
            ],
            "count": 1,
        }

    async def impact_path(self, permalink, *, max_hops=5):
        return {
            "root": permalink,
            "count": 1,
            "render": "# Impact path\n\n**direct callers** (1):\n- `code://test-project/b.py::caller`",
        }

    async def code_context(self, permalink, *, max_tokens=0):
        return {
            "permalink": permalink,
            "title": "shared",
            "render": "# Code context: `shared`\n\n```python\ndef shared(x): ...\n```",
        }


@pytest.fixture
def patched(monkeypatch):
    @asynccontextmanager
    async def fake_get_client():
        yield object()

    async def fake_get_active_project(client, project=None, context=None):
        return _FakeProject()

    monkeypatch.setattr(cg, "get_client", fake_get_client)
    monkeypatch.setattr(cg, "get_active_project", fake_get_active_project)
    # The tool imports KnowledgeClient lazily inside the function body.
    import memopad.mcp.clients as clients

    monkeypatch.setattr(clients, "KnowledgeClient", _FakeKnowledgeClient)


@pytest.mark.asyncio
async def test_index_code_renders_summary(patched):
    out = await cg.index_code.fn("/some/path")
    assert "Files:** 2" in out
    assert "Entities:** 7" in out
    assert "find_symbol" in out


@pytest.mark.asyncio
async def test_find_symbol_renders_permalink(patched):
    out = await cg.find_symbol.fn("shared")
    assert "code://test-project/a.py::shared" in out
    assert "function" in out
    assert "a.shared" in out


@pytest.mark.asyncio
async def test_find_symbol_no_matches(patched, monkeypatch):
    import memopad.mcp.clients as clients

    class _Empty(_FakeKnowledgeClient):
        async def find_symbol(self, name, *, exact=False):
            return {"symbols": [], "count": 0}

    monkeypatch.setattr(clients, "KnowledgeClient", _Empty)
    out = await cg.find_symbol.fn("nope")
    assert "No symbols found" in out


@pytest.mark.asyncio
async def test_impact_path_renders(patched):
    out = await cg.impact_path.fn("code://test-project/a.py::shared")
    assert "Impact path" in out
    assert "direct callers" in out


@pytest.mark.asyncio
async def test_code_context_renders(patched):
    out = await cg.code_context.fn("code://test-project/a.py::shared")
    assert "Code context" in out
    assert "def shared" in out