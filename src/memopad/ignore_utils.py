"""Utilities for handling .gitignore patterns and file filtering."""

import fnmatch
from pathlib import Path
from typing import Set, List


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
    """Optimized file ignore matcher.

    Categorizes ignore patterns to allow O(1) matching for common cases
    (extensions, exact names, root dirs) and falls back to fnmatch only
    when necessary.
    """

    def __init__(self, patterns: Set[str]):
        """Initialize matcher with patterns.

        Args:
            patterns: Set of gitignore-style patterns
        """
        # Categorized patterns for optimization
        self.exact_names: Set[str] = set()
        self.extensions: Set[str] = set()
        self.root_dirs: Set[str] = set()
        self.root_files: Set[str] = set()
        self.complex_patterns: List[str] = []

        for pattern in patterns:
            if not pattern:
                continue

            # Root-relative patterns (starting with /)
            if pattern.startswith("/"):
                root_pattern = pattern[1:]
                if root_pattern.endswith("/"):
                    # Directory at root (e.g., /dist/)
                    self.root_dirs.add(root_pattern[:-1])
                elif "*" not in root_pattern and "?" not in root_pattern and "[" not in root_pattern:
                    # Specific file at root (e.g., /config.json)
                    self.root_files.add(root_pattern)
                else:
                    # Glob at root
                    self.complex_patterns.append(pattern)
                continue

            # Directory patterns (ending with /)
            if pattern.endswith("/"):
                dir_name = pattern[:-1]
                if "*" not in dir_name and "?" not in dir_name and "[" not in dir_name:
                    # Exact directory name anywhere (e.g., node_modules/)
                    self.exact_names.add(dir_name)
                else:
                    # Directory glob (e.g., temp_*/)
                    self.complex_patterns.append(pattern)
                continue

            # Simple extensions (*.py, *.db)
            if pattern.startswith("*.") and pattern.count("*") == 1 and "?" not in pattern and "[" not in pattern:
                self.extensions.add(pattern[2:])
                continue

            # Exact filenames everywhere
            if "*" not in pattern and "?" not in pattern and "[" not in pattern:
                self.exact_names.add(pattern)
                continue

            # Everything else is a complex glob
            self.complex_patterns.append(pattern)

    def match(self, file_path: Path, base_path: Path) -> bool:
        """Check if path matches any ignore pattern.

        Args:
            file_path: The file path to check
            base_path: The base directory for relative path calculation

        Returns:
            True if the path should be ignored, False otherwise
        """
        try:
            relative_path = file_path.relative_to(base_path)
        except ValueError:
            return False

        parts = relative_path.parts
        if not parts:
            return False

        # 1. Fast path: check exact names (directories or filenames) anywhere in path
        # This handles node_modules, .git, .venv, etc. O(N) lookup where N is path depth
        for part in parts:
            if part in self.exact_names:
                return True

        # 2. Fast path: check extensions on filename
        # This handles *.db, *.pyc, etc. O(1) lookup
        if self.extensions:
            name = parts[-1]
            if "." in name:
                ext = name.split(".")[-1]
                if ext in self.extensions:
                    return True

        # 3. Fast path: check root directories/files
        # This handles /dist, /build at root level
        if self.root_dirs:
            if parts[0] in self.root_dirs:
                return True

        if self.root_files:
            if len(parts) == 1 and parts[0] in self.root_files:
                return True

        # 4. Slow path: fnmatch for complex patterns
        # Only done if fast checks fail
        if self.complex_patterns:
            relative_posix = relative_path.as_posix()

            for pattern in self.complex_patterns:
                # Root relative glob
                if pattern.startswith("/"):
                    root_pattern = pattern[1:]
                    if fnmatch.fnmatch(relative_posix, root_pattern):
                        return True
                    continue

                # Directory glob (e.g. "build_*/")
                if pattern.endswith("/"):
                    dir_glob = pattern[:-1]
                    # Check if any path part matches the directory glob
                    for part in parts:
                        if fnmatch.fnmatch(part, dir_glob):
                            return True
                    continue

                # Standard glob - check parts and full path
                if fnmatch.fnmatch(relative_posix, pattern):
                    return True

                # Check parts for glob match (e.g. "test_*.py" matching "tests/test_api.py")
                for part in parts:
                    if fnmatch.fnmatch(part, pattern):
                        return True

        return False

    def match_entry(self, entry_name: str) -> bool:
        """Optimized check for simple directory entry name.

        Use this when scanning a directory and you only have the filename/dirname,
        not the full path. This can quickly filter out common ignores like .git
        without constructing full Path objects.

        Args:
            entry_name: The name of the file or directory

        Returns:
            True if it definitely matches an exact name or extension pattern
        """
        if entry_name in self.exact_names:
            return True

        if self.extensions and "." in entry_name:
            ext = entry_name.split(".")[-1]
            if ext in self.extensions:
                return True

        return False


def should_ignore_path(file_path: Path, base_path: Path, ignore_patterns: Set[str]) -> bool:
    """Check if a file path should be ignored based on gitignore patterns.

    Args:
        file_path: The file path to check
        base_path: The base directory for relative path calculation
        ignore_patterns: Set of patterns to match against

    Returns:
        True if the path should be ignored, False otherwise

    Note:
        This function creates a new IgnoreMatcher every time. For performance-critical
        loops, instantiate IgnoreMatcher once and reuse it.
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
