"""Tests for the in-task short-term context layering service (Tb G6).

The service is file-backed local I/O plus pure policy functions, so tests use a
tmp_path session dir and a deterministic token counter. They cover the three
layers, the offload thresholds (0.5 / 0.85), the Mermaid cap (0.2), drill-down by
node id, name validation, and the non-breaking store-only mode (budget 0).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memopad.config import MemoPadConfig
from memopad.services.shortterm_context import (
    DEFAULT_AGGRESSIVE_COMPRESS_RATIO,
    DEFAULT_MILD_OFFLOAD_RATIO,
    DEFAULT_MMD_MAX_TOKEN_RATIO,
    LEVEL_AGGRESSIVE,
    LEVEL_MILD,
    LEVEL_NONE,
    ShortTermConfig,
    ShortTermContext,
    ShortTermError,
    estimate_tokens,
    offload_level_for,
    render_mermaid,
    safe_ref_name,
)


def _cfg(budget: int = 1000, **ov) -> ShortTermConfig:
    base = dict(
        token_budget=budget,
        mild_offload_ratio=DEFAULT_MILD_OFFLOAD_RATIO,
        aggressive_compress_ratio=DEFAULT_AGGRESSIVE_COMPRESS_RATIO,
        mmd_max_token_ratio=DEFAULT_MMD_MAX_TOKEN_RATIO,
    )
    base.update(ov)
    return ShortTermConfig(**base)


def _st(tmp_path: Path, budget: int = 1000, **ov) -> ShortTermContext:
    return ShortTermContext(tmp_path / "sess", _cfg(budget, **ov))


# --- pure policy -----------------------------------------------------------


def test_offload_level_zero_budget_is_store_only():
    cfg = _cfg(budget=0)
    assert offload_level_for(10_000, cfg) == LEVEL_NONE


def test_offload_level_thresholds():
    cfg = _cfg(budget=1000)
    # 499 < 500 -> none
    assert offload_level_for(499, cfg) == LEVEL_NONE
    # 500 -> mild
    assert offload_level_for(500, cfg) == LEVEL_MILD
    # 849 -> still mild
    assert offload_level_for(849, cfg) == LEVEL_MILD
    # 850 -> aggressive (checked before mild)
    assert offload_level_for(850, cfg) == LEVEL_AGGRESSIVE
    assert offload_level_for(5000, cfg) == LEVEL_AGGRESSIVE


def test_safe_ref_name_rejects_traversal_and_paths():
    assert safe_ref_name("note1") == "note1"
    assert safe_ref_name("a_b-c.d") == "a_b-c.d"
    for bad in ["", "..", ".", "a/b", "a\\b", "/abs", " leading", ".dotfile", ""]:
        with pytest.raises(ShortTermError):
            safe_ref_name(bad)


def test_render_mermaid_unlimited_includes_all():
    from memopad.services.shortterm_context import StepRecord

    steps = [
        StepRecord(0, "read_note", "read architecture", None, 10, "t0"),
        StepRecord(1, "search", "found 3 hits", None, 10, "t1"),
        StepRecord(2, "write_note", "wrote summary", None, 10, "t2"),
    ]
    mmd = render_mermaid(steps, max_tokens=0)
    assert mmd.startswith("flowchart TD")
    assert 's0["read_note: read architecture"]' in mmd
    assert "s0 --> s1" in mmd
    assert "s1 --> s2" in mmd
    assert "sMore" not in mmd


def test_render_mermaid_cap_drops_tail_with_hint():
    from memopad.services.shortterm_context import StepRecord

    steps = [
        StepRecord(i, "tool", f"step number {i} with some padding text", None, 10, f"t{i}")
        for i in range(20)
    ]
    # Tiny cap: only a couple of nodes fit.
    mmd = render_mermaid(steps, max_tokens=30)
    assert "sMore" in mmd  # tail was dropped with a drill-down hint
    # The hint reports how many were dropped.
    assert "more steps" in mmd


# --- service file I/O ------------------------------------------------------


def test_add_ref_writes_and_reads_back(tmp_path):
    st = _st(tmp_path)
    path = st.add_ref("out1", "# raw\nbody text")
    assert path.is_file()
    assert path.name == "out1.md"
    assert st.read_ref("out1") == "# raw\nbody text"


def test_add_ref_overwrites(tmp_path):
    st = _st(tmp_path)
    st.add_ref("out1", "v1")
    st.add_ref("out1", "v2")
    assert st.read_ref("out1") == "v2"


def test_add_ref_rejects_unsafe_name(tmp_path):
    st = _st(tmp_path)
    with pytest.raises(ShortTermError):
        st.add_ref("../escape", "x")


def test_read_ref_missing_raises(tmp_path):
    st = _st(tmp_path)
    with pytest.raises(ShortTermError):
        st.read_ref("nope")


def test_add_step_appends_and_indexes(tmp_path):
    st = _st(tmp_path)
    s0 = st.add_step("read_note", "read foo", ref_name="out1", timestamp="t0")
    s1 = st.add_step("search", "found bar", timestamp="t1")
    assert s0.index == 0
    assert s1.index == 1
    assert s0.node_id() == "s0"
    assert s0.ref_name == "out1"
    assert s1.ref_name is None
    loaded = st.steps()
    assert len(loaded) == 2
    assert loaded[0].tool == "read_note"
    assert loaded[1].summary == "found bar"
    assert st.step_count() == 2


def test_add_step_validates_ref_name(tmp_path):
    st = _st(tmp_path)
    with pytest.raises(ShortTermError):
        st.add_step("t", "s", ref_name="bad/name")


def test_steps_persist_across_instances(tmp_path):
    st = _st(tmp_path)
    st.add_step("read_note", "read foo", timestamp="t0")
    # Re-open the same session dir -> steps survive on disk.
    st2 = ShortTermContext(tmp_path / "sess", _cfg())
    assert st2.step_count() == 1
    assert st2.steps()[0].summary == "read foo"


# --- offload + injection ---------------------------------------------------


def test_offload_none_injects_refs_and_steps(tmp_path):
    # Budget large vs content -> none.
    st = _st(tmp_path, budget=10_000)
    st.add_ref("r1", "x" * 40)
    st.add_step("t", "summary", ref_name="r1", timestamp="t0")
    assert st.offload_level() == LEVEL_NONE
    inj = st.build_injection()
    assert inj["offload_level"] == LEVEL_NONE
    assert "refs" in inj and "steps" in inj
    assert "canvas" not in inj


def test_offload_mild_drops_refs_keeps_steps(tmp_path):
    # Make raw cross 0.5 but stay under 0.85. Budget 1000 -> mild at 500..849.
    st = _st(tmp_path, budget=1000)
    # refs ~ 200 tokens (800 chars), steps small -> raw ~200 -> under mild. Push refs up.
    st.add_ref("big", "y" * 2400)  # 600 tokens -> crosses 500, under 850
    st.add_step("t", "small", timestamp="t0")
    assert st.offload_level() == LEVEL_MILD
    inj = st.build_injection()
    assert inj["offload_level"] == LEVEL_MILD
    assert "steps" in inj
    assert "refs" not in inj  # refs offloaded from injection
    assert "canvas" not in inj


def test_offload_aggressive_regenerates_canvas_and_injects_it(tmp_path):
    st = _st(tmp_path, budget=1000)
    # Cross 0.85: need >=850 raw tokens.
    st.add_ref("huge", "z" * 3600)  # 900 tokens -> aggressive
    for i in range(3):
        st.add_step("t", f"step {i}", timestamp=f"t{i}")
    level = st.maybe_offload()
    assert level == LEVEL_AGGRESSIVE
    assert st.canvas_file.is_file()  # regenerated
    inj = st.build_injection()
    assert inj["offload_level"] == LEVEL_AGGRESSIVE
    assert "canvas" in inj
    assert "steps" not in inj and "refs" not in inj
    canvas = inj["canvas"]
    assert isinstance(canvas, str) and canvas.startswith("flowchart TD")


def test_maybe_offload_mild_does_not_write_canvas(tmp_path):
    st = _st(tmp_path, budget=1000)
    st.add_ref("big", "y" * 2400)
    st.add_step("t", "s", timestamp="t0")
    level = st.maybe_offload()
    assert level == LEVEL_MILD
    assert not st.canvas_file.is_file()  # no regeneration at mild


def test_canvas_capped_to_mmd_ratio(tmp_path):
    # budget 1000 -> canvas cap = 0.2 * 1000 = 200 tokens (soft: the first node
    # and the tail hint can push it slightly over, but it stays bounded and far
    # below the uncapped size).
    from memopad.services.shortterm_context import StepRecord

    st = _st(tmp_path, budget=1000)
    for i in range(40):
        st.add_step("tool", f"step {i} " + "k" * 50, timestamp=f"t{i}")
    mmd = st.render_canvas()
    assert "sMore" in mmd  # 40 steps cannot fit in 200 tokens -> tail dropped
    # Soft cap: bounded near 200, not the full 40-step canvas.
    assert estimate_tokens(mmd) <= 200 + 60
    # Uncapped render is substantially larger -> the cap actually trimmed.
    uncapped = render_mermaid([s for s in st.steps()], max_tokens=0)
    assert estimate_tokens(uncapped) > estimate_tokens(mmd)
    assert "sMore" not in uncapped


# --- drill-down ------------------------------------------------------------


def test_drill_down_returns_step_and_ref(tmp_path):
    st = _st(tmp_path, budget=10_000)
    st.add_ref("r1", "raw output body")
    st.add_step("read_note", "read architecture", ref_name="r1", timestamp="t0")
    out = st.drill_down("s0")
    assert out["step"].summary == "read architecture"
    assert out["ref_content"] == "raw output body"


def test_drill_down_step_without_ref_has_no_ref_content(tmp_path):
    st = _st(tmp_path, budget=10_000)
    st.add_step("search", "found hits", timestamp="t0")
    out = st.drill_down("s0")
    assert out["step"].tool == "search"
    assert "ref_content" not in out


def test_drill_down_missing_ref_surfaced(tmp_path):
    st = _st(tmp_path, budget=10_000)
    # Link a ref that was never written.
    st.add_step("read_note", "read X", ref_name="ghost", timestamp="t0")
    out = st.drill_down("s0")
    assert out["ref_content"] is None
    assert out["ref_missing"] == "ghost"


def test_drill_down_bad_node_id_raises(tmp_path):
    st = _st(tmp_path)
    for bad in ["", "0", "step0", "sx", "s-1", "s"]:
        with pytest.raises(ShortTermError):
            st.drill_down(bad)


def test_drill_down_missing_step_raises(tmp_path):
    st = _st(tmp_path, budget=10_000)
    st.add_step("t", "s", timestamp="t0")
    with pytest.raises(ShortTermError):
        st.drill_down("s9")


# --- config projection + end-of-session -----------------------------------


def test_shortterm_config_from_app_config():
    c = MemoPadConfig()
    # Defaults: budget 0, ratios 0.5/0.85/0.2.
    cfg = ShortTermConfig.from_app_config(c)
    assert cfg.token_budget == c.shortterm_context_token_budget == 0
    assert cfg.mild_offload_ratio == 0.5
    assert cfg.aggressive_compress_ratio == 0.85
    assert cfg.mmd_max_token_ratio == 0.2


def test_stable_steps_returns_persisted_steps(tmp_path):
    st = _st(tmp_path, budget=10_000)
    st.add_step("a", "first", timestamp="t0")
    st.add_step("b", "second", timestamp="t1")
    stable = st.stable_steps()
    assert [s.summary for s in stable] == ["first", "second"]


def test_clear_removes_session_dir(tmp_path):
    st = _st(tmp_path, budget=10_000)
    st.add_ref("r1", "x")
    st.add_step("t", "s", timestamp="t0")
    st.regenerate_canvas()
    assert st.session_dir.is_dir()
    st.clear()
    assert not st.session_dir.exists()


def test_disabled_by_default_is_store_only(tmp_path):
    # App default config has budget 0 -> store-only, no compression regardless of size.
    cfg = ShortTermConfig.from_app_config(MemoPadConfig())
    st = ShortTermContext(tmp_path / "sess", cfg)
    st.add_ref("big", "x" * 10_000)
    st.add_step("t", "s", timestamp="t0")
    assert st.offload_level() == LEVEL_NONE
    inj = st.build_injection()
    # With budget 0, nothing is compressed; refs + steps are both injected.
    assert "refs" in inj and "steps" in inj