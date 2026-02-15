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
    # Get the relative path from base
    try:
        relative_path = file_path.relative_to(base_path)
        relative_str = str(relative_path)
        relative_posix = relative_path.as_posix()  # Use forward slashes for matching

        # Check each pattern
        for pattern in ignore_patterns:
            # Handle patterns starting with / (root relative)
            if pattern.startswith("/"):
                root_pattern = pattern[1:]  # Remove leading /

                # For directory patterns ending with /
                if root_pattern.endswith("/"):
                    dir_name = root_pattern[:-1]  # Remove trailing /
                    # Check if the first part of the path matches the directory name
                    if len(relative_path.parts) > 0 and relative_path.parts[0] == dir_name:
                        return True
                else:
                    # Regular root-relative pattern
                    if fnmatch.fnmatch(relative_posix, root_pattern):
                        return True
                continue

            # Handle directory patterns (ending with /)
            if pattern.endswith("/"):
                dir_name = pattern[:-1]  # Remove trailing /
                # Check if any path part matches the directory name
                if dir_name in relative_path.parts:
                    return True
                continue

            # Direct name match (e.g., ".git", "node_modules")
            if pattern in relative_path.parts:
                return True

            # Check if any individual path part matches the glob pattern
            # This handles cases like ".*" matching ".hidden.md" in "concept/.hidden.md"
            for part in relative_path.parts:
                if fnmatch.fnmatch(part, pattern):
                    return True

            # Glob pattern match on full path
            if fnmatch.fnmatch(relative_posix, pattern) or fnmatch.fnmatch(relative_str, pattern):
                return True  # pragma: no cover

        return False
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


class FastIgnoreMatcher:
    """Optimized matcher for ignore patterns.

    Pre-processes patterns into categories (simple names, extensions, etc.) for O(1) or O(N)
    string lookup instead of expensive Path operations and repeated regex compilation.
    """

    def __init__(self, patterns: Set[str]):
        self._simple_names = set()
        self._extensions = []  # list of suffixes
        self._complex_patterns = []  # list of (pattern, is_root_relative, is_dir_only)

        for p in patterns:
            # Analyze pattern
            if p.startswith("/"):
                is_root = True
                p_clean = p[1:]
            else:
                is_root = False
                p_clean = p

            if p_clean.endswith("/"):
                is_dir_only = True
                p_clean = p_clean[:-1]
            else:
                is_dir_only = False

            # Categorize
            if (
                not is_root
                and not is_dir_only
                and "*" not in p_clean
                and "?" not in p_clean
                and "[" not in p_clean
                and "/" not in p_clean
            ):
                self._simple_names.add(p_clean)
            elif (
                not is_root
                and not is_dir_only
                and p_clean.startswith("*.")
                and p_clean.count("*") == 1
                and "?" not in p_clean
                and "[" not in p_clean
                and "/" not in p_clean
            ):
                self._extensions.append(p_clean[1:])  # extension
            else:
                self._complex_patterns.append((p, is_root, is_dir_only))

        self._extensions = tuple(self._extensions)

    def match(self, path: str, is_dir: bool = False) -> bool:
        """Check if a file/directory path matches ignore patterns.

        Args:
            path: Relative path from project root (forward slashes)
            is_dir: Whether the entry is a directory

        Returns:
            True if ignored
        """
        # Extract filename
        if "/" in path:
            name = path.rsplit("/", 1)[1]
        else:
            name = path

        # 1. Simple names (O(1))
        # Matches if the name appears as a component anywhere (unless constrained by pattern logic,
        # but simple names in gitignore match anywhere)
        if name in self._simple_names:
            return True

        # 2. Extensions (O(num_extensions), usually small)
        if self._extensions and name.endswith(self._extensions):
            return True

        # 3. Complex patterns (O(num_complex))
        if self._complex_patterns:
            for pattern, is_root, is_dir_only in self._complex_patterns:
                if is_dir_only and not is_dir:
                    continue

                if is_root:
                    # Root pattern matches against full relative path
                    # e.g. /build matches "build", but not "src/build"
                    p_clean = pattern[1:]
                    if p_clean.endswith("/"):
                        p_clean = p_clean[:-1]

                    if fnmatch.fnmatch(path, p_clean):
                        return True
                else:
                    # Regular pattern
                    if is_dir_only:
                        p_clean = pattern[:-1]
                    else:
                        p_clean = pattern

                    # Matches name (e.g. "node_modules")
                    if fnmatch.fnmatch(name, p_clean):
                        return True

                    # Matches path (e.g. "src/temp")
                    if fnmatch.fnmatch(path, p_clean):
                        return True

        return False
