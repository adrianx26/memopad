"""Type definitions for the assimilate tool."""

from typing import TypedDict, NotRequired


class PageLinks(TypedDict):
    """Links extracted from a page."""

    internal: list[str]
    github: list[str]
    external: list[str]


class PageData(TypedDict):
    """Data structure for a crawled page or file."""

    url: str
    text: str
    content_types: list[str]
    links: PageLinks
    is_file: bool


class CrawlResult(TypedDict):
    """Result structure from crawling or cloning operations."""

    pages: list[PageData]
    all_github_links: list[str]
    all_external_links: list[str]
    errors: list[str]


class NoteBuilderConfig(TypedDict):
    """Configuration for a note builder."""

    content_type: str
    title: str
    description: str
    max_chars: NotRequired[int]
