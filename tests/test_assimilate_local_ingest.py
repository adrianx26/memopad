"""Tests for the assimilate local file/directory ingestion path (Bug 10).

Pure-filesystem: no DB, no MCP server, no network. Validates that
`local_source` resolves file:// URLs and bare paths, and that `crawl_local`
walks a directory, classifies content, and skips unsupported extensions.
"""

import asyncio
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from memopad.mcp.tools.assimilate.local_ingest import (
    crawl_local,
    local_label,
    local_source,
)


def test_local_source_resolves_bare_existing_path():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        assert local_source(urlparse(str(p)), str(p)) == p


def test_local_source_resolves_file_uri():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        furl = p.as_uri()  # file:///C:/... or file:///tmp/...
        assert local_source(urlparse(furl), furl) == p


def test_local_source_returns_none_for_remote_url():
    assert local_source(urlparse("https://github.com/org/repo"), "https://github.com/org/repo") is None
    assert local_source(urlparse("https://example.com"), "https://example.com") is None


def test_local_source_returns_none_for_nonexistent_bare_path():
    assert local_source(urlparse("/does/not/exist/anywhere"), "/does/not/exist/anywhere") is None


def test_local_label_sanitizes():
    with tempfile.TemporaryDirectory(prefix="weird name!! ") as d:
        p = Path(d)
        label = local_label(p)
        assert " " not in label
        assert "!" not in label
        assert label  # non-empty


def test_crawl_local_walks_directory_and_classifies():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        (p / "README.md").write_text("# Hello\n\nThis is an AGENTS.md-style agent profile.\n")
        (p / "sub").mkdir()
        (p / "sub" / "app.py").write_text('def tool_use():\n    @app.get("/x")\n    pass\n')

        res = asyncio.run(crawl_local(p, max_pages=0))

        assert res["errors"] == []
        urls = [pg["url"] for pg in res["pages"]]
        assert any(u.endswith("README.md") for u in urls)
        assert any(u.endswith("app.py") for u in urls)

        readme = next(pg for pg in res["pages"] if pg["url"].endswith("README.md"))
        assert "agent_profile" in readme["content_types"]
        app = next(pg for pg in res["pages"] if pg["url"].endswith("app.py"))
        assert "tools_functions" in app["content_types"]
        # CrawlResult shape parity with the HTTP/github strategies.
        assert readme["links"] == {"internal": [], "github": [], "external": []}
        assert readme["is_file"] is True


def test_crawl_local_single_file_processes_regardless_of_extension():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d, "notes.unknownext")
        f.write_text("just text")
        res = asyncio.run(crawl_local(f))
        assert len(res["pages"]) == 1
        assert res["pages"][0]["text"] == "just text"


def test_crawl_local_directory_skips_unsupported_extensions():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        (p / "keep.md").write_text("# keep me")
        (p / "skip.unknownext").write_text("x")
        res = asyncio.run(crawl_local(p))
        urls = [pg["url"] for pg in res["pages"]]
        assert any(u.endswith("keep.md") for u in urls)
        assert not any(u.endswith("skip.unknownext") for u in urls)


def test_crawl_local_respects_max_pages():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        for i in range(5):
            (p / f"f{i}.md").write_text(f"# f{i}")
        res = asyncio.run(crawl_local(p, max_pages=2))
        assert len(res["pages"]) == 2


def test_crawl_local_skips_vcs_and_build_dirs():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        (p / "keep.md").write_text("# keep")
        (p / ".git").mkdir()
        (p / ".git" / "config").write_text("git stuff")
        (p / "node_modules").mkdir()
        (p / "node_modules" / "pkg.json").write_text("{}")
        res = asyncio.run(crawl_local(p))
        urls = [pg["url"] for pg in res["pages"]]
        assert any(u.endswith("keep.md") for u in urls)
        assert not any(".git" in u for u in urls)
        assert not any("node_modules" in u for u in urls)