"""Assimilate tool for Memopad MCP server.

Crawls a URL, extracts knowledge (content, links, agent profiles, skills, rules),
and stores everything as structured notes in memopad.
"""

import asyncio
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
from .note_builders import build_all_notes, build_overview_note, build_github_links_note, build_note, build_functional_diagram_note, truncate_content
from .types import CrawlResult

# HTML utilities (for backward compatibility with tests)
from .html_utils import (
    LinkExtractor,
    HTMLToText,
    extract_links,
    html_to_text,
    categorize_links,
)

# Content detection (for backward compatibility with tests)
from .content_detector import detect_content_type

# Aliases for test compatibility (old naming with underscore prefix)
_build_overview_note = build_overview_note
_build_github_links_note = build_github_links_note
_build_functional_diagram_note = build_functional_diagram_note
_safe_truncate = truncate_content

# Note builder aliases with underscore prefix for test compatibility
def _build_agent_profiles_note(data):
    from .note_builders import NOTE_BUILDERS
    config = next((c for c in NOTE_BUILDERS if c.content_type == "agent_profile"), None)
    if config:
        return build_note(data, config)
    return None

def _build_skills_rules_note(data):
    from .note_builders import NOTE_BUILDERS
    config = next((c for c in NOTE_BUILDERS if c.content_type == "skills_rules"), None)
    if config:
        return build_note(data, config)
    return None

def _build_concepts_note(data):
    from .note_builders import NOTE_BUILDERS
    config = next((c for c in NOTE_BUILDERS if c.content_type == "concepts"), None)
    if config:
        return build_note(data, config)
    return None

def _build_soul_files_note(data):
    from .note_builders import NOTE_BUILDERS
    config = next((c for c in NOTE_BUILDERS if c.content_type == "soul_file"), None)
    if config:
        return build_note(data, config)
    return None

def _build_tools_functions_note(data):
    from .note_builders import NOTE_BUILDERS
    config = next((c for c in NOTE_BUILDERS if c.content_type == "tools_functions"), None)
    if config:
        return build_note(data, config)
    return None

def _build_algorithms_note(data):
    from .note_builders import NOTE_BUILDERS
    config = next((c for c in NOTE_BUILDERS if c.content_type == "algorithms"), None)
    if config:
        return build_note(data, config)
    return None

def _build_decision_structure_note(data):
    from .note_builders import NOTE_BUILDERS
    config = next((c for c in NOTE_BUILDERS if c.content_type == "decision_structure"), None)
    if config:
        return build_note(data, config)
    return None

# Constant aliases for test compatibility
MAX_FILE_READ_SIZE = DEFAULT_CONFIG.max_file_read_size
DEFAULT_MAX_FILES = DEFAULT_CONFIG.default_max_files
MAX_NOTE_CONTENT = DEFAULT_CONFIG.max_note_content


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
                project_path=getattr(active_project, 'path', 'unknown'),
                strategy=strategy,
                max_depth=max_depth,
                max_pages=max_pages,
            )
            assimilate_logger.log_detection(url, strategy)

            from memopad.mcp.clients import KnowledgeClient

            knowledge_client = KnowledgeClient(client, active_project.external_id)

            stored: list[str] = []
            for title, content in notes_to_write:
                try:
                    entity = Entity(
                        title=title,
                        directory=directory,
                        entity_type="note",
                        content_type="text/markdown",
                        content=content,
                        entity_metadata={"tags": ["assimilated", domain]},
                    )
                    try:
                        result = await knowledge_client.create_entity(
                            entity.model_dump(), fast=True
                        )
                    except Exception as e:
                        if (
                            "409" in str(e)
                            or "conflict" in str(e).lower()
                            or "already exists" in str(e).lower()
                        ):
                            if entity.permalink:
                                try:
                                    entity_id = await knowledge_client.resolve_entity(
                                        entity.permalink
                                    )
                                    result = await knowledge_client.update_entity(
                                        entity_id, entity.model_dump(), fast=False
                                    )
                                    global_logger.info(
                                        f"assimilate: updated existing note "
                                        f"'{title}' at {result.permalink}"
                                    )

                                    # Log file update to assimilate logger
                                    file_path = f"{directory}/{title}.md"
                                    assimilate_logger.log_file_saved(
                                        title=title,
                                        file_path=file_path,
                                        permalink=result.permalink,
                                        directory=directory,
                                        operation="updated",
                                        content_length=len(content),
                                    )
                                except Exception as update_err:
                                    global_logger.error(
                                        f"assimilate: update failed for '{title}': {update_err}"
                                    )
                                    raise update_err
                            else:
                                raise
                        else:
                            raise
                    stored.append(f"- {title}: {result.permalink}")
                    global_logger.info(
                        f"assimilate: stored note '{title}' at {result.permalink}"
                    )

                    # Log file save to assimilate logger
                    file_path = f"{directory}/{title}.md"
                    assimilate_logger.log_file_saved(
                        title=title,
                        file_path=file_path,
                        permalink=result.permalink,
                        directory=directory,
                        operation="created",
                        content_length=len(content),
                    )
                except Exception as e:
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

        # Build summary
        summary_lines = [
            "# Assimilation Complete\n",
            f"source: {url}",
            f"project: {active_project.name}",
            f"items_processed: {len(data['pages'])}",
            f"github_links_found: {len(data['all_github_links'])}",
            f"notes_stored: {len(notes_to_write)}",
            f"directory: {directory}",
            "\n## Notes Created\n",
        ]
        summary_lines.extend(stored)

        if data["all_github_links"]:
            summary_lines.append(f"\n## GitHub Links ({len(data['all_github_links'])}\n")
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
