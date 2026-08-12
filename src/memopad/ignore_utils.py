"""Utilities for handling .gitignore patterns and file filtering."""

import fnmatch
from pathlib import Path
from typing import Set


# Common directories and patterns to ignore by default
# These are used as fallback if .bmignore doesn't exist
DEFAULT_IGNORE_PATTERNS = {
    # Hidden files (files starting with dot)
    ".*",
    # Basic Memory internal files
    "*.db",
    "*.db-shm",
    "*.db-wal",
    "config.json",
    # Version control
    ".git",
    ".svn",
    # Python
    "__pycache__",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    ".pytest_cache",
    ".coverage",
    "*.egg-info",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
    # Virtual environments
    ".venv",
    "venv",
    "env",
    ".env",
    # Node.js
    "node_modules",
    # Build artifacts
    "build",
    "dist",
    ".cache",
    # IDE
    ".idea",
    ".vscode",
    # OS files
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    # Obsidian
    ".obsidian",
    # Temporary files
    "*.tmp",
    "*.swp",
    "*.swo",
    "*~",
}


def get_bmignore_path() -> Path:
    """Get path to .bmignore file.

    Returns:
        Path to ~/{data_dir_name}/.bmignore
    """
    from memopad.config import DATA_DIR_NAME

    return Path.home() / DATA_DIR_NAME / ".bmignore"


def create_default_bmignore() -> None:
    """Create default .bmignore file if it doesn't exist.

    This ensures users have a file they can customize for all Basic Memory operations.
    """
    bmignore_path = get_bmignore_path()

    if bmignore_path.exists():
        return

    bmignore_path.parent.mkdir(parents=True, exist_ok=True)
    bmignore_path.write_text("""# Basic Memory Ignore Patterns
# This file is used by both 'bm cloud upload', 'bm cloud bisync', and file sync
# Patterns use standard gitignore-style syntax

# Hidden files (files starting with dot)
.*

# Basic Memory internal files (includes test databases)
*.db
*.db-shm
*.db-wal
config.json

# Version control
.git
.svn

# Python
__pycache__
*.pyc
*.pyo
*.pyd
.pytest_cache
.coverage
*.egg-info
.tox
.mypy_cache
.ruff_cache

# Virtual environments
.venv
venv
env
.env

# Node.js
node_modules

# Build artifacts
build
dist
.cache

# IDE
.idea
.vscode

# OS files
.DS_Store
Thumbs.db
desktop.ini

# Obsidian
.obsidian

# Temporary files
*.tmp
*.swp
*.swo
*~
""")


def load_bmignore_patterns() -> Set[str]:
    """Load patterns from .bmignore file.

    Returns:
        Set of patterns from .bmignore, or DEFAULT_IGNORE_PATTERNS if file doesn't exist
    """
    bmignore_path = get_bmignore_path()

    # Create default file if it doesn't exist
    if not bmignore_path.exists():
        create_default_bmignore()

    patterns = set()

    try:
        with bmignore_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if line and not line.startswith("#"):
                    patterns.add(line)
    except Exception:  # pragma: no cover
        # If we can't read .bmignore, fall back to defaults
        return set(DEFAULT_IGNORE_PATTERNS)  # pragma: no cover

    # If no patterns were loaded, use defaults
    if not patterns:  # pragma: no cover
        return set(DEFAULT_IGNORE_PATTERNS)  # pragma: no cover

    return patterns


def load_gitignore_patterns(base_path: Path, use_gitignore: bool = True) -> Set[str]:
    """Load gitignore patterns from .gitignore file and .bmignore.

    Combines patterns from:
    1. ~/{data_dir_name}/.bmignore (user's global ignore patterns)
    2. {base_path}/.gitignore (project-specific patterns, if use_gitignore=True)

    Args:
        base_path: The base directory to search for .gitignore file
        use_gitignore: If False, only load patterns from .bmignore (default: True)

    Returns:
        Set of patterns to ignore
    """
    # Start with patterns from .bmignore
    patterns = load_bmignore_patterns()

    if use_gitignore:
        gitignore_file = base_path / ".gitignore"
        if gitignore_file.exists():
            try:
                with gitignore_file.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        # Skip empty lines and comments
                        if line and not line.startswith("#"):
                            patterns.add(line)
            except Exception:
                # If we can't read .gitignore, just use default patterns
                pass

    return patterns


class IgnoreMatcher:
    def __init__(self, patterns: Set[str]):
        self.exact_names = set()
        self.extensions = set()
        self.dir_exact_names = set()
        self.root_patterns = set()
        self.root_dir_exact = set()
        self.complex_patterns = set()

        for pattern in patterns:
            if pattern.startswith("/"):
                root_pattern = pattern[1:]
                if root_pattern.endswith("/"):
                    dir_name = root_pattern[:-1]
                    self.root_dir_exact.add(dir_name)
                else:
                    self.root_patterns.add(root_pattern)
            elif pattern.endswith("/"):
                dir_name = pattern[:-1]
                self.dir_exact_names.add(dir_name)
            elif not any(c in pattern for c in "*?["):
                if "/" in pattern:
                    # e.g., "docs/_build" or "src/generated"
                    self.complex_patterns.add(pattern)
                else:
                    self.exact_names.add(pattern)
            elif pattern.startswith("*.") and not any(c in pattern[2:] for c in "*?["):
                if "/" in pattern[2:]:
                    self.complex_patterns.add(pattern)
                elif "." in pattern[2:]:
                    # e.g., "*.tar.gz" - treat as complex to let fnmatch handle it safely
                    self.complex_patterns.add(pattern)
                else:
                    self.extensions.add(pattern[2:])
            else:
                self.complex_patterns.add(pattern)

    def match(self, relative_path_str: str) -> bool:
        # normalize to posix
        relative_posix = relative_path_str.replace("\\", "/")
        parts = relative_posix.split("/")
        if not parts or (len(parts) == 1 and parts[0] == ""):
            return False

        if parts[0] in self.root_dir_exact:
            return True

        # Check parts exact match
        for part in parts:
            if part in self.dir_exact_names or part in self.exact_names:
                return True

        # Check extensions
        if self.extensions:
            for part in parts:
                idx = part.rfind(".")
                if idx != -1 and part[idx + 1 :] in self.extensions:
                    return True

        if not self.root_patterns and not self.complex_patterns:
            return False

        for root_pattern in self.root_patterns:
            if fnmatch.fnmatch(relative_posix, root_pattern):
                return True

        for pattern in self.complex_patterns:
            for part in parts:
                if fnmatch.fnmatch(part, pattern):
                    return True
            if fnmatch.fnmatch(relative_posix, pattern):
                return True
            if relative_path_str != relative_posix and fnmatch.fnmatch(relative_path_str, pattern):
                return True

        return False


# Cache for IgnoreMatchers to avoid recompiling patterns
_matcher_cache = {}


def should_ignore_path(file_path: Path, base_path: Path, ignore_patterns: Set[str]) -> bool:
    """Check if a file path should be ignored based on gitignore patterns.

    Args:
        file_path: The file path to check
        base_path: The base directory for relative path calculation
        ignore_patterns: Set of patterns to match against

    Returns:
        True if the path should be ignored, False otherwise
    """
    try:
        # Fast string-based check if path is absolute and under base
        file_path_str = str(file_path)
        base_path_str = str(base_path)

        if file_path_str.startswith(base_path_str):
            rel_len = len(base_path_str)
            if len(file_path_str) > rel_len and file_path_str[rel_len] in ("/", "\\"):
                relative_str = file_path_str[rel_len + 1 :]
            elif len(file_path_str) == rel_len:
                return False  # Same directory
            else:
                relative_str = str(file_path.relative_to(base_path))
        else:
            relative_str = str(file_path.relative_to(base_path))

        if not relative_str:
            return False

        # Optimize by caching matcher per pattern set
        key = frozenset(ignore_patterns)
        matcher = _matcher_cache.get(key)
        if matcher is None:
            matcher = IgnoreMatcher(ignore_patterns)
            _matcher_cache[key] = matcher

        return matcher.match(relative_str)
    except ValueError:
        return False


def filter_files(
    files: list[Path], base_path: Path, ignore_patterns: Set[str] | None = None
) -> tuple[list[Path], int]:
    """Filter a list of files based on gitignore patterns.

    Args:
        files: List of file paths to filter
        base_path: The base directory for relative path calculation
        ignore_patterns: Set of patterns to ignore. If None, loads from .gitignore

    Returns:
        Tuple of (filtered_files, ignored_count)
    """
    if ignore_patterns is None:
        ignore_patterns = load_gitignore_patterns(base_path)

    filtered_files = []
    ignored_count = 0

    for file_path in files:
        if should_ignore_path(file_path, base_path, ignore_patterns):
            ignored_count += 1
        else:
            filtered_files.append(file_path)

    return filtered_files, ignored_count
