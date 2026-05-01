"""Assimilate tool for Memopad MCP server.

Crawls a URL, extracts knowledge (content, links, agent profiles, skills, rules),
and stores everything as structured notes in memopad.

Incremental assimilation
------------------------
Re-assimilating the same source is cheap. For every note we generate, we
compute a SHA256 of the content body and stash it in
`entity_metadata._assimilate_content_hash` along with the source URL. On
re-run, before issuing an update we fetch the existing entity and compare
hashes — when they match we skip the write entirely. The result:

  - first run:   N created, 0 updated, 0 unchanged
  - re-run, no upstream changes: 0 created, 0 updated, N unchanged (skipped)
  - re-run, only X notes changed: 0 created, X updated, N-X unchanged

This avoids unnecessary file rewrites, sync reindex passes, and (when
embeddings are enabled) embedding regeneration.
"""

import asyncio
import hashlib
import webbrowser
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastmcp import Context
from loguru import logger as global_logger

from memopad.mcp.async_client import get_client
from memopad.mcp.project_context import get_active_project, add_project_metadata
from memopad.mcp.server import mcp
from memopad.schemas.base import Entity

from .config import DEFAULT_CONFIG, DIRECT_DOWNLOAD_EXTENSIONS, DIRECT_DOWNLOAD_CONTENT_TYPES
from .crawler import crawl, get_http_client
from .file_processor import FileProcessor
from .github import clone_github_repo, is_github_repo
from .logger import get_logger as get_assimilate_logger
from .note_builders import build_all_notes
from .types import CrawlResult


# Metadata keys we set on every assimilated entity. The leading underscore
# marks them as "assimilate-internal" so the rest of MemoPad can ignore them.
ASSIMILATE_HASH_KEY = "_assimilate_content_hash"
ASSIMILATE_SOURCE_KEY = "_assimilate_source"


def _content_hash(content: str) -> str:
    """SHA256 of the note body, used to detect whether the content has changed
    since the last assimilation. Body-only — frontmatter is excluded so
    metadata-only differences don't count as content changes.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@mcp.tool(
    description="""Assimilate knowledge from a URL into memopad.

    Crawls the given URL (and linked pages), extracts useful content, and stores
    structured notes in the knowledge base. Automatically detects:
    - Agent profiles & system prompts
    - Skills, rules, and workflow definitions
    - Architectural concepts and design patterns
    - GitHub repository links
    - Documents (PDF, DOCX, XLSX) and Images

    Notes are stored under <domain>/ in the target project.

    Args:
        url: The URL to assimilate (e.g. "https://github.com/org/repo")
        project: Project to store notes in. Optional - uses default if not specified.
        max_depth: How many link-hops deep to crawl (default: 10)
        max_pages: Maximum pages to fetch (default: 0 = unlimited)
        open_browser: Open the URL in the system browser for visualization (default: False)
    """,
)
async def assimilate(
    url: str,
    project: Optional[str] = None,
    max_depth: int = DEFAULT_CONFIG.max_crawl_depth,
    max_pages: int = 0,
    open_browser: bool = False,
    context: Context | None = None,
) -> str:
    """MCP tool wrapper for _assimilate_impl."""
    return await _assimilate_impl(
        url=url,
        project=project,
        max_depth=max_depth,
        max_pages=max_pages,
        open_browser=open_browser,
        context=context,
    )


async def _assimilate_impl(
    url: str,
    project: Optional[str] = None,
    max_depth: int = DEFAULT_CONFIG.max_crawl_depth,
    max_pages: int = 0,
    open_browser: bool = False,
    context: Context | None = None,
) -> str:
    """Assimilate knowledge from a URL into memopad."""
    global_logger.info(
        f"MCP tool call tool=assimilate url={url} "
        f"max_depth={max_depth} max_pages={max_pages} open_browser={open_browser}"
    )

    # Initialize assimilate logger
    assimilate_logger = get_assimilate_logger()
    log_entry = None

    try:
        # Validate URL
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return "# Error\n\nInvalid URL. Please provide a full URL like https://example.com"

        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]

        # Open in browser if requested
        if open_browser:
            try:
                global_logger.info(f"Opening browser for {url}")
                webbrowser.open(url)
            except Exception as e:
                global_logger.error(f"Failed to open browser for {url}: {e}")

        data: CrawlResult | None = None
        strategy = "unknown"

        # Strategy 1: GitHub Repo
        if is_github_repo(url):
            strategy = "github"
            global_logger.info(f"assimilate: detected GitHub repo, cloning {url}")
            data = await clone_github_repo(url, max_files=max_pages)
            path_parts = parsed.path.strip("/").split("/")
            if len(path_parts) >= 2:
                domain = f"{domain}/{path_parts[0]}/{path_parts[1]}"

        # Strategy 2: Check for direct file download
        else:
            is_file_ext = url.lower().endswith(DIRECT_DOWNLOAD_EXTENSIONS)
            should_download_directly = is_file_ext
            content_type = ""

            # If not obvious extension, check Content-Type via HEAD request
            if not should_download_directly:
                try:
                    async with get_http_client() as client:
                        head_resp = await client.head(
                            url, follow_redirects=True, timeout=DEFAULT_CONFIG.head_timeout
                        )
                        content_type = head_resp.headers.get("content-type", "").lower()
                        if any(t in content_type for t in DIRECT_DOWNLOAD_CONTENT_TYPES):
                            should_download_directly = True
                except Exception:
                    pass  # Ignore HEAD errors, fall back to crawl

            if should_download_directly:
                global_logger.info(f"assimilate: detected direct file download for {url}")
                try:
                    async with get_http_client() as client:
                        resp = await client.get(
                            url, follow_redirects=True, timeout=DEFAULT_CONFIG.download_timeout
                        )
                        resp.raise_for_status()
                        content = resp.content
                        if not content_type:
                            content_type = resp.headers.get("content-type", "").lower()

                        text = FileProcessor.extract_text_content(content, content_type, url)

                        # Construct a single-page result
                        data = {
                            "pages": [{
                                "url": str(resp.url),
                                "text": text,
                                "content_types": ["file_content"],
                                "links": {"internal": [], "github": [], "external": []},
                                "is_file": True,
                            }],
                            "all_github_links": [],
                            "all_external_links": [],
                            "errors": [],
                        }
                except Exception as e:
                    global_logger.error(f"Failed to download file {url}: {e}")
                    data = {
                        "pages": [],
                        "all_github_links": [],
                        "all_external_links": [],
                        "errors": [str(e)],
                    }

        # Strategy 3: Generic Crawl (Fallback)
        if data is None:
            global_logger.info(f"assimilate: starting generic crawl of {url}")
            data = await crawl(url, max_depth=max_depth, max_pages=max_pages)

        global_logger.info(
            f"assimilate: processing complete — {len(data['pages'])} pages/files, "
            f"{len(data['all_github_links'])} github links"
        )

        if not data["pages"]:
            if data.get("errors"):
                error_details = "\n".join(f"- {e}" for e in data["errors"])
                return f"# Error\n\nCould not fetch content from {url}:\n\n{error_details}"
            return f"# Error\n\nCould not fetch any content from {url}"

        # Build notes
        global_logger.info("assimilate: building structured notes from gathered data")
        notes_to_write = build_all_notes(url, data)

        # Store notes in memopad
        directory = f"{domain}_Assimilated"

        async with get_client() as client:
            active_project = await get_active_project(client, project, context)

            # Start assimilate logging
            log_entry = assimilate_logger.start_operation(
                url=url,
                project=active_project.name,
                project_path=active_project.path,
                strategy=strategy,
                max_depth=max_depth,
                max_pages=max_pages,
            )
            assimilate_logger.log_detection(url, strategy)

            from memopad.mcp.clients import KnowledgeClient

            knowledge_client = KnowledgeClient(client, active_project.external_id)

            stored: list[str] = []
            counts = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0}

            for title, content in notes_to_write:
                # Compute the body hash up front so we can write it on create
                # and compare it on conflict. Body-only — frontmatter is added
                # by the entity service downstream and isn't part of our hash.
                new_hash = _content_hash(content)
                metadata = {
                    "tags": ["assimilated", domain],
                    ASSIMILATE_HASH_KEY: new_hash,
                    ASSIMILATE_SOURCE_KEY: url,
                }

                try:
                    entity = Entity(
                        title=title,
                        directory=directory,
                        entity_type="note",
                        content_type="text/markdown",
                        content=content,
                        entity_metadata=metadata,
                    )

                    # --- Optimistic create ---
                    try:
                        result = await knowledge_client.create_entity(
                            entity.model_dump(), fast=True
                        )
                        operation = "created"
                        counts["created"] += 1
                    except Exception as e:
                        # Trigger: KnowledgeClient may raise either an httpx.HTTPStatusError
                        #          (HTTP 409) or a domain-level "already exists" error.
                        # Why: prefer typed status check; fall back to message match only when
                        #      the typed path doesn't apply (e.g. service-layer exception).
                        is_conflict = False
                        status = getattr(getattr(e, "response", None), "status_code", None)
                        if status == 409:
                            is_conflict = True
                        else:
                            # Conflicts can surface as:
                            #   - a wrapped HTTP 409 (handled above)
                            #   - the API's own "already exists" / "conflict" wording
                            #   - a raw SQLAlchemy IntegrityError when fast=True
                            #     skips the service-layer translation (the message
                            #     reads "UNIQUE constraint failed: entity.permalink…")
                            msg_lower = str(e).lower()
                            if (
                                "conflict" in msg_lower
                                or "already exists" in msg_lower
                                or "unique constraint failed" in msg_lower
                                or "integrityerror" in type(e).__name__.lower()
                            ):
                                is_conflict = True

                        if not is_conflict or not entity.permalink:
                            raise

                        # --- Conflict path: incremental skip / update ---
                        # Resolve the existing entity and compare its stored hash
                        # to the one we just computed. Match → no-op (skip the
                        # update entirely, including the file rewrite + reindex
                        # it would trigger). Mismatch → update with the new hash.
                        try:
                            entity_id = await knowledge_client.resolve_entity(
                                entity.permalink
                            )
                            existing = await knowledge_client.get_entity(entity_id)
                            existing_meta = existing.entity_metadata or {}
                            existing_hash = existing_meta.get(ASSIMILATE_HASH_KEY)

                            if existing_hash == new_hash:
                                # Content unchanged since last assimilation —
                                # short-circuit. Note: we don't re-resolve from
                                # the file, so a manual edit by the user that
                                # happens to match this hash is treated as
                                # unchanged. That's the right behavior: hash
                                # match ⇒ no semantically meaningful diff.
                                counts["unchanged"] += 1
                                stored.append(
                                    f"- {title}: unchanged ({existing.permalink})"
                                )
                                global_logger.info(
                                    f"assimilate: '{title}' unchanged — skipped"
                                )
                                file_path = f"{directory}/{title}.md"
                                assimilate_logger.log_file_saved(
                                    title=title,
                                    file_path=file_path,
                                    permalink=existing.permalink,
                                    directory=directory,
                                    operation="unchanged",
                                    content_length=len(content),
                                )
                                continue

                            # Hash changed (or no prior hash recorded) — update.
                            result = await knowledge_client.update_entity(
                                entity_id, entity.model_dump(), fast=False
                            )
                            operation = "updated"
                            counts["updated"] += 1
                            global_logger.info(
                                f"assimilate: updated existing note "
                                f"'{title}' at {result.permalink}"
                            )
                        except Exception as update_err:
                            global_logger.error(
                                f"assimilate: update failed for '{title}': {update_err}"
                            )
                            raise update_err

                    # --- Successful create or update ---
                    stored.append(f"- {title}: {operation} ({result.permalink})")
                    file_path = f"{directory}/{title}.md"
                    assimilate_logger.log_file_saved(
                        title=title,
                        file_path=file_path,
                        permalink=result.permalink,
                        directory=directory,
                        operation=operation,
                        content_length=len(content),
                    )

                except Exception as e:
                    counts["failed"] += 1
                    stored.append(f"- {title}: FAILED ({e})")
                    global_logger.error(f"assimilate: failed to store note '{title}': {e}")
                    assimilate_logger.log_error(
                        error_type="save_failed",
                        message=str(e),
                        details={"title": title, "directory": directory},
                    )

        # Complete logging
        assimilate_logger.complete_operation(
            status="completed",
            items_processed=len(data["pages"]),
            github_links_found=len(data["all_github_links"]),
        )

        # Build summary. Reports created/updated/unchanged/failed so the user
        # can immediately see how much of a re-run was cached.
        summary_lines = [
            "# Assimilation Complete\n",
            f"source: {url}",
            f"project: {active_project.name}",
            f"items_processed: {len(data['pages'])}",
            f"github_links_found: {len(data['all_github_links'])}",
            f"notes_total: {len(notes_to_write)}",
            f"  created:   {counts['created']}",
            f"  updated:   {counts['updated']}",
            f"  unchanged: {counts['unchanged']}  (skipped via content-hash match)",
            f"  failed:    {counts['failed']}",
            f"directory: {directory}",
            "\n## Notes\n",
        ]
        summary_lines.extend(stored)

        if data["all_github_links"]:
            summary_lines.append(f"\n## GitHub Links ({len(data['all_github_links'])})\n")
            for gh in data["all_github_links"][:20]:
                summary_lines.append(f"- {gh}")
            if len(data["all_github_links"]) > 20:
                summary_lines.append(
                    f"- ... and {len(data['all_github_links']) - 20} more "
                    "(see GitHub Links Index note)"
                )

        summary_result = "\n".join(summary_lines)
        return add_project_metadata(summary_result, active_project.name)

    except asyncio.CancelledError:
        global_logger.warning(
            f"assimilate: CancelledError for {url} — operation was cancelled"
        )
        assimilate_logger.complete_operation(
            status="cancelled",
            items_processed=0,
            github_links_found=0,
        )
        return (
            f"# Error\n\nAssimilation was cancelled for {url}.\n\n"
            "This can happen during long-running operations on Windows. "
            "Try:\n- Pre-cloning the repo manually\n- Limiting `max_pages`\n"
            "- Re-running the script (partial progress is retained)"
        )
    except Exception as e:
        global_logger.exception(f"assimilate: unhandled error for {url}")
        assimilate_logger.log_error(
            error_type="unhandled_exception",
            message=str(e),
            details={"exception_type": type(e).__name__},
        )
        assimilate_logger.complete_operation(
            status="failed",
            items_processed=0,
            github_links_found=0,
        )
        return (
            f"# Error\n\nAssimilation failed for {url}:\n\n"
            f"**{type(e).__name__}**: {e}\n\n"
            "Please check the MCP server logs for details."
        )
