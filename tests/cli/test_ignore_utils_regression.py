"""Tests for IgnoreMatcher regression scenarios."""

from pathlib import Path
from memopad.ignore_utils import should_ignore_path

def test_wildcard_directory_match():
    """Regression test for wildcard directory matching (e.g. 'temp_*/')."""
    base_path = Path("/project")
    patterns = {"temp_*/"}

    # Matches directory named temp_data
    file_path = base_path / "temp_data" / "file.txt"
    assert should_ignore_path(file_path, base_path, patterns)

    # Matches nested directory named temp_123
    file_path_nested = base_path / "src" / "temp_123" / "log.txt"
    assert should_ignore_path(file_path_nested, base_path, patterns)

    # Does NOT match file named temp_test (because pattern has trailing slash implies directory)
    # The new IgnoreMatcher is strict about is_dir check (via filesystem or caller).
    # Since /project/temp_test doesn't exist, it's treated as file and not ignored.
    file_path_file = base_path / "temp_test"
    assert not should_ignore_path(file_path_file, base_path, patterns)

def test_simple_glob_deep_match():
    """Regression test for simple glob matching deep paths (e.g. 'temp_*')."""
    base_path = Path("/project")
    patterns = {"temp_*"}

    # Matches nested file
    file_path = base_path / "src" / "temp_file.txt"
    assert should_ignore_path(file_path, base_path, patterns)

    # Matches directory component
    file_path_dir = base_path / "src" / "temp_dir" / "file.txt"
    assert should_ignore_path(file_path_dir, base_path, patterns)

def test_mixed_wildcards():
    """Test mixed wildcard patterns."""
    base_path = Path("/project")
    patterns = {"data_????????.json"}

    file_path = base_path / "data_20230101.json"
    assert should_ignore_path(file_path, base_path, patterns)

def test_path_pattern_match():
    """Regression test for path pattern matching (e.g. 'foo/bar')."""
    base_path = Path("/project")
    patterns = {"foo/bar"}

    # Matches foo/bar
    file_path = base_path / "foo" / "bar"
    assert should_ignore_path(file_path, base_path, patterns)

    # Matches foo/bar/baz
    file_path_nested = base_path / "foo" / "bar" / "baz"
    assert should_ignore_path(file_path_nested, base_path, patterns)

    # Should NOT match src/foo/bar (if pattern is rooted)
    file_path_deep = base_path / "src" / "foo" / "bar"
    assert not should_ignore_path(file_path_deep, base_path, patterns)
