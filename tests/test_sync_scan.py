
import pytest
import os
from pathlib import Path
from unittest.mock import MagicMock
from memopad.sync.sync_service import SyncService

@pytest.mark.asyncio
async def test_scan_directory_iterative(tmp_path, monkeypatch):
    # Setup directory structure
    root = tmp_path / "test_scan"
    root.mkdir()

    (root / "file1.txt").touch()
    (root / "ignored.txt").touch()
    (root / "sub").mkdir()
    (root / "sub" / "file2.txt").touch()
    (root / "sub" / "ignored_sub.txt").touch()
    (root / "node_modules").mkdir()
    (root / "node_modules" / "dep.js").touch()
    (root / "temp.tmp").touch()

    # Mock load_bmignore_patterns
    patterns = {
        "ignored.txt",
        "ignored_sub.txt",
        "node_modules",
        "*.tmp"
    }
    monkeypatch.setattr("memopad.sync.sync_service.load_bmignore_patterns", lambda: patterns)

    # Instantiate SyncService with mocks for dependencies
    # We pass None or Mocks. The scan_directory method mostly uses aiofiles and local logic.
    service = SyncService(
        app_config=MagicMock(),
        entity_service=MagicMock(),
        entity_parser=MagicMock(),
        entity_repository=MagicMock(),
        relation_repository=MagicMock(),
        project_repository=MagicMock(),
        search_service=MagicMock(),
        file_service=MagicMock()
    )

    # Run scan
    files = []
    async for path, stat in service.scan_directory(root):
        rel_path = Path(path).relative_to(root).as_posix()
        files.append(rel_path)

    files.sort()

    # Expected: file1.txt, sub/file2.txt
    # Ignored: ignored.txt, sub/ignored_sub.txt, node_modules/*, *.tmp

    assert "file1.txt" in files
    assert "sub/file2.txt" in files

    assert "ignored.txt" not in files
    assert "sub/ignored_sub.txt" not in files
    assert "node_modules/dep.js" not in files
    assert "temp.tmp" not in files
    assert len(files) == 2
