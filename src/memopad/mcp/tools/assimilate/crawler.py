"""Web crawler with connection pooling and rate limiting."""

import asyncio
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import httpx
from loguru import logger

from .config import DEFAULT_CONFIG, DEFAULT_HEADERS
from .content_detector import detect_content_type
from .html_utils import extract_links, html_to_text, categorize_links
from .types import CrawlResult


@asynccontextmanager
async def get_http_client():
    """Get an HTTP client with connection pooling."""
    limits = httpx.Limits(
        max_connections=DEFAULT_CONFIG.max_connections,
        max_keepalive_connections=DEFAULT_CONFIG.max_keepalive_connections,
    )
    async with httpx.AsyncClient(
        headers=DEFAULT_HEADERS,
        limits=limits,
        timeout=httpx.Timeout(DEFAULT_CONFIG.http_timeout),
    ) as client:
        yield client


async def fetch_page(
    http_client: httpx.AsyncClient, url: str
) -> tuple[str, str] | None:
    """Fetch a single page. Returns (html, final_url) or None on failure."""
    try:
        resp = await http_client.get(
            url,
            follow_redirects=True,
            timeout=httpx.Timeout(DEFAULT_CONFIG.http_timeout),
        )
        if resp.status_code != 200:
            logger.debug(f"assimilate: HTTP {resp.status_code} for {url}")
            return None
        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type and "text/plain" not in content_type:
            logger.debug(f"assimilate: skipping non-text content at {url}")
            return None
        return resp.text, str(resp.url)
    except Exception as e:
        logger.debug(f"assimilate: failed to fetch {url}: {e}")
        return None


async def crawl(
    start_url: str,
    max_depth: int = DEFAULT_CONFIG.max_crawl_depth,
    max_pages: int = 0,
) -> CrawlResult:
    """Crawl starting from a URL, returning structured results."""
    parsed_start = urlparse(start_url)
    base_domain = parsed_start.netloc.lower()

    visited: set[str] = set()
    pages: list[dict] = []
    all_github: set[str] = set()
    all_external: set[str] = set()
    errors: list[str] = []

    queue: list[tuple[str, int]] = [(start_url, 0)]

    async with get_http_client() as http_client:
        while queue and (max_pages == 0 or len(pages) < max_pages):
            url, depth = queue.pop(0)

            # Normalize trailing slash
            normalized = url.rstrip("/")
            if normalized in visited or url in visited:
                continue
            visited.add(url)
            visited.add(normalized)

            result = await fetch_page(http_client, url)
            if result is None:
                logger.warning(f"assimilate: failed to fetch {url}")
                errors.append(url)
                continue

            html, final_url = result
            logger.debug(f"assimilate: fetched {url} (final_url={final_url})")

            # If this is the start URL, update base_domain based on where we landed
            # to handle redirects (e.g. http -> https, non-www -> www)
            if depth == 0:
                parsed_final = urlparse(final_url)
                new_base = parsed_final.netloc.lower()
                if new_base.startswith("www."):
                    new_base = new_base[4:]
                base_domain = new_base

            text = html_to_text(html)
            links = extract_links(html, final_url)
            categorized = categorize_links(links, base_domain)

            content_types = detect_content_type(url, text)

            pages.append({
                "url": final_url,
                "text": text,
                "content_types": content_types,
                "links": categorized,
                "is_file": False,
            })

            all_github.update(categorized["github"])
            all_external.update(categorized["external"])

            # Queue internal links for deeper crawling
            if depth < max_depth:
                for internal_link in categorized["internal"]:
                    if (
                        internal_link not in visited
                        and internal_link.rstrip("/") not in visited
                    ):
                        queue.append((internal_link, depth + 1))

            # Also queue GitHub links (they often have READMEs with useful info)
            if depth < max_depth:
                for gh_link in categorized["github"]:
                    if (
                        gh_link not in visited
                        and gh_link.rstrip("/") not in visited
                    ):
                        queue.append((gh_link, depth + 1))

            # Rate limit
            await asyncio.sleep(DEFAULT_CONFIG.rate_limit_delay)

    return {
        "pages": pages,
        "all_github_links": sorted(all_github),
        "all_external_links": sorted(all_external),
        "errors": errors,
    }
