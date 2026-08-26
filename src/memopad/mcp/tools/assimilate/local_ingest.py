"""Local file/directory ingestion for the assimilate tool.

Lets ``assimilate`` consume a path on disk in addition to http(s)/github URLs:

  - a ``file://`` URL or a bare filesystem path
  - a single file -> one page
  - a directory -> one page per supported file (recursive), capped by ``max_pages``

Produces the same ``CrawlResult``-shaped dict the HTTP/crawl strategies build
(``pages``, ``all_github_links``, ``all_external_links``, ``errors``), so note
construction (``build_all_notes``) is reused unchanged. Only reads the filesystem;
never writes outside the normal note-creation path.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from urllib.request import url2pathname

from loguru import logger

from .config import DEFAULT_CONFIG
from .content_detector import detect_content_type
from .file_processor import FileProcessor

# Extensions we'll ingest when walking a directory. Text/code is decoded by
# FileProcessor's UTF-8 fallback; binary docs (pdf/docx/xlsx) and images go
# through their extractors. .doc/.xls are intentionally excluded — the
# extractors only handle the OOXML variants.
_LOCAL_TEXT_EXT = {
    ".md", ".markdown", ".txt", ".rst",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
    ".c", ".cpp", ".h", ".hpp", ".cs",
    ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg",
    ".csv", ".html", ".htm", ".xml", ".sh", ".bash", ".sql", ".log",
}
_LOCAL_BINARY_EXT = {".pdf", ".docx", ".xlsx", ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
_LOCAL_SUPPORTED_EXT = _LOCAL_TEXT_EXT | _LOCAL_BINARY_EXT

# Directory/file basename fragments to skip when walking (VCS, build noise).
_SKIP_DIRS = {".git", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".ruff_cache", ".pytest_cache", "dist", "build", ".cache"}


def local_source(parsed_url, url: str) -> Optional[Path]:
    """Return a local Path if ``url`` points at a file/dir on disk, else None.

    Handles ``file://`` URLs and bare paths that exist on the local filesystem.
    Anything else (http(s), github, malformed) returns None so the caller falls
    through to the remote-URL path.
    """
    if parsed_url.scheme == "file":
        try:
            # url2pathname turns file:///C:/foo -> C:/foo and file:///home/x -> /home/x
            return Path(url2pathname(parsed_url.path))
        except (OSError, ValueError):
            return None
    # Bare path that exists on disk. Guard against strings that aren't valid
    # paths on this OS (they'll raise on Path() construction in some cases).
    try:
        candidate = Path(url)
    except (OSError, ValueError):
        return None
    if candidate.exists():
        return candidate
    return None


def local_label(path: Path) -> str:
    """A filesystem-safe label for the local source, used as the note directory."""
    name = path.stem if path.is_file() else path.name
    label = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
    return label or "local"


def _iter_files(path: Path):
    """Yield files under ``path`` (single file or recursive dir walk)."""
    if path.is_file():
        yield path
        return
    for child in path.rglob("*"):
        if child.is_dir():
            if child.name in _SKIP_DIRS:
                # Prune: rglob doesn't prune, so just skip dir contents via name check
                continue
            continue
        # Skip files under skipped directories.
        if any(part in _SKIP_DIRS for part in child.relative_to(path).parts[:-1]):
            continue
        if child.name.startswith(".") and child.suffix not in _LOCAL_SUPPORTED_EXT:
            continue
        yield child


async def crawl_local(path: Path, max_pages: int = 0) -> dict:
    """Build a CrawlResult-shaped dict from a local file or directory.

    ``max_pages`` caps the number of files processed (0 = unlimited). Each
    page carries detected content types (via ``detect_content_type``) so the
    type-specific note builders fire, mirroring the github/crawl strategies.
    """
    pages: list[dict] = []
    errors: list[str] = []
    count = 0

    for f in _iter_files(path):
        if max_pages and count >= max_pages:
            break

        # For directory walks, only ingest supported extensions; for a single
        # file given explicitly, try regardless of extension (UTF-8 fallback).
        if path.is_dir() and f.suffix.lower() not in _LOCAL_SUPPORTED_EXT:
            continue

        try:
            size = f.stat().st_size
            if size > DEFAULT_CONFIG.max_file_read_size:
                logger.warning(f"local_ingest: skipping oversized file ({size} bytes): {f}")
                continue
            content = f.read_bytes()
        except OSError as e:
            errors.append(f"{f}: {e}")
            continue

        try:
            text = FileProcessor.extract_text_content(content, "", f.name)
        except Exception as e:  # pragma: no cover
            logger.error(f"local_ingest: extract failed for {f}: {e}")
            errors.append(f"{f}: extract failed: {e}")
            continue

        page_url = f"file://{f.as_posix()}"
        content_types = detect_content_type(page_url, text)
        if not content_types:
            content_types = ["file_content"]

        pages.append(
            {
                "url": page_url,
                "text": text,
                "content_types": content_types,
                "links": {"internal": [], "github": [], "external": []},
                "is_file": True,
            }
        )
        count += 1

    return {
        "pages": pages,
        "all_github_links": [],
        "all_external_links": [],
        "errors": errors,
    }