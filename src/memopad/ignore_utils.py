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
    """Optimized ignore pattern matcher using sets for fast lookups.

    Categorizes patterns to avoid O(N) fnmatch calls for every path part
    where exact matching or single-part extension matching is sufficient.
    """

    def __init__(self, patterns: Set[str]):
        self.exact_names = set()
        self.extensions = set()
        self.dir_exact_names = set()
        self.root_exact_names = set()
        self.root_dir_exact_names = set()
        self.complex_patterns = []
        self.root_complex_patterns = []

        for pattern in patterns:
            # Root relative patterns
            if pattern.startswith("/"):
                root_pattern = pattern[1:] # Remove leading /

                if root_pattern.endswith("/"):
                    dir_name = root_pattern[:-1]
                    if "*" not in dir_name and "?" not in dir_name and "[" not in dir_name:
                        self.root_dir_exact_names.add(dir_name)
                    else:
                        self.root_complex_patterns.append(pattern)
                elif "*" not in root_pattern and "?" not in root_pattern and "[" not in root_pattern:
                    self.root_exact_names.add(root_pattern)
                else:
                    self.root_complex_patterns.append(pattern)
                continue

            # Directory patterns
            if pattern.endswith("/"):
                dir_name = pattern[:-1]
                if "*" not in dir_name and "?" not in dir_name and "[" not in dir_name:
                    self.dir_exact_names.add(dir_name)
                else:
                    self.complex_patterns.append(pattern)
                continue

            # Single-part extensions (e.g. *.py, *.txt)
            if pattern.startswith("*.") and "*" not in pattern[2:] and "?" not in pattern[2:] and "[" not in pattern[2:]:
                self.extensions.add(pattern[1:]) # keep the dot
                continue

            # Exact names (no wildcards)
            if "*" not in pattern and "?" not in pattern and "[" not in pattern:
                self.exact_names.add(pattern)
            else:
                self.complex_patterns.append(pattern)

    def match_path_parts(self, relative_path: Path) -> bool:
        """Check if relative path matches patterns."""
        parts = relative_path.parts
        if not parts:
            return False

        relative_str = str(relative_path)
        relative_posix = relative_path.as_posix()

        # 1. Root-relative exact matches
        if parts[0] in self.root_dir_exact_names:
            return True
        if relative_posix in self.root_exact_names:
            return True

        # 2. Part-based exact and extension matches
        for part in parts:
            if part in self.exact_names:
                return True
            if part in self.dir_exact_names:
                return True
            # For extension match against parts (to handle .hidden.md case)
            if any(part.endswith(ext) for ext in self.extensions):
                return True

        # 3. Suffix match on full path
        if relative_path.suffix in self.extensions:
            return True

        # 4. Fallback to complex glob patterns
        for root_pattern in self.root_complex_patterns:
            rp = root_pattern[1:] # strip /
            if rp.endswith("/"):
                dir_name = rp[:-1]
                if parts and parts[0] == dir_name:
                    return True
            elif fnmatch.fnmatch(relative_posix, rp):
                return True

        for pattern in self.complex_patterns:
            if pattern.endswith("/"):
                dir_name = pattern[:-1]
                if dir_name in parts:
                    return True
                continue

            # Check individual parts for complex pattern
            for part in parts:
                if fnmatch.fnmatch(part, pattern):
                    return True

            if fnmatch.fnmatch(relative_posix, pattern) or fnmatch.fnmatch(relative_str, pattern):
                return True

        return False


# Global cache for IgnoreMatcher instances
_MATCHER_CACHE = {}


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

        # Use cached matcher or create a new one
        cache_key = frozenset(ignore_patterns)
        if cache_key not in _MATCHER_CACHE:
            _MATCHER_CACHE[cache_key] = IgnoreMatcher(ignore_patterns)

        matcher = _MATCHER_CACHE[cache_key]
        return matcher.match_path_parts(relative_path)

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
