import pytest
from pathlib import Path
from memopad.services.file_service import FileService

@pytest.mark.asyncio
async def test_content_type_security(file_service: FileService):
    """Test that dangerous content types are mapped to text/plain."""
    # HTML
    assert file_service.content_type("test.html") == "text/plain"
    assert file_service.content_type("test.htm") == "text/plain"

    # XHTML
    assert file_service.content_type("test.xhtml") == "text/plain"

    # SVG
    assert file_service.content_type("test.svg") == "text/plain"

    # Safe types
    assert file_service.content_type("test.txt") == "text/plain"
    # Note: text/markdown might be text/plain depending on environment,
    # but FileService.is_markdown depends on it being text/markdown.
    # In this environment, repro showed it is text/markdown.
    assert file_service.content_type("test.md") == "text/markdown"
    assert file_service.content_type("test.png") == "image/png"
    assert file_service.content_type("test.json") == "application/json"

    # Canvas
    assert file_service.content_type("test.canvas") == "application/json"
