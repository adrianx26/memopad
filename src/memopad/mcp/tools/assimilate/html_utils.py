"""HTML parsing utilities for the assimilate tool."""

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse


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
