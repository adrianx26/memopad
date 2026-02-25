"""Utilities for handling .gitignore patterns and file filtering."""

import fnmatch
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import FrozenSet, List, Set, Tuple


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


@dataclass
class Pattern:
    pattern: str
    is_dir: bool = False
    is_root: bool = False


class IgnoreMatcher:
    """Optimized matcher for gitignore patterns."""

    def __init__(self, patterns: FrozenSet[str]):
        self.exact_names: Set[str] = set()
        self.extensions: Set[str] = set()
        self.simple_globs: List[str] = []
        self.complex_patterns: List[Pattern] = []

        for p in patterns:
            if p.startswith("!"):
                continue  # Skip negation for now

            is_root = p.startswith("/")
            if is_root:
                p = p[1:]

            is_dir = p.endswith("/")
            if is_dir:
                p = p[:-1]

            if not p:
                continue

            # Classify
            has_slash = "/" in p

            # 1. Extension: *.ext (single extension only)
            # We skip multi-part extensions like *.tar.gz here and let them be handled
            # by simple_globs, because path.suffix only returns the last part (.gz).
            if (
                not has_slash
                and p.startswith("*.")
                and p.count("*") == 1
                and p.count(".") == 1
                and "?" not in p
                and "[" not in p
            ):
                self.extensions.add(p[1:])  # .ext
                continue

            # 2. Exact name (no wildcards)
            if not has_slash and all(c not in "*?[" for c in p):
                if is_root:
                    self.complex_patterns.append(Pattern(p, is_dir, True))
                else:
                    self.exact_names.add(p)
                continue

            # 3. Simple glob (no slash, has wildcards)
            if not has_slash:
                if is_root:
                    self.complex_patterns.append(Pattern(p, is_dir, True))
                else:
                    self.simple_globs.append(p)
                continue

            # 4. Complex pattern (has slash)
            # implicitly root-relative unless starts with **
            self.complex_patterns.append(Pattern(p, is_dir, is_root=True))

    def match(self, path: Path, base_path: Path) -> bool:
        """Check if path matches any ignore pattern."""
        # 1. Extensions (fastest check, independent of path location)
        if path.suffix in self.extensions:
            return True

        # Get relative path - needed for all other checks
        try:
            rel = path.relative_to(base_path)
        except ValueError:
            # If path is not relative to base_path, we can't ignore it based on these rules
            return False

        # 2. Exact names (anywhere in relative path)
        for part in rel.parts:
            if part in self.exact_names:
                return True

        # 3. Simple globs (anywhere in relative path)
        if self.simple_globs:
            for part in rel.parts:
                for g in self.simple_globs:
                    if fnmatch.fnmatch(part, g):
                        return True

        # 4. Complex patterns (relative path string)
        rel_str = rel.as_posix()

        for p in self.complex_patterns:
            if fnmatch.fnmatch(rel_str, p.pattern):
                return True

            # Check if it matches a parent directory
            # If the pattern matches a directory component, everything under it is ignored
            if fnmatch.fnmatch(rel_str, f"{p.pattern}/*"):
                return True

        return False


@lru_cache(maxsize=64)
def get_matcher(patterns: FrozenSet[str]) -> IgnoreMatcher:
    """Get cached IgnoreMatcher for a set of patterns."""
    return IgnoreMatcher(patterns)


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
    matcher = get_matcher(frozenset(ignore_patterns))
    return matcher.match(file_path, base_path)


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

    # Get matcher once (optimization: though get_matcher is cached, avoiding frozenset conversion is good if possible)
    # But here we pass Set, so we rely on should_ignore_path doing the caching.

    for file_path in files:
        if should_ignore_path(file_path, base_path, ignore_patterns):
            ignored_count += 1
        else:
            filtered_files.append(file_path)

    return filtered_files, ignored_count
