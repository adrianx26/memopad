"""Tests for MarkdownImporter per-file error capture (Phase 6.1).

Previously the importer logged per-file failures and only incremented a skip
count — the actual error messages were lost. Phase 6.1 adds an `errors` list to
`ImportResult` and collects them, so callers (e.g. the batch_import tool) can
surface what actually went wrong.
"""

import pytest

from memopad.importers.markdown_importer import MarkdownImporter


class _FakeFileService:
    async def ensure_directory(self, target_dir):
        return None


@pytest.mark.asyncio
async def test_markdown_importer_captures_per_file_errors(tmp_path):
    """A file that fails to write is counted as skipped AND its error captured."""

    src = tmp_path / "src"
    src.mkdir()
    (src / "note.md").write_text("# My Note\n\nSome content here.", encoding="utf-8")

    class _FailingImporter(MarkdownImporter):
        async def write_entity(self, entity, file_path):
            raise RuntimeError("disk full")

    importer = _FailingImporter(
        base_path=tmp_path, markdown_processor=None, file_service=_FakeFileService()
    )

    result = await importer.import_data(source_data=str(src), destination_folder="imported")

    assert result.success is True  # the batch as a whole still succeeds
    assert result.entities == 0
    assert result.skipped_entities == 1
    assert len(result.errors) == 1
    assert "disk full" in result.errors[0]
    assert "note.md" in result.errors[0]


@pytest.mark.asyncio
async def test_markdown_importer_errors_empty_on_clean_import(tmp_path):
    """No per-file errors -> errors list is empty (not None, not absent)."""

    src = tmp_path / "src"
    src.mkdir()
    (src / "note.md").write_text("# Clean Note\n\nBody.", encoding="utf-8")

    class _OkImporter(MarkdownImporter):
        async def write_entity(self, entity, file_path):
            return None  # success, no disk write needed for this test

    importer = _OkImporter(
        base_path=tmp_path, markdown_processor=None, file_service=_FakeFileService()
    )

    result = await importer.import_data(source_data=str(src), destination_folder="imported")

    assert result.success is True
    assert result.entities == 1
    assert result.errors == []