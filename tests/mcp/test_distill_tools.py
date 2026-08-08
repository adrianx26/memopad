"""MCP tool-level tests for the distillation tools (Tb L0-L3).

The tools talk to the server over HTTP; here we monkeypatch the HTTP seam
(`get_client`, `get_active_project`, `KnowledgeClient`) so the tool *rendering*
logic is exercised without standing up a server. The service + routes + client
are covered by their own tests.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from memopad.mcp.tools import distill as distill_tools


class _FakeProject:
    def __init__(self, name="test-project", external_id="ext-1"):
        self.name = name
        self.external_id = external_id


class _FakeKnowledgeClient:
    """Returns canned dicts/lists for each distillation method.

    Subclass and override individual methods to vary the canned data.
    """

    def __init__(self, client, external_id):
        self.client = client
        self.external_id = external_id

    async def distill_memory(self, level, *, max_memories=50):
        return {"l1_facts": 3, "l2_scenarios": 1, "l3_persona": 1}

    async def list_facts(self, *, limit=200):
        return [
            {
                "permalink": "memory://levels/L1/facts/a-thing-abc123",
                "title": "a thing",
                "entity_metadata": {"confidence": "0.80", "category": "definition"},
            }
        ]

    async def list_scenarios(self, *, limit=200):
        return [
            {
                "permalink": "memory://levels/L2/scenarios/cluster-1",
                "title": "cluster-1",
                "entity_metadata": {"source_entities": ["s1", "s2"]},
            }
        ]

    async def get_persona(self):
        return {
            "title": "persona",
            "content": "# Persona\n\n- [fact] A thing is a thing",
            "entity_metadata": {"source_entities": ["s1", "s2", "s3"]},
        }


@pytest.fixture
def patched(monkeypatch):
    @asynccontextmanager
    async def fake_get_client():
        yield object()

    async def fake_get_active_project(client, project=None, context=None):
        return _FakeProject()

    monkeypatch.setattr(distill_tools, "get_client", fake_get_client)
    monkeypatch.setattr(distill_tools, "get_active_project", fake_get_active_project)
    # The tool imports KnowledgeClient lazily inside the function body.
    import memopad.mcp.clients as clients

    monkeypatch.setattr(clients, "KnowledgeClient", _FakeKnowledgeClient)


@pytest.mark.asyncio
async def test_distill_memory_renders_counts(patched):
    out = await distill_tools.distill_memory.fn("L1,L2,L3")
    assert "L1 facts:** 3" in out
    assert "L2 scenarios:** 1" in out
    assert "L3 persona:** 1" in out
    assert "list_facts" in out


@pytest.mark.asyncio
async def test_list_facts_renders_permalink_and_confidence(patched):
    out = await distill_tools.list_facts.fn()
    assert "L1 facts (1)" in out
    assert "memory://levels/L1/facts/a-thing-abc123" in out
    assert "definition" in out
    assert "conf=0.80" in out


@pytest.mark.asyncio
async def test_list_facts_empty_guides_to_distill(patched, monkeypatch):
    import memopad.mcp.clients as clients

    class _Empty(_FakeKnowledgeClient):
        async def list_facts(self, *, limit=200):
            return []

    monkeypatch.setattr(clients, "KnowledgeClient", _Empty)
    out = await distill_tools.list_facts.fn()
    assert "No L1 facts distilled yet" in out
    assert "distill_memory" in out


@pytest.mark.asyncio
async def test_list_scenarios_renders_source_count(patched):
    out = await distill_tools.list_scenarios.fn()
    assert "L2 scenarios (1)" in out
    assert "memory://levels/L2/scenarios/cluster-1" in out
    assert "2 facts" in out


@pytest.mark.asyncio
async def test_get_persona_renders_content(patched):
    out = await distill_tools.get_persona.fn()
    assert "Persona — persona" in out
    assert "3 stable facts aggregated" in out
    assert "A thing is a thing" in out


@pytest.mark.asyncio
async def test_get_persona_missing_guides_to_distill(patched, monkeypatch):
    from mcp.server.fastmcp.exceptions import ToolError

    import memopad.mcp.clients as clients

    class _NoPersona(_FakeKnowledgeClient):
        async def get_persona(self):
            raise ToolError("404: no persona")

    monkeypatch.setattr(clients, "KnowledgeClient", _NoPersona)
    out = await distill_tools.get_persona.fn()
    assert "No L3 persona has been distilled yet" in out
    assert "distill_memory" in out