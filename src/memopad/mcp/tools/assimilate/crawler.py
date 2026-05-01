"""Web crawler with connection pooling and rate limiting."""

import asyncio
from collections import deque
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
) -> tuple[str, str, str] | tuple[None, None, str]:
    """Fetch a single page.

    Returns (body, final_url, content_type) on success, or (None, None, reason)
    on failure. The reason string is suitable for surfacing to the user.
    """
    try:
        resp = await http_client.get(
            url,
            follow_redirects=True,
            timeout=httpx.Timeout(DEFAULT_CONFIG.http_timeout),
        )
        if resp.status_code != 200:
            reason = f"HTTP {resp.status_code}"
            logger.debug(f"assimilate: {reason} for {url}")
            return None, None, reason
        content_type = resp.headers.get("content-type", "").lower()
        if "text/html" not in content_type and "text/plain" not in content_type:
            reason = f"non-text content-type: {content_type or 'unknown'}"
            logger.debug(f"assimilate: skipping {reason} at {url}")
            return None, None, reason
        return resp.text, str(resp.url), content_type
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"
        logger.debug(f"assimilate: failed to fetch {url}: {reason}")
        return None, None, reason


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

    # deque gives O(1) popleft; the previous list.pop(0) was O(n) per step.
    queue: deque[tuple[str, int]] = deque([(start_url, 0)])

    async with get_http_client() as http_client:
        while queue and (max_pages == 0 or len(pages) < max_pages):
            url, depth = queue.popleft()

            # Normalize trailing slash
            normalized = url.rstrip("/")
            if normalized in visited or url in visited:
                continue
            visited.add(url)
            visited.add(normalized)

            body, final_url, info = await fetch_page(http_client, url)
            if body is None or final_url is None:
                logger.warning(f"assimilate: failed to fetch {url}: {info}")
                errors.append(f"{url}: {info}")
                continue

            content_type = info
            logger.debug(f"assimilate: fetched {url} (final_url={final_url})")

            # If this is the start URL, update base_domain based on where we landed
            # to handle redirects (e.g. http -> https, non-www -> www)
            if depth == 0:
                parsed_final = urlparse(final_url)
                new_base = parsed_final.netloc.lower()
                if new_base.startswith("www."):
                    new_base = new_base[4:]
                base_domain = new_base

            # Trigger: text/plain pages skip HTML-to-text conversion and link extraction
            # Why: html_to_text on plain text would noisily strip incidental angle brackets,
            #      and links can't be reliably extracted from plain text.
            if "text/html" in content_type:
                text = html_to_text(body)
                links = extract_links(body, final_url)
                categorized = categorize_links(links, base_domain)
            else:
                text = body
                categorized = {"internal": [], "github": [], "external": []}

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

            if depth < max_depth:
                # Queue internal links and GitHub links (READMEs often hold useful info).
                # Same de-dup check as the loop head — cheap, prevents queue blowup.
                for link in (*categorized["internal"], *categorized["github"]):
                    if link not in visited and link.rstrip("/") not in visited:
                        queue.append((link, depth + 1))

            # Rate limit
            await asyncio.sleep(DEFAULT_CONFIG.rate_limit_delay)

    return {
        "pages": pages,
        "all_github_links": sorted(all_github),
        "all_external_links": sorted(all_external),
        "errors": errors,
    }
