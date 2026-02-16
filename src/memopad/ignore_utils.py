"""Utilities for handling .gitignore patterns and file filtering."""

import fnmatch
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


class IgnoreMatcher:
    """Efficient matcher for ignore patterns using pre-compiled regex and sets."""

    def __init__(self, patterns: Set[str]):
        self._root_dirs: Set[str] = set()
        self._root_patterns: List[Pattern] = []
        self._dirs: Set[str] = set()
        self._names: Set[str] = set()
        self._extensions: Set[str] = set()
        self._patterns: List[tuple[str, Pattern]] = []

        for pattern in patterns:
            # Handle patterns starting with / (root relative)
            if pattern.startswith("/"):
                p = pattern[1:]
                if p.endswith("/"):
                    # Root-relative directory: /node_modules/
                    self._root_dirs.add(p[:-1])
                else:
                    # Root-relative file/glob: /config.json or /*.txt
                    # fnmatch.translate converts glob to regex anchored at start and end
                    regex = fnmatch.translate(p)
                    self._root_patterns.append(re.compile(regex))
            # Handle directory patterns (ending with /)
            elif pattern.endswith("/"):
                # Directory anywhere: node_modules/
                self._dirs.add(pattern[:-1])
            # Simple extension: *.pyc (no other wildcards, no path separators)
            elif (
                pattern.startswith("*.")
                and pattern.count("*") == 1
                and pattern.count("?") == 0
                and "/" not in pattern
            ):
                self._extensions.add(pattern[1:])
            # Simple exact name: .git or node_modules (no wildcards, no path separators)
            elif (
                "/" not in pattern
                and "*" not in pattern
                and "?" not in pattern
                and "[" not in pattern
            ):
                self._names.add(pattern)
            else:
                # Complex glob: src/*.py or *foo*
                regex = fnmatch.translate(pattern)
                self._patterns.append((pattern, re.compile(regex)))

    def match(self, file_path: Path, base_path: Path) -> bool:
        """Check if file path matches any ignore pattern."""
        try:
            relative_path = file_path.relative_to(base_path)
            parts = relative_path.parts
            if not parts:
                return False

            # 1. Root-relative directories
            if parts[0] in self._root_dirs:
                return True

            relative_posix = relative_path.as_posix()

            # 2. Root-relative patterns
            for regex in self._root_patterns:
                if regex.match(relative_posix):
                    return True

            # 3. Component checks (iterating parts is fast enough)
            for part in parts:
                if part in self._dirs:
                    return True
                if part in self._names:
                    return True
                for ext in self._extensions:
                    if part.endswith(ext):
                        return True

            # 4. Complex patterns
            for pattern_str, regex in self._patterns:
                if "/" in pattern_str:
                    # If pattern contains slash, it matches against full path
                    if regex.match(relative_posix):
                        return True
                else:
                    # If pattern has no slash, it matches against any component
                    # But fnmatch behavior is slightly more complex, let's verify:
                    # 'src/*.py' (with slash) -> matched above
                    # '*.py' (no slash) -> matched via extensions or below
                    # 'foo*' (no slash) -> matches 'foobar' in any component
                    for part in parts:
                        if regex.match(part):
                            return True

            return False

        except ValueError:
            # If we can't get relative path, don't ignore
            return False


def should_ignore_path(
    file_path: Path, base_path: Path, ignore_patterns: Set[str] | IgnoreMatcher
) -> bool:
    """Check if a file path should be ignored based on gitignore patterns.

    Args:
        file_path: The file path to check
        base_path: The base directory for relative path calculation
        ignore_patterns: Set of patterns or IgnoreMatcher to match against

    Returns:
        True if the path should be ignored, False otherwise
    """
    # Use optimized matcher if provided
    if isinstance(ignore_patterns, IgnoreMatcher):
        return ignore_patterns.match(file_path, base_path)

    # Fallback to legacy implementation for sets
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

    # Optimize: create matcher once for the batch
    matcher = IgnoreMatcher(ignore_patterns)

    filtered_files = []
    ignored_count = 0

    for file_path in files:
        if matcher.match(file_path, base_path):
            ignored_count += 1
        else:
            filtered_files.append(file_path)

    return filtered_files, ignored_count
