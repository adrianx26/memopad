"""Configuration constants for the assimilate tool."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AssimilateConfig:
    """Configuration for assimilate operations."""

    # Max bytes to read per file when cloning a repo (1GB)
    max_file_read_size: int = 1_000_000_000

    # Default cap on files to process from a repo (when max_files=0/unlimited)
    default_max_files: int = 2_000

    # Safety margin below Entity MAX_CONTENT_LENGTH (50M) for note content
    max_note_content: int = 49_000_000

    # Large file threshold for logging (10MB)
    large_file_threshold: int = 10_000_000

    # Git clone timeout in seconds (5 minutes)
    git_timeout: float = 300.0

    # Rate limit delay between requests in seconds
    rate_limit_delay: float = 0.5

    # Maximum crawl depth
    max_crawl_depth: int = 10

    # HTTP request timeout in seconds
    http_timeout: float = 15.0

    # HEAD request timeout for content type detection
    head_timeout: float = 5.0

    # File download timeout
    download_timeout: float = 30.0

    # HTTP client connection limits
    max_connections: int = 10
    max_keepalive_connections: int = 5

    # Character limits for note builders
    overview_chars: int = 2000
    concepts_chars: int = 3000
    default_note_chars: int = 5000


# Default configuration instance
DEFAULT_CONFIG = AssimilateConfig()

# HTTP headers for crawler
DEFAULT_HEADERS = {
    "User-Agent": "Memopad-Assimilate/1.0 (knowledge-crawler)",
    "Accept": "text/html,text/plain,application/xhtml+xml",
}

# File patterns for GitHub repository scanning
REPO_FILE_PATTERNS = [
    "**/*.md",
    "**/*.txt",
    "**/*.rst",
    "**/COPYING",
    "**/LICENSE",
    "**/NOTICE",
    "**/*.py",
    "**/*.js",
    "**/*.ts",
    "**/*.go",
    "**/*.rs",
    "**/*.java",
    "**/*.c",
    "**/*.cpp",
    "**/*.h",
    "**/*.hpp",
    "**/*.json",
    "**/*.toml",
    "**/*.yaml",
    "**/*.yml",
]

# Direct file download extensions
DIRECT_DOWNLOAD_EXTENSIONS = (
    ".pdf",
    ".docx",
    ".xlsx",
    ".csv",
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".gif",
    ".webp",
    ".txt",
    ".ini",
    ".py",
)

# Content types for direct download detection
DIRECT_DOWNLOAD_CONTENT_TYPES = [
    "application/pdf",
    "wordprocessingml",
    "spreadsheet",
    "image/",
]

# Mermaid diagram color map for content types
TYPE_COLORS = {
    "config_docs": "#4CAF50",  # green
    "agent_profile": "#9C27B0",  # purple
    "skills_rules": "#FF9800",  # orange
    "concepts": "#2196F3",  # blue
    "soul_file": "#E91E63",  # pink
    "tools_functions": "#00BCD4",  # cyan
    "algorithms": "#FF5722",  # deep orange
    "decision_structure": "#795548",  # brown
}
