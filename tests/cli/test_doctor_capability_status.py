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


def _state_of(label: str, lines: list[str]) -> str:
    """Return the on/off state token for the capability line starting with `label:`.

    Each capability prints on its own line: `  <label>:<padded> <state>  (hint)`.
    We locate that line and read the state token after the label. This avoids
    counting the header text (which, since 0.20.2, contains the word "on" in
    "...on since 0.20.2") or hint substrings.
    """
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(label + ":"):
            # state is the first token after the label (after the padded colon).
            tail = stripped[len(label) + 1 :].strip()
            return tail.split()[0]
    raise AssertionError(f"no capability line for {label!r}; lines were:\n{lines!r}")


def test_capability_status_default_state(monkeypatch):
    """Default config: G6 short-term reports ON (default on since 0.20.2),
    every other capability reports off, G4 params show 0."""
    buf = _capture_console(monkeypatch)
    config = MemoPadConfig()  # G6 on by default; all others off

    doctor.print_capability_status(config)

    lines = buf.getvalue().splitlines()
    # Header present.
    assert any("Tb-borrowed capabilities" in ln for ln in lines)
    # Every boolean flag label + hint present.
    out = "\n".join(lines)
    for _, label, hint in doctor._CAPABILITY_FLAGS:
        assert label in out
        assert hint in out
    # G4 params present with their disabled value (0).
    assert "recall_max_chars_per_memory" in out
    assert "recall_timeout_ms" in out
    # Per-flag state: G6 on, every other capability flag off.
    for attr, label, _ in doctor._CAPABILITY_FLAGS:
        state = _state_of(label, lines)
        if attr == "shortterm_enabled":
            assert state == "on", f"{label} should be ON by default, got {state!r}"
        else:
            assert state == "off", f"{label} should be off by default, got {state!r}"


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