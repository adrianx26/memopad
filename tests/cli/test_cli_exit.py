"""Regression tests for CLI command exit behavior.

These tests verify that CLI commands exit cleanly without hanging,
which was a bug fixed in the database initialization refactor.
"""

import subprocess
from pathlib import Path


def test_bm_version_exits_cleanly():
    """Test that 'bm --version' exits cleanly within timeout."""
    # Use uv run to ensure correct environment
    # Use sys.executable to ensure we use the same python environment
    import sys
    import os

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parent.parent.parent / "src")

    result = subprocess.run(
        [sys.executable, "-m", "memopad.cli.main", "--version"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=Path(__file__).parent.parent.parent,  # Project root
        env=env,
    )
    assert result.returncode == 0
    assert "MemoPad version:" in result.stdout


def test_bm_help_exits_cleanly():
    """Test that 'bm --help' exits cleanly within timeout."""
    import sys
    import os

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parent.parent.parent / "src")

    result = subprocess.run(
        [sys.executable, "-m", "memopad.cli.main", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=Path(__file__).parent.parent.parent,
        env=env,
    )
    assert result.returncode == 0
    assert "Memopad" in result.stdout


def test_bm_tool_help_exits_cleanly():
    """Test that 'bm tool --help' exits cleanly within timeout."""
    import sys
    import os

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parent.parent.parent / "src")

    result = subprocess.run(
        [sys.executable, "-m", "memopad.cli.main", "tool", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=Path(__file__).parent.parent.parent,
        env=env,
    )
    assert result.returncode == 0
    assert "tool" in result.stdout.lower()
