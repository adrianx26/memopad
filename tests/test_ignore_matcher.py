import pytest
from pathlib import Path
from memopad.ignore_utils import IgnoreMatcher

class TestIgnoreMatcher:
    @pytest.fixture
    def patterns(self):
        return {
            ".git",
            "node_modules",
            "*.db",
            "/dist/",
            "build_*/",
            "test_*.py",
            "/config.json",
        }

    @pytest.fixture
    def matcher(self, patterns):
        return IgnoreMatcher(patterns)

    def test_exact_names(self, matcher):
        base = Path("/app")
        assert matcher.match(Path("/app/.git/HEAD"), base)
        assert matcher.match(Path("/app/node_modules/pkg/index.js"), base)
        assert matcher.match(Path("/app/src/node_modules/pkg/index.js"), base)
        assert not matcher.match(Path("/app/src/main.py"), base)

    def test_extensions(self, matcher):
        base = Path("/app")
        assert matcher.match(Path("/app/data.db"), base)
        assert matcher.match(Path("/app/src/data.db"), base)
        assert not matcher.match(Path("/app/data.txt"), base)

    def test_root_dirs(self, matcher):
        base = Path("/app")
        assert matcher.match(Path("/app/dist/main.js"), base)
        assert not matcher.match(Path("/app/src/dist/main.js"), base)

    def test_root_files(self, matcher):
        base = Path("/app")
        assert matcher.match(Path("/app/config.json"), base)
        assert not matcher.match(Path("/app/src/config.json"), base)

    def test_directory_glob(self, matcher):
        base = Path("/app")
        assert matcher.match(Path("/app/build_123/output.txt"), base)
        assert matcher.match(Path("/app/src/build_456/temp.log"), base)
        assert not matcher.match(Path("/app/build/output.txt"), base)

    def test_file_glob(self, matcher):
        base = Path("/app")
        assert matcher.match(Path("/app/test_api.py"), base)
        assert matcher.match(Path("/app/src/test_utils.py"), base)
        assert not matcher.match(Path("/app/api_test.py"), base)

    def test_match_entry(self, matcher):
        assert matcher.match_entry(".git")
        assert matcher.match_entry("node_modules")
        assert matcher.match_entry("data.db")
        assert not matcher.match_entry("main.py")
        assert not matcher.match_entry("dist") # Root dir logic not applicable for simple entry name check
