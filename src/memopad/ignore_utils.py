"""Utilities for handling .gitignore patterns and file filtering."""

import fnmatch
import os
import re
from pathlib import Path
from typing import Set, List, Pattern


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


class IgnoreMatcher:
    """Optimized matcher for gitignore patterns."""

    def __init__(self, patterns: Set[str]):
        """Initialize matcher with patterns.

        Args:
            patterns: Set of gitignore-style patterns
        """
        self._root_dirs: Set[str] = set()
        self._root_patterns: List[Pattern] = []
        self._any_dirs: Set[str] = set()
        self._any_names: Set[str] = set()
        self._full_path_patterns: List[Pattern] = []
        self._part_patterns_combined: Pattern | None = None

        part_patterns = []
        flags = re.IGNORECASE if os.name == 'nt' else 0

        for pattern in patterns:
            # Handle patterns starting with / (root relative)
            if pattern.startswith("/"):
                inner = pattern[1:]
                # For directory patterns ending with /
                if inner.endswith("/"):
                    self._root_dirs.add(inner[:-1])
                else:
                    # Regular root-relative pattern
                    try:
                        regex = fnmatch.translate(inner)
                        self._root_patterns.append(re.compile(regex, flags))
                    except Exception:
                        pass
                continue

            # Handle directory patterns (ending with /)
            if pattern.endswith("/"):
                self._any_dirs.add(pattern[:-1])
                continue

            # Check if it's a direct name match or needs globs
            # If it contains slash, it's a path match (fnmatch rule)
            if '/' in pattern:
                try:
                    regex = fnmatch.translate(pattern)
                    self._full_path_patterns.append(re.compile(regex, flags))
                except Exception:
                    pass
                continue

            # Simple name or glob
            if not any(c in pattern for c in '*?[]'):
                # Exact name anywhere
                self._any_names.add(pattern)
            else:
                # Glob pattern matching any part
                try:
                    # fnmatch.translate returns anchored regex like (?s:.*\.pyc)\Z
                    # We want to use it to match individual parts
                    regex = fnmatch.translate(pattern)
                    part_patterns.append(regex)
                except Exception:
                    pass

        if part_patterns:
            # Combine all part patterns into one regex for performance
            # fnmatch.translate results usually look like '(?s:pattern)\Z'
            # We strip the (?s: prefix and )\Z suffix to combine them
            # This is a bit hacky but works for standard fnmatch output
            # Alternatively, we just join them with |
            try:
                self._part_patterns_combined = re.compile('|'.join(part_patterns), flags)
            except Exception:
                # If combination fails (e.g. too many groups), fall back?
                # For now just ignore faulty patterns
                pass

    def match(self, relative_path: str) -> bool:
        """Check if relative path matches any ignore pattern.

        Args:
            relative_path: Path relative to base directory (forward slashes preferred)

        Returns:
            True if ignored
        """
        # Normalize slashes for Windows
        if os.sep == '\\':
            relative_path = relative_path.replace('\\', '/')

        parts = relative_path.split('/')

        # 1. Root directory check
        if parts and parts[0] in self._root_dirs:
            return True

        # 2. Root patterns (regex on full path)
        for regex in self._root_patterns:
            if regex.match(relative_path):
                return True

        # 3. Any dirs/names (Set intersection - fast)
        # Using set(parts) is O(depth), usually small
        parts_set = set(parts)
        if not self._any_dirs.isdisjoint(parts_set):
            return True
        if not self._any_names.isdisjoint(parts_set):
            return True

        # 4. Part patterns (Combined regex on parts)
        if self._part_patterns_combined:
            for part in parts:
                if self._part_patterns_combined.match(part):
                    return True

        # 5. Full path patterns (globs with slashes)
        for regex in self._full_path_patterns:
            if regex.match(relative_path):
                return True

        return False


def get_bmignore_path() -> Path:
    """Get path to .bmignore file.

    Returns:
        Path to ~/.memopad/.bmignore
    """
    return Path.home() / ".memopad" / ".bmignore"


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
    1. ~/.memopad/.bmignore (user's global ignore patterns)
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
        relative_path = file_path.relative_to(base_path)
        # Use simple string conversion for IgnoreMatcher
        matcher = IgnoreMatcher(ignore_patterns)
        return matcher.match(str(relative_path))
    except ValueError:
        # If we can't get relative path, don't ignore
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
