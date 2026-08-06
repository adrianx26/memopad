"""MCP tool-level tests for the short-term context tools (Tb G6).

These cover the `shortterm_enabled` gate (fail-fast when off) and an enabled
end-to-end flow: record a ref + step, read the injected context (none level),
drill down by node id, and finalize. The filesystem root is redirected to a
tmp_path so no real data dir is touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memopad.config import MemoPadConfig
from memopad.mcp.tools import shortterm as shortterm_tools
from mcp.server.fastmcp.exceptions import ToolError


# `@mcp.tool` wraps each function in a fastmcp FunctionTool; `.fn` is the
# original coroutine. Call through this so tests exercise the real tool body
# without standing up the MCP server.
def _fn(tool):
    return tool.fn


REF = _fn(shortterm_tools.add_session_ref)
STEP = _fn(shortterm_tools.add_session_step)
CTX = _fn(shortterm_tools.get_session_context)
DRILL = _fn(shortterm_tools.drill_down_session)
FINAL = _fn(shortterm_tools.finalize_session)


class _FakeConfigManager:
    """Returns a controlled MemoPadConfig so tools don't read the real config file."""

    def __init__(self, cfg: MemoPadConfig):
        self._cfg = cfg

    def load_config(self) -> MemoPadConfig:
        return self._cfg


def _enabled_cfg(budget: int = 1000) -> MemoPadConfig:
    cfg = MemoPadConfig()
    cfg.shortterm_enabled = True
    cfg.shortterm_context_token_budget = budget
    return cfg


def _disabled_cfg() -> MemoPadConfig:
    cfg = MemoPadConfig()
    cfg.shortterm_enabled = False
    return cfg


@pytest.fixture
def patched(monkeypatch, tmp_path):
    """Patch ConfigManager + sessions root; expose a mutable config object."""
    holder = {}

    def install(cfg: MemoPadConfig) -> None:
        holder["cfg"] = cfg
        monkeypatch.setattr(
            shortterm_tools, "ConfigManager", lambda: _FakeConfigManager(cfg)
        )
        monkeypatch.setattr(
            shortterm_tools, "_sessions_root", lambda app_config: tmp_path / "sessions"
        )

    holder["install"] = install
    return holder


@pytest.mark.asyncio
async def test_disabled_gate_raises_toolerror(patched):
    patched["install"](_disabled_cfg())
    with pytest.raises(ToolError):
        await REF("s1", "r1", "content")
    with pytest.raises(ToolError):
        await STEP("s1", "t", "sum")
    with pytest.raises(ToolError):
        await CTX("s1")


@pytest.mark.asyncio
async def test_unsafe_session_id_rejected(patched):
    patched["install"](_enabled_cfg())
    with pytest.raises(ToolError):
        await REF("../escape", "r1", "x")


@pytest.mark.asyncio
async def test_enabled_end_to_end_flow(patched):
    patched["install"](_enabled_cfg(budget=10_000))  # large budget -> level stays "none"
    sid = "sess-1"

    r = await REF(sid, "out1", "raw output body")
    assert "offload level: none" in r.lower()

    s = await STEP(sid, "read_note", "read architecture", ref_name="out1")
    assert "s0" in s
    assert "offload level: none" in s.lower()

    ctx = await CTX(sid)
    assert "none" in ctx.lower()
    assert "read_note" in ctx
    assert "out1" in ctx  # refs injected at "none"

    dd = await DRILL(sid, "s0")
    assert "read architecture" in dd
    assert "raw output body" in dd  # ref content recovered via drill-down

    fin = await FINAL(sid)
    assert "read_note" in fin
    assert "1 stable steps" in fin.lower() or "1 stable" in fin.lower()


@pytest.mark.asyncio
async def test_aggressive_offload_injects_canvas(patched):
    # Small budget so a big ref pushes the session into aggressive offload.
    patched["install"](_enabled_cfg(budget=1000))
    sid = "sess-2"
    # 3600 chars -> ~900 tokens -> crosses 0.85 * 1000 = 850.
    await REF(sid, "big", "z" * 3600)
    await STEP(sid, "t", "did a thing")
    ctx = await CTX(sid)
    assert "aggressive" in ctx.lower()
    assert "```mermaid" in ctx  # canvas is the injected layer


@pytest.mark.asyncio
async def test_finalize_clear_removes_session(patched, tmp_path):
    patched["install"](_enabled_cfg(budget=10_000))
    sid = "sess-3"
    await STEP(sid, "t", "s")
    session_dir = tmp_path / "sessions" / sid
    assert session_dir.is_dir()
    await FINAL(sid, clear=True)
    assert not session_dir.exists()


@pytest.mark.asyncio
async def test_drill_down_bad_node_id_raises(patched):
    patched["install"](_enabled_cfg(budget=10_000))
    sid = "sess-4"
    await STEP(sid, "t", "s")
    with pytest.raises(ToolError):
        await DRILL(sid, "nope")


def test_sessions_root_uses_data_dir_path_property_without_parens():
    # Regression: `data_dir_path` is a @property on MemoPadConfig (returns a
    # Path), not a method. Calling it with parens raises `TypeError: 'WindowsPath'
    # object is not callable` and breaks every G6 tool the moment
    # `shortterm_enabled` is turned on. The other test cases monkeypatch
    # `_sessions_root`, so they never exercised the real one — which is why this
    # bug slipped through. Here we call the real `_sessions_root` (no patch) and
    # assert it resolves without raising and points under the config's data dir.
    cfg = MemoPadConfig()
    root = shortterm_tools._sessions_root(cfg)
    assert root == Path(cfg.data_dir_path) / "sessions"