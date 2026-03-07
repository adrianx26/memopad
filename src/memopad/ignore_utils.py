"""Utilities for handling .gitignore patterns and file filtering."""

import fnmatch
import os
import re
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


class IgnoreMatcher:
    """Optimized matcher for gitignore/bmignore patterns.

    Pre-compiles patterns and uses pure string matching instead of Path objects
    for significant performance improvements during bulk directory scans.
    """

    def __init__(self, patterns: Set[str]):
        self.exact_names: Set[str] = set()
        self.exact_dirs: Set[str] = set()
        self.glob_patterns: list[re.Pattern] = []
        self.root_patterns: list[tuple[str, bool]] = []
        self.complex_patterns: list[str] = []

        for p in patterns:
            # Root patterns
            if p.startswith("/"):
                root_p = p[1:]
                if root_p.endswith("/"):
                    self.root_patterns.append((root_p[:-1], True))
                else:
                    self.root_patterns.append((root_p, False))
                continue

            # Directory patterns
            if p.endswith("/"):
                p_dir = p[:-1]
                if "*" not in p_dir and "?" not in p_dir and "[" not in p_dir and "/" not in p_dir:
                    self.exact_dirs.add(p_dir)
                else:
                    self.complex_patterns.append(p)
                continue

            # Regular patterns
            if "/" in p:
                self.complex_patterns.append(p)
            elif "*" in p or "?" in p or "[" in p:
                # Compile regex for glob patterns for faster matching
                regex_str = fnmatch.translate(p)
                self.glob_patterns.append(re.compile(regex_str))
            else:
                self.exact_names.add(p)

    def match(self, path: Path | str, base: Path | str) -> bool:
        """Check if a path matches the ignore patterns.

        Uses pure string operations for speed.

        Args:
            path: Path to check
            base: Base directory for relative path calculation

        Returns:
            True if path should be ignored
        """
        try:
            # Fast path string processing
            path_str = str(path)
            base_str = str(base)

            # Ensure base_str ends with sep to avoid false prefix matches
            # e.g. base="/app/dir" matching path="/app/dir2/file.txt"
            if not base_str.endswith(os.sep):
                base_str += os.sep

            # Simple string prefix check
            if not path_str.startswith(base_str) and path_str != str(base):
                # Fallback to relative_to if it's not a simple string prefix
                # (e.g. symlinks or weird paths)
                if isinstance(path, Path) and isinstance(base, Path):
                    rel = path.relative_to(base)
                    rel_posix = rel.as_posix()
                    parts = rel.parts
                else:
                    return False
            else:
                if path_str == str(base):
                    return False
                rel_path = path_str[len(base_str):]

                # Convert backslashes to forward slashes for matching
                if os.sep == "\\":
                    rel_posix = rel_path.replace("\\", "/") # pragma: no cover
                else:
                    rel_posix = rel_path

                parts = tuple(rel_posix.split("/"))

            # Empty path (matches base dir)
            if not parts or (len(parts) == 1 and parts[0] == ""):
                return False

            # Check root patterns
            if self.root_patterns:
                for rp, is_dir in self.root_patterns:
                    if is_dir:
                        if parts[0] == rp:
                            return True
                    else:
                        if fnmatch.fnmatch(rel_posix, rp):
                            return True

            # Check exact names/dirs on all parts
            if self.exact_names or self.exact_dirs:
                for part in parts:
                    if part in self.exact_names or part in self.exact_dirs:
                        return True

            # Check glob patterns
            if self.glob_patterns:
                for part in parts:
                    for regex in self.glob_patterns:
                        if regex.match(part):
                            return True

            # Check complex patterns (fallback to fnmatch on whole path)
            if self.complex_patterns:
                for p in self.complex_patterns:
                    if p.endswith("/"):
                        dir_name = p[:-1]
                        if dir_name in parts:
                            return True
                        # handle wildcards e.g. temp_*/
                        elif fnmatch.fnmatch(rel_posix + "/", p) or fnmatch.fnmatch(rel_posix, p):
                            return True
                    if fnmatch.fnmatch(rel_posix, p):
                        return True

            return False
        except ValueError:
            return False


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

    This function instantiates a new IgnoreMatcher on each call.
    For performance in loops, instantiate IgnoreMatcher directly and call match().

    Args:
        file_path: The file path to check
        base_path: The base directory for relative path calculation
        ignore_patterns: Set of patterns to match against

    Returns:
        True if the path should be ignored, False otherwise
    """
    matcher = IgnoreMatcher(ignore_patterns)
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

    matcher = IgnoreMatcher(ignore_patterns)
    filtered_files = []
    ignored_count = 0

    for file_path in files:
        if matcher.match(file_path, base_path):
            ignored_count += 1
        else:
            filtered_files.append(file_path)

    return filtered_files, ignored_count
