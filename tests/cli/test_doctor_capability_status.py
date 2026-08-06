"""Unit tests for `memopad doctor`'s Tb-borrowed capability status report.

Only the pure `print_capability_status` is tested here — it reads a `MemoPadConfig`
directly and prints, so no server/HTTP is needed. The async probes
(`run_capability_probes`) and the roundtrip/drift flows require a live server and
are not unit-tested, consistent with the rest of `doctor.py`.
"""

from __future__ import annotations

import io

from rich.console import Console

from memopad.cli.commands import doctor
from memopad.config import MemoPadConfig


def _capture_console(monkeypatch) -> io.StringIO:
    """Replace doctor's module console with a plain-text capturing one."""
    buf = io.StringIO()
    monkeypatch.setattr(doctor, "console", Console(file=buf, no_color=True, width=200))
    return buf


def test_capability_status_all_off_by_default(monkeypatch):
    """Default config: every capability reports off, G4 params show 0."""
    buf = _capture_console(monkeypatch)
    config = MemoPadConfig()  # all flags default off

    doctor.print_capability_status(config)

    out = buf.getvalue()
    # Header present.
    assert "Tb-borrowed capabilities" in out
    # Every boolean flag reported off.
    for _, label, hint in doctor._CAPABILITY_FLAGS:
        assert label in out
        assert hint in out
    # G4 params present with their disabled value (0).
    assert "recall_max_chars_per_memory" in out
    assert "recall_timeout_ms" in out
    assert " off " in out
    # Nothing is reported as on under the default config. Use the space-padded
    # state token so "on" inside a hint word like "context" doesn't false-match.
    assert " on " not in out


def test_capability_status_reflects_enabled_flags(monkeypatch):
    """When flags are on, the report says on for each enabled capability + G4."""
    buf = _capture_console(monkeypatch)
    config = MemoPadConfig(
        levels_enabled=True,
        levels_pipeline_automatic=True,
        skills_enabled=True,
        codegraph_enabled=True,
        shortterm_enabled=True,
        recall_max_chars_per_memory=5000,
        recall_timeout_ms=2000,
    )

    doctor.print_capability_status(config)

    out = buf.getvalue()
    # Five "on" states for the boolean flags + two for the G4 params = 7 total.
    # Use the space-padded state token to avoid matching "on" inside hint words.
    assert out.count(" on ") >= 7
    # G4 numeric values are echoed.
    assert "5000" in out
    assert "2000ms" in out
    # The enabled flags' hints still appear.
    for _, label, hint in doctor._CAPABILITY_FLAGS:
        assert hint in out