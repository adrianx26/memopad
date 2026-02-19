"""Tests for project context utilities (no standard-library mock usage).

These functions are config/env driven, so we use the real ConfigManager-backed
test config file and pytest monkeypatch for environment variables.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_uses_env_var_priority(config_manager, monkeypatch):
    from memopad.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    cfg.default_project_mode = False
    config_manager.save_config(cfg)

    monkeypatch.setenv("MEMOPAD_MCP_PROJECT", "env-project")
    assert await resolve_project_parameter(project="explicit-project") == "env-project"


@pytest.mark.asyncio
async def test_uses_explicit_project(config_manager, monkeypatch):
    from memopad.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    cfg.default_project_mode = False
    config_manager.save_config(cfg)

    monkeypatch.delenv("MEMOPAD_MCP_PROJECT", raising=False)
    assert await resolve_project_parameter(project="explicit-project") == "explicit-project"


@pytest.mark.asyncio
async def test_uses_default_project(config_manager, config_home, monkeypatch):
    from memopad.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    cfg.default_project_mode = True
    # default_project must exist in the config project list, otherwise config validation
    # will coerce it back to an existing default.
    (config_home / "default-project").mkdir(parents=True, exist_ok=True)
    cfg.projects["default-project"] = str(config_home / "default-project")
    cfg.default_project = "default-project"
    config_manager.save_config(cfg)

    monkeypatch.delenv("MEMOPAD_MCP_PROJECT", raising=False)
    assert await resolve_project_parameter(project=None) == "default-project"


@pytest.mark.asyncio
async def test_returns_none_when_no_resolution(config_manager, monkeypatch):
    from memopad.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    cfg.default_project_mode = False
    config_manager.save_config(cfg)

    monkeypatch.delenv("MEMOPAD_MCP_PROJECT", raising=False)
    assert await resolve_project_parameter(project=None) is None
