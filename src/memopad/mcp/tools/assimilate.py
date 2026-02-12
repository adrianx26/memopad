"""Assimilate tool for Basic Memory MCP server.

Crawls a URL, extracts knowledge (content, links, agent profiles, skills, rules),
and stores everything as structured notes in memopad.
"""

import asyncio
import re
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from fastmcp import Context
from loguru import logger

from memopad.mcp.async_client import get_client
from memopad.mcp.project_context import get_active_project, add_project_metadata
from memopad.mcp.server import mcp
from memopad.schemas.base import Entity


# ---------------------------------------------------------------------------
# HTML helpers (stdlib only)
# ---------------------------------------------------------------------------

class LinkExtractor(HTMLParser):
    """Extract all href links from HTML."""

    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for attr, value in attrs:
                if attr == "href" and value:
                    self.links.append(value)


class HTMLToText(HTMLParser):
    """Convert HTML to plain readable text (lightweight, no deps)."""

    SKIP_TAGS = frozenset([
        "script", "style", "noscript", "svg", "head", "nav", "footer",
        "template", "iframe",
    ])
    BLOCK_TAGS = frozenset([
        "p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6",
        "li", "tr", "blockquote", "pre", "section", "article",
        "header", "main", "aside", "details", "summary", "figcaption",
    ])
    HEADING_TAGS = frozenset(["h1", "h2", "h3", "h4", "h5", "h6"])

    def __init__(self):
        super().__init__()
        self._pieces: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in self.HEADING_TAGS:
            level = int(tag[1])
            self._pieces.append("\n" + "#" * level + " ")
        elif tag in self.BLOCK_TAGS:
            self._pieces.append("\n")
        elif tag == "br":
            self._pieces.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in self.BLOCK_TAGS or tag in self.HEADING_TAGS:
            self._pieces.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        self._pieces.append(data)

    def get_text(self) -> str:
        raw = "".join(self._pieces)
        # Collapse excessive blank lines
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def extract_links(html: str, base_url: str) -> list[str]:
    """Extract and resolve all links from HTML."""
    parser = LinkExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    resolved = []
    for href in parser.links:
        # Skip anchors, javascript, mailto
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        full = urljoin(base_url, href)
        # Strip fragment
        full = full.split("#")[0]
        if full:
            resolved.append(full)
    return resolved


def html_to_text(html: str) -> str:
    """Convert HTML to readable plain text."""
    parser = HTMLToText()
    try:
        parser.feed(html)
    except Exception:
        return ""
    return parser.get_text()


def categorize_links(links: list[str], base_domain: str) -> dict[str, list[str]]:
    """Categorize links into internal, github, and external."""
    internal: list[str] = []
    github: list[str] = []
    external: list[str] = []
    seen: set[str] = set()

    for link in links:
        if link in seen:
            continue
        seen.add(link)
        parsed = urlparse(link)
        domain = parsed.netloc.lower()

        if "github.com" in domain or "raw.githubusercontent.com" in domain:
            github.append(link)
        elif domain == base_domain or domain.endswith("." + base_domain):
            internal.append(link)
        else:
            external.append(link)

    return {"internal": internal, "github": github, "external": external}


# ---------------------------------------------------------------------------
# Content detection helpers
# ---------------------------------------------------------------------------

AGENT_PROFILE_PATTERNS = re.compile(
    r"(AGENTS\.md|CLAUDE\.md|SYSTEM_PROMPT|system[_-]?prompt|agent[_-]?profile|"
    r"\.cursorrules|cursor[_-]?rules|copilot[_-]?instructions|\.github/copilot)",
    re.IGNORECASE,
)

SKILLS_PATTERNS = re.compile(
    r"(SKILL\.md|skills/|\.agent/|workflows/|\.gemini/|"
    r"rules[_-]?file|RULES\.md|\.clinerules|\.windsurfrules)",
    re.IGNORECASE,
)

CONFIG_PATTERNS = re.compile(
    r"(README\.md|readme\.md|pyproject\.toml|package\.json|Cargo\.toml|"
    r"setup\.py|setup\.cfg|Makefile|justfile|docker-compose|Dockerfile)",
    re.IGNORECASE,
)


def detect_content_type(url: str, text: str) -> list[str]:
    """Identify what kind of useful content a page contains."""
    types: list[str] = []
    combined = url + "\n" + text[:3000]

    if AGENT_PROFILE_PATTERNS.search(combined):
        types.append("agent_profile")
    if SKILLS_PATTERNS.search(combined):
        types.append("skills_rules")
    if CONFIG_PATTERNS.search(combined):
        types.append("config_docs")

    # Detect conceptual content
    concept_keywords = [
        "architecture", "design pattern", "framework", "plugin system",
        "extension", "middleware", "pipeline", "workflow engine",
        "knowledge graph", "semantic", "embedding", "vector",
        "rag", "retrieval", "agent", "tool use", "function calling",
    ]
    text_lower = text[:5000].lower()
    if sum(1 for kw in concept_keywords if kw in text_lower) >= 2:
        types.append("concepts")

    return types


# ---------------------------------------------------------------------------
# Crawler
# ---------------------------------------------------------------------------

async def _fetch_page(
    http_client: httpx.AsyncClient, url: str
) -> tuple[str, str] | None:
    """Fetch a single page. Returns (html, final_url) or None on failure."""
    try:
        resp = await http_client.get(
            url,
            follow_redirects=True,
            timeout=httpx.Timeout(15.0),
        )
        if resp.status_code != 200:
            logger.debug(f"assimilate: HTTP {resp.status_code} for {url}")
            return None
        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type and "text/plain" not in content_type:
            # Skip binary files, images, etc.
            logger.debug(f"assimilate: skipping non-text content at {url}")
            return None
        return resp.text, str(resp.url)
    except Exception as e:
        logger.debug(f"assimilate: failed to fetch {url}: {e}")
        return None


async def crawl(
    start_url: str, max_depth: int = 2, max_pages: int = 30
) -> dict:
    """Crawl starting from a URL, returning structured results.

    Returns dict with keys:
      - pages: list of {url, text, content_types, links}
      - all_github_links: deduplicated list of GitHub URLs
      - all_external_links: deduplicated list of external URLs
      - errors: list of URLs that failed
    """
    parsed_start = urlparse(start_url)
    base_domain = parsed_start.netloc.lower()

    visited: set[str] = set()
    pages: list[dict] = []
    all_github: set[str] = set()
    all_external: set[str] = set()
    errors: list[str] = []

    queue: list[tuple[str, int]] = [(start_url, 0)]

    headers = {
        "User-Agent": "Memopad-Assimilate/1.0 (knowledge-crawler)",
        "Accept": "text/html,text/plain,application/xhtml+xml",
    }

    async with httpx.AsyncClient(headers=headers) as http_client:
        while queue and len(pages) < max_pages:
            url, depth = queue.pop(0)

            # Normalize trailing slash
            normalized = url.rstrip("/")
            if normalized in visited or url in visited:
                continue
            visited.add(url)
            visited.add(normalized)

            result = await _fetch_page(http_client, url)
            if result is None:
                errors.append(url)
                continue

            html, final_url = result
            text = html_to_text(html)
            links = extract_links(html, final_url)
            categorized = categorize_links(links, base_domain)

            content_types = detect_content_type(url, text)

            pages.append({
                "url": final_url,
                "text": text,
                "content_types": content_types,
                "links": categorized,
            })

            all_github.update(categorized["github"])
            all_external.update(categorized["external"])

            # Queue internal links for deeper crawling
            if depth < max_depth:
                for internal_link in categorized["internal"]:
                    if internal_link not in visited and internal_link.rstrip("/") not in visited:
                        queue.append((internal_link, depth + 1))

            # Also queue GitHub links (they often have READMEs with useful info)
            if depth < max_depth:
                for gh_link in categorized["github"]:
                    if gh_link not in visited and gh_link.rstrip("/") not in visited:
                        queue.append((gh_link, depth + 1))

            # Rate limit
            await asyncio.sleep(0.5)

    return {
        "pages": pages,
        "all_github_links": sorted(all_github),
        "all_external_links": sorted(all_external),
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Note builders
# ---------------------------------------------------------------------------

def _build_overview_note(start_url: str, data: dict) -> str:
    """Build the main overview note content."""
    lines = [
        f"# Assimilated: {start_url}\n",
        f"- [source] Source URL: {start_url}",
        f"- [stats] Pages crawled: {len(data['pages'])}",
        f"- [stats] GitHub links found: {len(data['all_github_links'])}",
        f"- [stats] External links found: {len(data['all_external_links'])}",
        f"- [stats] Errors: {len(data['errors'])}",
        "",
        "## Pages Crawled\n",
    ]
    for page in data["pages"]:
        types_str = ", ".join(page["content_types"]) if page["content_types"] else "general"
        lines.append(f"- [{types_str}] {page['url']}")

    if data["errors"]:
        lines.append("\n## Errors\n")
        for err in data["errors"]:
            lines.append(f"- {err}")

    # Add a summary of the first page content (usually the main README)
    if data["pages"]:
        first_text = data["pages"][0]["text"][:2000]
        lines.append("\n## Main Page Summary\n")
        lines.append(first_text)

    return "\n".join(lines)


def _build_agent_profiles_note(data: dict) -> str | None:
    """Build note for discovered agent profiles and system prompts."""
    sections = []
    for page in data["pages"]:
        if "agent_profile" in page.get("content_types", []):
            sections.append(f"## From: {page['url']}\n")
            # Take meaningful portion of text
            sections.append(page["text"][:3000])
            sections.append("")

    if not sections:
        return None

    header = "# Agent Profiles & System Prompts\n\n"
    header += "- [category] Extracted agent profiles, system prompts, and AI instructions\n\n"
    return header + "\n".join(sections)


def _build_skills_rules_note(data: dict) -> str | None:
    """Build note for discovered skills, rules, and workflows."""
    sections = []
    for page in data["pages"]:
        if "skills_rules" in page.get("content_types", []):
            sections.append(f"## From: {page['url']}\n")
            sections.append(page["text"][:3000])
            sections.append("")

    if not sections:
        return None

    header = "# Skills, Rules & Workflows\n\n"
    header += "- [category] Extracted skills definitions, rules files, and workflow patterns\n\n"
    return header + "\n".join(sections)


def _build_concepts_note(data: dict) -> str | None:
    """Build note for discovered concepts and ideas."""
    sections = []
    for page in data["pages"]:
        if "concepts" in page.get("content_types", []):
            sections.append(f"## From: {page['url']}\n")
            sections.append(page["text"][:3000])
            sections.append("")

    if not sections:
        return None

    header = "# Concepts & Ideas\n\n"
    header += "- [category] Extracted architectural concepts, design patterns, and ideas\n\n"
    return header + "\n".join(sections)


def _build_github_links_note(data: dict) -> str | None:
    """Build the GitHub links index note."""
    if not data["all_github_links"]:
        return None

    lines = [
        "# GitHub Links Index\n",
        "- [category] All GitHub links discovered during assimilation\n",
    ]
    for link in data["all_github_links"]:
        lines.append(f"- {link}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main tool
# ---------------------------------------------------------------------------

@mcp.tool(
    description="""Assimilate knowledge from a URL into memopad.

    Crawls the given URL (and linked pages), extracts useful content, and stores
    structured notes in the knowledge base. Automatically detects:
    - Agent profiles & system prompts
    - Skills, rules, and workflow definitions
    - Architectural concepts and design patterns
    - GitHub repository links

    Notes are stored under Assimilated/<domain>/ in the target project.

    Args:
        url: The URL to assimilate (e.g. "https://github.com/org/repo")
        project: Project to store notes in. Optional - uses default if not specified.
        max_depth: How many link-hops deep to crawl (default: 2)
        max_pages: Maximum number of pages to fetch (default: 30)
    """,
)
async def assimilate(
    url: str,
    project: Optional[str] = None,
    max_depth: int = 2,
    max_pages: int = 30,
    context: Context | None = None,
) -> str:
    """Assimilate knowledge from a URL into memopad.

    Crawls the URL and linked pages, extracts agent profiles, skills, rules,
    concepts, and GitHub links, then stores everything as structured notes.

    Project Resolution:
    Server resolves projects in this order: Single Project Mode → project parameter → default project.
    If project unknown, use list_memory_projects() or recent_activity() first.

    Args:
        url: The starting URL to crawl and assimilate
        project: Project name to store notes in. Optional.
        max_depth: Maximum crawl depth from start URL (default: 2)
        max_pages: Maximum total pages to crawl (default: 30)
        context: Optional FastMCP context for performance caching.

    Returns:
        Summary of what was crawled and stored.

    Examples:
        assimilate("https://github.com/org/repo")
        assimilate("https://github.com/org/repo", project="research", max_depth=3)

    Raises:
        ValueError: If URL is invalid or project doesn't exist
    """
    logger.info(f"MCP tool call tool=assimilate url={url} max_depth={max_depth} max_pages={max_pages}")

    # Validate URL
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return "# Error\n\nInvalid URL. Please provide a full URL like https://example.com"

    domain = parsed.netloc.lower()

    # Crawl
    logger.info(f"assimilate: starting crawl of {url}")
    data = await crawl(url, max_depth=max_depth, max_pages=max_pages)
    logger.info(
        f"assimilate: crawl complete — {len(data['pages'])} pages, "
        f"{len(data['all_github_links'])} github links"
    )

    if not data["pages"]:
        return f"# Error\n\nCould not fetch any content from {url}"

    # Build notes
    notes_to_write: list[tuple[str, str]] = []

    overview = _build_overview_note(url, data)
    notes_to_write.append(("Overview", overview))

    agent_note = _build_agent_profiles_note(data)
    if agent_note:
        notes_to_write.append(("Agent Profiles", agent_note))

    skills_note = _build_skills_rules_note(data)
    if skills_note:
        notes_to_write.append(("Skills and Rules", skills_note))

    concepts_note = _build_concepts_note(data)
    if concepts_note:
        notes_to_write.append(("Concepts and Ideas", concepts_note))

    github_note = _build_github_links_note(data)
    if github_note:
        notes_to_write.append(("GitHub Links Index", github_note))

    # Store notes in memopad
    directory = f"Assimilated/{domain}"

    async with get_client() as client:
        active_project = await get_active_project(client, project, context)

        from memopad.mcp.clients import KnowledgeClient
        knowledge_client = KnowledgeClient(client, active_project.external_id)

        stored: list[str] = []
        for title, content in notes_to_write:
            entity = Entity(
                title=title,
                directory=directory,
                entity_type="note",
                content_type="text/markdown",
                content=content,
                entity_metadata={"tags": ["assimilated", domain]},
            )
            try:
                try:
                    result = await knowledge_client.create_entity(entity.model_dump(), fast=False)
                except Exception as e:
                    if "409" in str(e) or "conflict" in str(e).lower() or "already exists" in str(e).lower():
                        if entity.permalink:
                            entity_id = await knowledge_client.resolve_entity(entity.permalink)
                            result = await knowledge_client.update_entity(
                                entity_id, entity.model_dump(), fast=False
                            )
                        else:
                            raise
                    else:
                        raise
                stored.append(f"- {title}: {result.permalink}")
                logger.info(f"assimilate: stored note '{title}' at {result.permalink}")
            except Exception as e:
                stored.append(f"- {title}: FAILED ({e})")
                logger.error(f"assimilate: failed to store note '{title}': {e}")

    # Build summary
    summary_lines = [
        "# Assimilation Complete\n",
        f"source: {url}",
        f"project: {active_project.name}",
        f"pages_crawled: {len(data['pages'])}",
        f"github_links_found: {len(data['all_github_links'])}",
        f"notes_stored: {len(notes_to_write)}",
        f"directory: {directory}",
        "\n## Notes Created\n",
    ]
    summary_lines.extend(stored)

    if data["all_github_links"]:
        summary_lines.append(f"\n## GitHub Links ({len(data['all_github_links'])})\n")
        for gh in data["all_github_links"][:20]:
            summary_lines.append(f"- {gh}")
        if len(data["all_github_links"]) > 20:
            summary_lines.append(f"- ... and {len(data['all_github_links']) - 20} more (see GitHub Links Index note)")

    summary_result = "\n".join(summary_lines)
    return add_project_metadata(summary_result, active_project.name)
