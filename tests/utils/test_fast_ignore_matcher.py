"""Tests for ignore_utils."""

import pytest
from memopad.ignore_utils import FastIgnoreMatcher

def test_fast_ignore_matcher_simple_names():
    patterns = {"node_modules", ".git", "build"}
    matcher = FastIgnoreMatcher(patterns)

    # Matches at root
    assert matcher.match("node_modules", is_dir=True)
    # Matches deep
    assert matcher.match("src/node_modules", is_dir=True)

    assert matcher.match(".git", is_dir=True)
    assert matcher.match("src/build", is_dir=True)

    assert matcher.match("src", is_dir=True) is False
    assert matcher.match("file.txt", is_dir=False) is False

def test_fast_ignore_matcher_root_relative():
    patterns = {"/build"}
    matcher = FastIgnoreMatcher(patterns)

    # Matches root
    assert matcher.match("build", is_dir=True)

    # Should NOT match deep (Regression check)
    assert matcher.match("src/build", is_dir=True) is False

    assert matcher.match("src", is_dir=True) is False

def test_fast_ignore_matcher_extensions():
    patterns = {"*.pyc", "*.tmp"}
    matcher = FastIgnoreMatcher(patterns)

    assert matcher.match("file.pyc", is_dir=False)
    assert matcher.match("src/file.pyc", is_dir=False)
    assert matcher.match("file.tmp", is_dir=False)
    assert matcher.match("file.py", is_dir=False) is False

def test_fast_ignore_matcher_directory_only():
    patterns = {"temp/"}
    matcher = FastIgnoreMatcher(patterns)

    assert matcher.match("temp", is_dir=True)
    assert matcher.match("src/temp", is_dir=True)
    assert matcher.match("temp", is_dir=False) is False

def test_fast_ignore_matcher_complex():
    patterns = {"test_*/data"}
    matcher = FastIgnoreMatcher(patterns)

    assert matcher.match("test_1/data", is_dir=True)
    assert matcher.match("test_2/data/file", is_dir=False) is False # Gitignore usually doesn't match parent?
    # If "test_*/data" matches "test_1/data", then "test_1/data" is ignored.
    # So "test_1/data/file" is implicitly ignored.
    # But match() is called on "test_1/data" first.

    # But if we call match on "test_1/data/file"?
    # gitignore matches if any parent matches?
    # The scanner handles checking parents. We only need to check current path.
    pass
