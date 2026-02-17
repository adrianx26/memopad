
import pytest
from pathlib import Path
from memopad.ignore_utils import IgnoreMatcher, should_ignore_path

class TestIgnoreMatcher:

    @pytest.fixture
    def base_path(self):
        return Path("/app")

    def test_ignore_matcher_names(self, base_path):
        patterns = {"node_modules", ".git", "build"}
        matcher = IgnoreMatcher(patterns)

        # Test exact matches
        assert matcher.match(base_path / "node_modules", base_path)
        assert matcher.match(base_path / ".git", base_path)
        assert matcher.match(base_path / "build", base_path)

        # Test deep matches
        assert matcher.match(base_path / "src/node_modules", base_path)
        assert matcher.match(base_path / "project/.git/HEAD", base_path)

        # Test non-matches
        assert not matcher.match(base_path / "src/main.py", base_path)
        assert not matcher.match(base_path / "node_modules_suffix", base_path)

        # Verify optimization categories
        assert "node_modules" in matcher.ignore_names
        assert not matcher.ignore_extensions
        assert not matcher.complex_patterns

    def test_ignore_matcher_extensions(self, base_path):
        patterns = {"*.pyc", "*.tmp"}
        matcher = IgnoreMatcher(patterns)

        # Test extension matches
        assert matcher.match(base_path / "test.pyc", base_path)
        assert matcher.match(base_path / "temp/file.tmp", base_path)

        # Test directory ending with extension (should match per gitignore rules if name matches)
        assert matcher.match(base_path / "folder.tmp/file.txt", base_path)

        # Test non-matches
        assert not matcher.match(base_path / "test.py", base_path)
        assert not matcher.match(base_path / "tmp/file", base_path)

        # Verify optimization categories
        assert not matcher.ignore_names
        assert ".pyc" in matcher.ignore_extensions
        assert ".tmp" in matcher.ignore_extensions
        assert not matcher.complex_patterns

    def test_ignore_matcher_complex(self, base_path):
        patterns = {"src/ignore_me", "docs/*.md", "/root.txt", ".*"}
        matcher = IgnoreMatcher(patterns)

        # Test complex matches
        assert matcher.match(base_path / "src/ignore_me", base_path)
        assert matcher.match(base_path / "docs/readme.md", base_path)
        assert matcher.match(base_path / "root.txt", base_path)

        # Test . (hidden files) handled by .*
        assert matcher.match(base_path / ".hidden", base_path)

        # Test non-matches
        assert not matcher.match(base_path / "other/ignore_me", base_path) # anchored path
        assert not matcher.match(base_path / "docs/script.js", base_path)
        assert not matcher.match(base_path / "sub/root.txt", base_path) # anchored at root

        # Verify optimization categories
        assert not matcher.ignore_names

        # ".*" has * so it is complex.

        assert len(matcher.complex_patterns) == 4

    def test_ignore_matcher_mixed(self, base_path):
        patterns = {"node_modules", "*.pyc", "src/temp"}
        matcher = IgnoreMatcher(patterns)

        assert matcher.match(base_path / "node_modules", base_path)
        assert matcher.match(base_path / "file.pyc", base_path)
        assert matcher.match(base_path / "src/temp", base_path)

        assert not matcher.match(base_path / "src/main.py", base_path)

    def test_consistency_with_should_ignore_path(self, base_path):
        """Verify IgnoreMatcher produces same results as should_ignore_path for various cases."""
        patterns = {
            "node_modules", "*.pyc", "dist", ".git",
            "src/temp", "/config.json", "docs/*.md"
        }

        test_paths = [
            base_path / "node_modules",
            base_path / "src/node_modules/pkg",
            base_path / "main.pyc",
            base_path / "dist/bundle.js",
            base_path / ".git/HEAD",
            base_path / "src/temp/file",
            base_path / "config.json",
            base_path / "sub/config.json",
            base_path / "docs/intro.md",
            base_path / "docs/script.js",
            base_path / "README.md",
        ]

        matcher = IgnoreMatcher(patterns)

        for path in test_paths:
            expected = should_ignore_path(path, base_path, patterns)
            actual = matcher.match(path, base_path)
            assert actual == expected, f"Mismatch for {path}: expected {expected}, got {actual}"
