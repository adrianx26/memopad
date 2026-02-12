"""Integration tests for version command."""

from typer.testing import CliRunner

from memopad.cli.main import app
import memopad


def test_version_command():
    """Test 'bm --version' command shows version."""
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert memopad.__version__ in result.stdout
