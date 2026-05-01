"""Tests for the StorageOptimizer service."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from memopad.services.optimization_service import (
    StorageOptimizer,
    _normalize_for_hash,
    _redirect_stub,
    format_report,
)


@dataclass
class _FakeProject:
    """Stand-in for the real Project model — only the attrs StorageOptimizer touches."""

    name: str
    path: str


@pytest.fixture
def project(tmp_path: Path) -> _FakeProject:
    return _FakeProject(name="test", path=str(tmp_path))


def write(tmp_path: Path, relpath: str, content: str) -> Path:
    """Helper: write a markdown file under tmp_path."""
    full = tmp_path / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return full


# --- Helpers ---


class TestNormalizeForHash:
    def test_strips_yaml_frontmatter(self):
        with_fm = "---\ntitle: x\n---\n\nbody text\n"
        without_fm = "body text"
        assert _normalize_for_hash(with_fm) == _normalize_for_hash(without_fm)

    def test_canonicalizes_line_endings(self):
        crlf = "line one\r\nline two\r\n"
        lf = "line one\nline two\n"
        assert _normalize_for_hash(crlf) == _normalize_for_hash(lf)

    def test_trims_trailing_whitespace_per_line(self):
        a = "foo  \nbar\t\n"
        b = "foo\nbar\n"
        assert _normalize_for_hash(a) == _normalize_for_hash(b)


class TestRedirectStub:
    def test_contains_redirects_to_relation(self):
        stub = _redirect_stub("notes/canonical.md")
        assert "redirects_to [[notes/canonical]]" in stub

    def test_strips_md_suffix_from_permalink(self):
        stub = _redirect_stub("a/b.md")
        assert "[[a/b]]" in stub
        assert "[[a/b.md]]" not in stub


# --- Service ---


@pytest.mark.asyncio
async def test_get_storage_usage_empty(project, tmp_path):
    optimizer = StorageOptimizer(project)
    usage = await optimizer.get_storage_usage()
    assert usage.total_files == 0
    assert usage.total_size == 0


@pytest.mark.asyncio
async def test_find_duplicates_detects_identical_content(project, tmp_path):
    write(tmp_path, "a.md", "# Same\n\nbody\n")
    write(tmp_path, "b.md", "# Same\n\nbody\n")
    write(tmp_path, "c.md", "# Different\n\nother\n")

    optimizer = StorageOptimizer(project)
    groups = await optimizer.find_duplicates()

    assert len(groups) == 1
    assert len(groups[0].duplicates) == 1


@pytest.mark.asyncio
async def test_find_duplicates_ignores_frontmatter_differences(project, tmp_path):
    # Same body, different frontmatter — should still be flagged as duplicates.
    write(tmp_path, "a.md", "---\ntitle: A\n---\n\nbody\n")
    write(tmp_path, "b.md", "---\ntitle: B\n---\n\nbody\n")

    optimizer = StorageOptimizer(project)
    groups = await optimizer.find_duplicates()
    assert len(groups) == 1


@pytest.mark.asyncio
async def test_find_duplicates_skips_readme(project, tmp_path):
    # README.md is in the skip list — duplicates across dirs are common & legitimate.
    write(tmp_path, "x/README.md", "# Hello\n")
    write(tmp_path, "y/README.md", "# Hello\n")

    optimizer = StorageOptimizer(project)
    groups = await optimizer.find_duplicates()
    assert groups == []


@pytest.mark.asyncio
async def test_find_duplicates_skips_empty_files(project, tmp_path):
    # Empty notes shouldn't all collide with each other.
    write(tmp_path, "a.md", "")
    write(tmp_path, "b.md", "   \n   \n")

    optimizer = StorageOptimizer(project)
    groups = await optimizer.find_duplicates()
    assert groups == []


@pytest.mark.asyncio
async def test_optimize_dry_run_does_not_modify_files(project, tmp_path):
    write(tmp_path, "a.md", "# Same\n\nbody\n")
    write(tmp_path, "b.md", "# Same\n\nbody\n")

    optimizer = StorageOptimizer(project)
    result = await optimizer.optimize(dry_run=True)

    assert result.dry_run is True
    assert result.duplicate_count == 1
    assert result.files_rewritten == 0
    # Both files still have original content.
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == "# Same\n\nbody\n"
    assert (tmp_path / "b.md").read_text(encoding="utf-8") == "# Same\n\nbody\n"


@pytest.mark.asyncio
async def test_optimize_apply_rewrites_duplicates(project, tmp_path):
    write(tmp_path, "a.md", "---\ntitle: A\n---\n\nbody\n")
    write(tmp_path, "b.md", "---\ntitle: B\n---\n\nbody\n")

    optimizer = StorageOptimizer(project)
    result = await optimizer.optimize(dry_run=False)

    assert result.dry_run is False
    assert result.files_rewritten == 1

    # Canonical (oldest) keeps its content; the duplicate becomes a redirect stub
    # but retains its frontmatter.
    canonical = result.duplicate_groups[0].canonical
    duplicate = result.duplicate_groups[0].duplicates[0]
    assert "body" in (tmp_path / canonical).read_text(encoding="utf-8")

    dup_text = (tmp_path / duplicate).read_text(encoding="utf-8")
    assert "redirects_to" in dup_text
    assert "title:" in dup_text  # frontmatter preserved


@pytest.mark.asyncio
async def test_format_report_marks_dry_run_explicitly(project, tmp_path):
    write(tmp_path, "a.md", "# X\n\ny\n")
    write(tmp_path, "b.md", "# X\n\ny\n")

    optimizer = StorageOptimizer(project)
    usage = await optimizer.get_storage_usage()
    result = await optimizer.optimize(dry_run=True)
    report = format_report(usage, result, project.name)

    # The user must be able to see at a glance whether files were touched.
    assert "DRY RUN" in report
    assert "Duplicate groups: 1" in report


def test_init_requires_path_or_home():
    @dataclass
    class _Bare:
        name: str

    with pytest.raises(ValueError, match="path"):
        StorageOptimizer(_Bare(name="x"))
