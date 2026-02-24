"""Utilities for handling .gitignore patterns and file filtering."""

import fnmatch
import functools
from pathlib import Path
from typing import Set, Tuple, List


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
    """Efficient matcher for ignore patterns using set lookups and pre-compiled logic."""

    def __init__(self, patterns: Set[str]):
        self.patterns = patterns
        self.exact_names: Set[str] = set()
        self.extension_patterns: Set[str] = set()
        self.root_relative_patterns: List[Tuple[str, bool]] = []  # (pattern, is_dir)

        # simple_globs: patterns with NO slash but WITH wildcards.
        # e.g. "temp_*", "*.log" (if not covered by extension_patterns)
        self.simple_globs: List[Tuple[str, bool]] = []  # (pattern, is_dir)

        # path_patterns: patterns WITH slash (anywhere).
        # e.g. "foo/bar", "src/*.py"
        self.path_patterns: List[Tuple[str, bool]] = []  # (pattern, is_dir)

        for pattern in patterns:
            # Check for directory marker
            is_dir = pattern.endswith("/")
            clean_pattern = pattern[:-1] if is_dir else pattern

            if pattern.startswith("/"):
                # Root relative pattern
                # Remove leading slash for matching
                pat = clean_pattern[1:]
                self.root_relative_patterns.append((pat, is_dir))
            elif "/" in clean_pattern:
                # Contains path separator (but not at start) -> path match
                self.path_patterns.append((clean_pattern, is_dir))
            else:
                # No path separator -> name match or simple glob
                if "*" in clean_pattern or "?" in clean_pattern or "[" in clean_pattern:
                    if (
                        clean_pattern.startswith("*.")
                        and clean_pattern.count("*") == 1
                        and "?" not in clean_pattern
                        and "[" not in clean_pattern
                    ):
                        # Simple extension pattern *.ext
                        self.extension_patterns.add(clean_pattern[1:])  # store .ext
                    else:
                        self.simple_globs.append((clean_pattern, is_dir))
                else:
                    # Exact name match
                    self.exact_names.add(clean_pattern)

    def match(self, path: Path, base_path: Path) -> bool:
        """Check if path matches ignore patterns.

        Args:
            path: Absolute path to check
            base_path: Base directory for relative path calculation
        """
        try:
            # Ensure path is relative to base_path first to match legacy behavior
            # and ensure we don't ignore files outside the project
            relative_path = path.relative_to(base_path)
        except ValueError:
            return False

        # We need name and relative path
        name = path.name

        # Fast check on name
        if name in self.exact_names:
            return True

        # Check extension
        if self.extension_patterns:
            for ext in self.extension_patterns:
                if name.endswith(ext):
                    return True

        # Need relative path for other checks
        rel_posix = relative_path.as_posix()

        return self._match_relative(name, rel_posix, path.is_dir())

    def match_entry(self, name: str, relative_path: str, is_dir: bool = False) -> bool:
        """Check if entry matches ignore patterns using pre-computed relative path string.

        Args:
            name: File/Directory name
            relative_path: Relative path string (posix style)
            is_dir: Whether entry is a directory
        """
        # Fast check on name
        if name in self.exact_names:
            return True

        # Check extension
        if self.extension_patterns:
            for ext in self.extension_patterns:
                if name.endswith(ext):
                    return True

        return self._match_relative(name, relative_path, is_dir)

    def _match_relative(self, name: str, rel_posix: str, is_dir: bool) -> bool:
        parts = None # Lazy split

        # Check root relative patterns
        for pat, pat_is_dir in self.root_relative_patterns:
            if pat_is_dir:
                # Pattern demands directory
                # "foo/" matches "foo" (if dir) or "foo/bar"
                # Check if first part of path matches
                if parts is None: parts = rel_posix.split("/")
                if parts and parts[0] == pat:
                    return True
            else:
                if fnmatch.fnmatch(rel_posix, pat):
                    return True

        # Check simple globs (no slash)
        for pat, pat_is_dir in self.simple_globs:
            # Optimization: check name first
            if fnmatch.fnmatch(name, pat):
                if pat_is_dir:
                    # If pattern is "foo/" and name is "foo", match only if is_dir or path is deeper
                    if is_dir: return True
                    # If path is "src/foo/bar.txt", name is "bar.txt". If pattern is "foo/", it doesn't match name.
                else:
                    return True

            # Must check all parts if it's a deep path
            # e.g. pattern "temp_*" matches "src/temp_dir/file.txt"
            if parts is None: parts = rel_posix.split("/")

            # Check other parts (parents)
            for part in parts[:-1]:
                if fnmatch.fnmatch(part, pat):
                    # For simple globs, any matching component triggers ignore
                    return True

        # Check path patterns (has slash)
        for pat, pat_is_dir in self.path_patterns:
            # Match full relative path
            if fnmatch.fnmatch(rel_posix, pat):
                if pat_is_dir:
                    if is_dir: return True
                else:
                    return True

            # Match prefix (e.g. "src/temp_data" ignoring "src/temp_data/file.txt")
            if rel_posix.startswith(pat + "/"):
                return True

        # If name is in exact_names, we already returned True at start of match_entry/match.
        # But if we have "src/foo/bar.txt" and "foo" is in exact_names...
        # We need to check if any PART is in exact_names
        if self.exact_names:
            if parts is None: parts = rel_posix.split("/")
            # We already checked name (last part)
            if len(parts) > 1:
                # Check parent parts
                for part in parts[:-1]:
                    if part in self.exact_names:
                        return True

        return False

@functools.lru_cache(maxsize=128)
def _get_matcher(patterns_tuple: Tuple[str]) -> IgnoreMatcher:
    return IgnoreMatcher(set(patterns_tuple))

def should_ignore_path(file_path: Path, base_path: Path, ignore_patterns: Set[str]) -> bool:
    """Check if a file path should be ignored based on gitignore patterns.

    Args:
        file_path: The file path to check
        base_path: The base directory for relative path calculation
        ignore_patterns: Set of patterns to match against

    Returns:
        True if the path should be ignored, False otherwise
    """
    # Use cached matcher factory to avoid re-parsing patterns
    # Convert set to sorted tuple for deterministic caching
    matcher = _get_matcher(tuple(sorted(ignore_patterns)))
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

    matcher = _get_matcher(tuple(sorted(ignore_patterns)))

    filtered_files = []
    ignored_count = 0

    for file_path in files:
        if matcher.match(file_path, base_path):
            ignored_count += 1
        else:
            filtered_files.append(file_path)

    return filtered_files, ignored_count
