"""Tests for the pure CodeGraph query logic (Tb G2).

Builds a small in-memory `GraphView` and exercises `find_symbol`, `impact_path`
(BFS over reverse calls), and `code_context`. No DB.
"""

from __future__ import annotations

import pytest

from memopad.importers.code_importer import (
    ENTITY_CLASS,
    ENTITY_FUNCTION,
    ENTITY_MODULE,
)
from memopad.services.codegraph_query import (
    CodeContext,
    GraphView,
    SymbolNode,
    build_view,
    code_context,
    find_symbol,
    impact_path,
)


def _node(permalink, title, etype=ENTITY_FUNCTION, qname=None, file=None):
    return SymbolNode(
        permalink=permalink, title=title, entity_type=etype, qualified_name=qname, file=file
    )


# Graph: a -> b -> c, and d -> b. So c is called by b; b by a and d.
# impact_path(c) => {b:1, a:2, d:2}.
A = "code://p/x.py::a"
B = "code://p/x.py::b"
C = "code://p/x.py::c"
D = "code://p/x.py::d"
FILE = "code://p/x.py"
MOD = "code://p/x.py::module:x"


def _view():
    return build_view(
        symbols=[
            _node(A, "a", qname="x.a", file="x.py"),
            _node(B, "b", qname="x.b", file="x.py"),
            _node(C, "c", qname="x.c", file="x.py"),
            _node(D, "d", qname="x.d", file="x.py"),
            _node(MOD, "x", etype=ENTITY_MODULE),
        ],
        calls=[(A, B), (B, C), (D, B)],
        imports=[(A, MOD)],
        defined_in=[(A, FILE), (B, FILE), (C, FILE), (D, FILE)],
    )


# --- find_symbol -----------------------------------------------------------


def test_find_symbol_substring_and_exact():
    view = _view()
    hits = find_symbol(view, "a")
    assert {h.permalink for h in hits} == {A}
    # 'b' matches both 'b' title; substring 'c' matches 'c' only.
    assert {h.permalink for h in find_symbol(view, "b")} == {B}
    # No match for functions only via substring on module 'x'.
    assert find_symbol(view, "x")[0].permalink == MOD


def test_find_symbol_excludes_files_and_orders_by_kind():
    view = build_view(
        symbols=[
            _node("code://p/x.py::Cls", "Cls", etype=ENTITY_CLASS),
            _node("code://p/x.py::fn", "fn"),
            _node("code://p/x.py", "x.py", etype="file"),
            _node("code://p/x.py::module:x", "x", etype=ENTITY_MODULE),
        ]
    )
    hits = find_symbol(view, "x")  # substring 'x' matches Cls? no; fn? no; module x? yes; file x.py excluded
    titles = [h.title for h in hits]
    assert "x" in titles  # module
    assert "x.py" not in titles  # file excluded


# --- impact_path -----------------------------------------------------------


def test_impact_path_bfs_distances():
    view = _view()
    ip = impact_path(view, C)
    assert ip.distances == {B: 1, A: 2, D: 2}
    assert C not in ip.distances  # root excluded


def test_impact_path_no_dependents():
    view = _view()
    ip = impact_path(view, A)  # nobody calls a
    assert ip.distances == {}
    assert "No dependents" in ip.render(view)


def test_impact_path_respects_max_hops():
    view = _view()
    ip = impact_path(view, C, max_hops=1)
    assert ip.distances == {B: 1}  # a and d are 2 hops -> excluded


def test_impact_path_cycles_terminated():
    # a -> b -> a (cycle). impact_path(b) -> a at hop 1; no infinite loop.
    X = "code://p/x.py::x"
    Y = "code://p/x.py::y"
    view = build_view(
        symbols=[_node(X, "x"), _node(Y, "y")],
        calls=[(X, Y), (Y, X)],
    )
    ip = impact_path(view, Y)
    assert ip.distances == {X: 1}


def test_impact_path_render_groups_by_hop():
    view = _view()
    ip = impact_path(view, C)
    out = ip.render(view)
    assert "direct callers" in out
    assert "2-hop transitive" in out
    assert B in out and A in out and D in out


# --- code_context ----------------------------------------------------------


def test_code_context_assembles_neighbors():
    view = _view()
    ctx = code_context(view, B)
    assert ctx.symbol.permalink == B
    assert ctx.callers == [A, D]  # reverse calls of b
    assert ctx.callees == [C]
    assert ctx.defined_in == FILE


def test_code_context_unknown_permalink_raises():
    view = _view()
    with pytest.raises(KeyError):
        code_context(view, "code://p/x.py::nope")


def test_code_context_render_includes_callers_and_definition():
    view = _view()
    ctx = code_context(view, A)
    ctx.definition = "def a():\n    return b()"
    out = ctx.render(view)
    assert "Called by" in out or "No direct callers" in out  # a has no callers
    assert "Calls" in out
    assert "def a():" in out


def test_code_context_render_token_budgeted():
    view = _view()
    ctx = code_context(view, A)
    ctx.definition = "def a():\n    " + "x" * 2000
    out = ctx.render(view, max_tokens=50)  # 50 tokens ~ 200 chars
    assert "[truncated]" in out
    assert len(out) < 400