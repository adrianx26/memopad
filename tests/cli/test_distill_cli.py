"""Unit tests for `memopad distill` CLI command (Tb L0-L3).

Only the pure dispatch glue is tested here — `_parse_levels` (a pure function)
and the command's routing/argument validation. The async HTTP paths
(`_run_distill` / `_run_dry_run`) require a live server and are not unit-tested,
consistent with `doctor.py`'s test split.
"""

from __future__ import annotations

import inspect

import pytest
from typer.testing import CliRunner

from memopad.cli.commands import distill as distill_cmd


def test_parse_levels_single():
    assert distill_cmd._parse_levels("L1") == ["L1"]


def test_parse_levels_preserves_pipeline_order_and_dedups():
    # Out-of-order + duplicate + whitespace + mixed case → ordered, unique.
    assert distill_cmd._parse_levels(" l3, L1 ,l2,L1 ") == ["L1", "L2", "L3"]


def test_parse_levels_rejects_invalid():
    with pytest.raises(Exception):
        distill_cmd._parse_levels("L1,L4")


def test_parse_levels_rejects_empty():
    with pytest.raises(Exception):
        distill_cmd._parse_levels(",,,")


def _coro_name(coro) -> str:
    """Return the qualified name of the awaited coroutine's underlying function."""
    return coro.cr_frame.f_code.co_qualname if coro else ""


@pytest.fixture
def captured_run(monkeypatch):
    """Replace run_with_cleanup with a recorder that closes the coroutine.

    The command builds the coroutine and passes it to `run_with_cleanup`; we
    intercept it so we can assert *which* async path was dispatched without
    running it (no server needed). The coroutine's qualified name is captured
    *before* close (cr_frame is None after close), and the coroutine is closed
    to avoid an "awaited-never" RuntimeWarning.
    """
    recorded: list[str] = []

    def fake_run_with_cleanup(coro):
        recorded.append(_coro_name(coro))
        coro.close()
        return None

    monkeypatch.setattr(distill_cmd, "run_with_cleanup", fake_run_with_cleanup)
    return recorded


def test_distill_command_dispatches_dry_run(captured_run):
    runner = CliRunner()
    result = runner.invoke(distill_cmd.app, ["distill", "--dry-run"])
    assert result.exit_code == 0
    assert captured_run == ["_run_dry_run"]


def test_distill_command_dispatches_pass_by_default(captured_run):
    runner = CliRunner()
    result = runner.invoke(distill_cmd.app, ["distill", "--level", "L1,L2,L3"])
    assert result.exit_code == 0
    assert captured_run == ["_run_distill"]


def test_distill_command_rejects_invalid_level(captured_run):
    runner = CliRunner()
    result = runner.invoke(distill_cmd.app, ["distill", "--level", "L9"])
    # BadParameter → the command prints the error and exits non-zero.
    assert result.exit_code != 0
    assert "invalid level" in result.output.lower()
    # The validation happens before any coroutine is dispatched.
    assert captured_run == []


def test_distill_command_rejects_max_memories_out_of_range(captured_run):
    runner = CliRunner()
    result = runner.invoke(distill_cmd.app, ["distill", "--max-memories", "0"])
    assert result.exit_code != 0
    assert "between 1 and 1000" in result.output.lower()
    assert captured_run == []


def test_distill_async_runners_are_async():
    """The dispatched coroutines must actually be async functions (sanity)."""
    assert inspect.iscoroutinefunction(distill_cmd._run_distill)
    assert inspect.iscoroutinefunction(distill_cmd._run_dry_run)