"""Tests for storage optimization tool with SQLite integration."""

import pytest

from memopad.mcp.tools import optimize_storage


@pytest.mark.asyncio
async def test_optimize_storage_basic_functionality(app, test_project):
    """Test that optimize_storage tool can be called successfully.
    
    This test verifies:
    1. The optimize_storage tool is accessible
    2. It can be called with a test project
    3. It returns a valid response with expected structure
    """
    result = await optimize_storage.fn(
        project=test_project.name,
    )
    
    # Verify we got a response
    assert result is not None
    assert isinstance(result, str)
    assert len(result) > 0
    
    # Verify the report contains expected sections
    assert "Storage Optimization Report" in result
    assert "Current Storage Usage" in result
    assert "Optimization Results" in result
    
    # Check that it mentions the test project name
    assert test_project.name in result
    
    # Verify statistics are present (test project contains default files)
    assert "Total files:" in result
    assert "Total size:" in result
    assert "Files processed:" in result
    assert "Files optimized:" in result


@pytest.mark.asyncio
async def test_optimize_storage_uses_default_project(app, test_project, monkeypatch):
    """Test optimize_storage uses default project when available.

    This test verifies the tool can determine the active project from context
    when a default project is configured.
    """
    # Patch get_active_project at the module level where it's imported
    import sys
    opt_module = sys.modules["memopad.mcp.tools.optimize_storage"]

    async def mock_get_active_project(client, project=None, context=None):
        return test_project

    monkeypatch.setattr(opt_module, "get_active_project", mock_get_active_project)

    result = await optimize_storage.fn()

    # Verify we got a response
    assert result is not None
    assert isinstance(result, str)
    assert len(result) > 0

    # Should still mention test project (fixture)
    assert test_project.name in result
