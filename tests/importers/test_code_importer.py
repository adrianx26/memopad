"""Tests for the CodeGraph importer pure logic (Tb G2).

Covers the Python regex parser, the graph builder (entities + relations with
`code://` permalinks), permalink helpers, name validation, and the file scanner
skip-list. No DB, no I/O beyond tmp_path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memopad.importers.code_importer import (
    CONTENT_TYPE_PYTHON,
    ENTITY_CLASS,
    ENTITY_FILE,
    ENTITY_FUNCTION,
    ENTITY_MODULE,
    CodeGraphBuilder,
    CodeGraphError,
    PythonRegexParser,
    code_permalink,
    dotted_module_name,
    get_parser,
    iter_source_files,
    language_for_extension,
    parse_code_permalink,
    parse_file,
    REL_CALLS,
    REL_DEFINED_IN,
    REL_IMPORTS,
)


# --- permalink helpers -----------------------------------------------------


def test_code_permalink_file_and_symbol():
    assert code_permalink("p", "a/b.py") == "code://p/a/b.py"
    assert code_permalink("p", "a/b.py", "foo") == "code://p/a/b.py::foo"


def test_parse_code_permalink_roundtrip():
    p = code_permalink("proj", "pkg/mod.py", "Klass")
    parts = parse_code_permalink(p)
    assert parts == {"project": "proj", "rel_path": "pkg/mod.py", "symbol": "Klass"}
    assert parse_code_permalink(code_permalink("proj", "x.py"))["symbol"] is None


def test_parse_code_permalink_rejects_non_code():
    with pytest.raises(CodeGraphError):
        parse_code_permalink("https://x/y")


def test_dotted_module_name():
    assert dotted_module_name("pkg/sub/mod.py") == "pkg.sub.mod"
    assert dotted_module_name("pkg/sub/__init__.py") == "pkg.sub"
    assert dotted_module_name("mod.py") == "mod"


# --- parser ----------------------------------------------------------------


PY_SOURCE = '''\
"""module docstring."""
import os
from memopad.config import MemoPadConfig

def alpha(x):
    return beta(x) + 1

def beta(y):
    return y * 2

class Foo:
    def method(self):
        return alpha(1)

    def helper(self):
        return beta(self.x)
'''


def test_python_parser_extracts_symbols_imports_calls():
    parser = PythonRegexParser()
    r = parser.parse(PY_SOURCE, rel_path="pkg/mod.py")
    assert r.module_name == "pkg.mod"
    names = {s.name: s for s in r.symbols}
    assert {"alpha", "beta", "Foo"} <= set(names)
    assert names["alpha"].kind == ENTITY_FUNCTION
    assert names["Foo"].kind == ENTITY_CLASS
    # Imports.
    mods = {i.module for i in r.imports}
    assert "os" in mods and "memopad.config" in mods
    # alpha calls beta.
    assert "beta" in names["alpha"].calls
    # Foo.method calls alpha.
    method = next(s for s in r.symbols if s.name == "method")
    assert "alpha" in method.calls


def test_get_parser_unsupported_raises():
    with pytest.raises(CodeGraphError):
        get_parser("rust")


def test_language_for_extension():
    assert language_for_extension(".py") == "python"
    assert language_for_extension(".rs") is None


# --- builder ---------------------------------------------------------------


def test_builder_emits_file_module_symbol_entities_and_relations():
    parser = PythonRegexParser()
    r = parser.parse(PY_SOURCE, rel_path="pkg/mod.py")
    graph = CodeGraphBuilder("proj").build([r])

    types = [e.entity_type for e in graph.entities]
    assert ENTITY_FILE in types
    assert ENTITY_MODULE in types
    assert ENTITY_FUNCTION in types
    assert ENTITY_CLASS in types

    # Permalinks well-formed.
    file_entity = next(e for e in graph.entities if e.entity_type == ENTITY_FILE)
    assert file_entity.file_path == "code://proj/pkg/mod.py"
    assert file_entity.permalink == file_entity.file_path
    assert file_entity.content_type == CONTENT_TYPE_PYTHON

    alpha = next(e for e in graph.entities if e.title == "alpha")
    assert alpha.file_path == "code://proj/pkg/mod.py::alpha"
    assert alpha.entity_metadata["qualified_name"] == "pkg.mod.alpha"

    # defined_in: symbol -> file.
    defined_in = [rel for rel in graph.relations if rel.relation_type == REL_DEFINED_IN]
    alpha_def = next(rel for rel in defined_in if rel.from_permalink == alpha.file_path)
    assert alpha_def.to_permalink == file_entity.file_path

    # calls: alpha -> beta (resolved same-file).
    calls = [rel for rel in graph.relations if rel.relation_type == REL_CALLS]
    alpha_call = next(rel for rel in calls if rel.from_permalink == alpha.file_path)
    assert alpha_call.to_name == "beta"
    assert alpha_call.to_permalink == "code://proj/pkg/mod.py::beta"

    # imports: file -> module (os unresolved; memopad.config unresolved too).
    imports = [rel for rel in graph.relations if rel.relation_type == REL_IMPORTS]
    assert any(i.to_name == "os" and i.to_permalink is None for i in imports)


def test_builder_resolves_cross_file_calls():
    src_a = "def shared():\n    return 1\n"
    src_b = "def caller():\n    return shared()\n"
    parser = PythonRegexParser()
    ra = parser.parse(src_a, rel_path="a.py")
    rb = parser.parse(src_b, rel_path="b.py")
    graph = CodeGraphBuilder("proj").build([ra, rb])
    caller = next(e for e in graph.entities if e.title == "caller")
    call = next(
        rel for rel in graph.relations
        if rel.relation_type == REL_CALLS and rel.from_permalink == caller.file_path
    )
    assert call.to_name == "shared"
    assert call.to_permalink == "code://proj/a.py::shared"  # resolved cross-file


def test_builder_resolves_imports_to_known_module():
    # a.py defines a module; b.py imports it.
    src_a = "def f():\n    return 1\n"
    src_b = "from a import f\n"
    parser = PythonRegexParser()
    ra = parser.parse(src_a, rel_path="a.py")
    rb = parser.parse(src_b, rel_path="b.py")
    graph = CodeGraphBuilder("proj").build([ra, rb])
    b_file = next(e for e in graph.entities if e.entity_type == ENTITY_FILE and e.title == "b.py")
    imp = next(
        rel for rel in graph.relations
        if rel.relation_type == REL_IMPORTS and rel.from_permalink == b_file.file_path
    )
    assert imp.to_name == "a"
    assert imp.to_permalink is not None  # resolved to a's module entity
    assert "module:a" in imp.to_permalink


def test_builder_impacts_not_stored():
    r = PythonRegexParser().parse(PY_SOURCE, rel_path="pkg/mod.py")
    graph = CodeGraphBuilder("proj").build([r])
    assert not any(rel.relation_type == "impacts" for rel in graph.relations)


# --- file scanning ---------------------------------------------------------


def test_iter_source_files_skips_junk(tmp_path):
    (tmp_path / "a.py").write_text("x = 1")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("y = 2")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "c.pyc").write_text("z")
    (tmp_path / "README.md").write_text("hi")
    files = iter_source_files(tmp_path)
    rels = [p.relative_to(tmp_path).as_posix() for p in files]
    assert "a.py" in rels and "sub/b.py" in rels
    assert not any("pyc" in r or "__pycache__" in r for r in rels)
    assert not any(r.endswith(".md") for r in rels)


def test_parse_file_round_trip(tmp_path):
    p = tmp_path / "mod.py"
    p.write_text("def f():\n    return 1\n")
    r = parse_file(p, tmp_path)
    assert r.module_name == "mod"
    assert r.symbols[0].name == "f"


def test_parse_file_unsupported_raises(tmp_path):
    p = tmp_path / "x.rs"
    p.write_text("fn main(){}")
    with pytest.raises(CodeGraphError):
        parse_file(p, tmp_path)