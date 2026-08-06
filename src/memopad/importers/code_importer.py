"""CodeGraph importer — index source code into the existing Entity/Relation graph.

MemoPad indexes markdown; Tb indexes code (symbols, files, call/impact
relations). This module adds the latter by **reusing the existing graph** — no
new tables, no schema changes — so code lives alongside notes and is queryable
through the same Entity/Observation/Relation repositories.

Two layers, kept strictly apart:

* **Pure parsing + graph building** (this module): deterministic, no I/O, no DB.
  A pluggable `CodeParser` Protocol turns a source file into symbols/imports/
  calls; a `CodeGraphBuilder` turns those into `EntityPayload`/`RelationPayload`
  with `code://` permalinks. The default `PythonRegexParser` needs no extra
  dependencies; a tree-sitter parser can plug into the same Protocol later.
* **DB-backed persistence + query** (`codegraph_service.py`): upserts the
  payloads via `entity_repository` / `relation_repository` and answers
  `find_symbol` / `impact_path` / `code_context`.

Permalink scheme (per the plan)::

    code://<project>/<rel_path>               -> file entity
    code://<project>/<rel_path>::<symbol>     -> function/class entity

`impacts` is **derived** (reverse of `calls`), computed at query time by the BFS
in `codegraph_query.py` — it is not stored, so it can never drift from `calls`.

Entity types: ``file``, ``function``, ``class``, ``module``.
Relation types: ``defined_in`` (symbol/module -> file), ``imports`` (file ->
module), ``calls`` (function -> function/class).

Non-breaking: the whole feature is gated by ``codegraph_enabled`` (default off)
and nothing here is wired into a hot path; the importer is invoked explicitly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Protocol

# --- Entity / relation vocabulary ------------------------------------------

ENTITY_FILE = "file"
ENTITY_FUNCTION = "function"
ENTITY_CLASS = "class"
ENTITY_MODULE = "module"

REL_DEFINED_IN = "defined_in"
REL_IMPORTS = "imports"
REL_CALLS = "calls"
# `impacts` is derived (reverse of `calls`); exported for query-layer reference.
REL_IMPACTS = "impacts"

# Content type stored on the Entity; non-markdown so it never participates in
# the markdown permalink-uniqueness constraint.
CONTENT_TYPE_PYTHON = "text/x-python"

SUPPORTED_LANGUAGES = {"python"}


class CodeGraphError(ValueError):
    """Fail-fast error for unsupported languages / malformed code input."""


# --- Parse result data structures ------------------------------------------


@dataclass
class ImportRef:
    """One import statement: the module dotted path and the imported names."""

    module: str  # dotted module path, e.g. "os.path" or "memopad.config"
    names: List[str] = field(default_factory=list)  # names imported from it


@dataclass
class Symbol:
    """A function or class extracted from a source file."""

    name: str
    kind: str  # ENTITY_FUNCTION | ENTITY_CLASS
    qualified_name: str  # module.Symbol or module.Class.method
    start_line: int  # 1-based
    end_line: int  # 1-based, inclusive
    source: str  # the definition snippet (def/class line + body)
    calls: List[str] = field(default_factory=list)  # callee names found in body


@dataclass
class ParseResult:
    """A parsed source file."""

    rel_path: str  # repo-relative posix path
    language: str
    module_name: str  # dotted module path derived from rel_path
    symbols: List[Symbol] = field(default_factory=list)
    imports: List[ImportRef] = field(default_factory=list)


# --- Payloads (input to the DB-backed service) -----------------------------


@dataclass
class EntityPayload:
    """A code entity to upsert into the graph."""

    entity_type: str
    title: str
    file_path: str  # code:// permalink — the upsert conflict key
    permalink: str  # same as file_path for code entities
    content_type: str
    content: str  # definition / source snippet
    entity_metadata: Dict[str, object] = field(default_factory=dict)


@dataclass
class RelationPayload:
    """A code relation to upsert. `to_permalink` may be None (unresolved)."""

    from_permalink: str
    to_permalink: Optional[str]
    to_name: str
    relation_type: str
    context: Optional[str] = None


# --- Permalink helpers -----------------------------------------------------


def code_permalink(project: str, rel_path: str, symbol: Optional[str] = None) -> str:
    """Build a `code://` permalink for a file or symbol.

    `rel_path` is normalized to posix. A symbol permalink appends ``::name``.
    """
    rel = PurePosixPath(rel_path).as_posix()
    base = f"code://{project}/{rel}"
    return f"{base}::{symbol}" if symbol else base


def parse_code_permalink(permalink: str) -> Dict[str, Optional[str]]:
    """Split a `code://` permalink into project / rel_path / symbol parts."""
    if not permalink.startswith("code://"):
        raise CodeGraphError(f"not a code permalink: {permalink!r}")
    body = permalink[len("code://") :]
    project, _, rest = body.partition("/")
    if not rest:
        raise CodeGraphError(f"malformed code permalink: {permalink!r}")
    rel_path, _, symbol = rest.partition("::")
    return {"project": project, "rel_path": rel_path, "symbol": symbol or None}


# --- Parser Protocol + registry --------------------------------------------


class CodeParser(Protocol):
    """A language-specific source parser.

    Implementations turn raw source text + a repo-relative path into a
    `ParseResult`. The default `PythonRegexParser` has no external deps; a
    tree-sitter parser can implement this Protocol and register via
    `register_parser` without touching the builder or service.
    """

    def parse(self, source: str, rel_path: str) -> ParseResult: ...


_PARSERS: Dict[str, CodeParser] = {}


def register_parser(language: str, parser: CodeParser) -> None:
    """Register a parser for a language (extension point for tree-sitter)."""
    _PARSERS[language] = parser


def get_parser(language: str) -> CodeParser:
    """Return the parser for a language, or raise (fail-fast, no guessing)."""
    try:
        return _PARSERS[language]
    except KeyError as e:
        raise CodeGraphError(
            f"no CodeParser registered for language {language!r}; "
            f"supported: {sorted(_PARSERS) or 'none'}"
        ) from e


def language_for_extension(suffix: str) -> Optional[str]:
    """Map a file extension to a supported language, or None if unsupported."""
    return {".py": "python"}.get(suffix.lower())


def dotted_module_name(rel_path: str) -> str:
    """Derive a dotted module name from a repo-relative path.

    `pkg/sub/mod.py` -> `pkg.sub.mod`; `pkg/sub/__init__.py` -> `pkg.sub`.
    """
    posix = PurePosixPath(rel_path).with_suffix("")
    parts = list(_posix_parts(posix))
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else "__main__"


def _posix_parts(p: PurePosixPath) -> List[str]:
    """list(p.parts) but strips a leading '.' or '/' root segment."""
    parts = [x for x in p.parts if x not in (".", "/")]
    return parts or [p.as_posix()]


# --- Python regex parser ---------------------------------------------------

# Top-level-ish definitions. We capture the name and the leading indent so we
# can find the symbol's extent (until the next definition at the same or lower
# indent, or EOF). This is intentionally simple — it handles the common case of
# module-level defs/classes and one level of nesting (methods). It does NOT aim
# to be a full AST; the tree-sitter seam is the upgrade path for edge cases.
_DEF_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<kw>async\s+def|def|class)\s+(?P<name>[A-Za-z_]\w*)")
_IMPORT_RE = re.compile(r"^[ \t]*import\s+(?P<mod>[\w.]+)")
_FROM_IMPORT_RE = re.compile(r"^[ \t]*from\s+(?P<mod>[\w.]+)\s+import\s+(?P<names>.+)")
# A call: an identifier immediately followed by '('. We exclude def/class
# declarations and control-flow keywords so we don't record them as calls.
_CALL_RE = re.compile(r"\b(?P<name>[A-Za-z_]\w*)\s*\(")
_CALL_BLACKLIST = {
    "def", "class", "if", "elif", "for", "while", "return", "with",
    "assert", "raise", "yield", "lambda", "await", "async", "print",
    "isinstance", "super",  # builtins/dunder noise; kept minimal intentionally
}
# Builtins we still want to ignore as "calls" for impact analysis (they're not
# in the project graph). Callers can refine; the builder only resolves names that
# exist as symbols, so unresolved builtins are dropped anyway.


class PythonRegexParser:
    """Dependency-free Python parser using regexes (the default `CodeParser`).

    Good enough for module-level functions/classes and methods; deliberately not
    a full AST. Returns one `ParseResult` with symbols (each carrying the call
    names found in its body) and imports.
    """

    def parse(self, source: str, rel_path: str) -> ParseResult:
        lines = source.splitlines()
        module = dotted_module_name(rel_path)
        result = ParseResult(rel_path=rel_path, language="python", module_name=module)

        # First pass: locate definition starts and their indent.
        def_starts: List[tuple[int, str, str, str]] = []  # (line_idx, indent, kw, name)
        for idx, line in enumerate(lines):
            m = _DEF_RE.match(line)
            if m:
                def_starts.append((idx, m.group("indent"), m.group("kw"), m.group("name")))

        # Second pass: for each def, determine end line (next def at <= indent).
        for i, (idx, indent, kw, name) in enumerate(def_starts):
            start_idx = idx
            end_idx = len(lines) - 1
            for j in range(i + 1, len(def_starts)):
                nidx, nindent, _, _ = def_starts[j]
                if len(nindent) <= len(indent):
                    end_idx = nidx - 1
                    break
            body = "\n".join(lines[start_idx : end_idx + 1])
            kind = ENTITY_CLASS if kw == "class" else ENTITY_FUNCTION
            calls = _extract_calls("\n".join(lines[start_idx + 1 : end_idx + 1]))
            qname = f"{module}.{name}"
            result.symbols.append(
                Symbol(
                    name=name,
                    kind=kind,
                    qualified_name=qname,
                    start_line=start_idx + 1,
                    end_line=end_idx + 1,
                    source=body,
                    calls=calls,
                )
            )

        # Imports.
        for line in lines:
            m = _FROM_IMPORT_RE.match(line)
            if m:
                names = [n.strip().split(" as ")[0].strip() for n in m.group("names").split(",")]
                names = [n for n in names if n and n != "*"]
                result.imports.append(ImportRef(module=m.group("mod"), names=names))
                continue
            m = _IMPORT_RE.match(line)
            if m:
                result.imports.append(ImportRef(module=m.group("mod"), names=[]))

        return result


def _extract_calls(text: str) -> List[str]:
    """Extract unique callee names from a function body, in order of first use."""
    seen: List[str] = []
    found: set[str] = set()
    for m in _CALL_RE.finditer(text):
        name = m.group("name")
        if name in _CALL_BLACKLIST or name in found:
            continue
        found.add(name)
        seen.append(name)
    return seen


# Register the default parser so `get_parser("python")` works out of the box.
register_parser("python", PythonRegexParser())


# --- Graph builder ---------------------------------------------------------


@dataclass
class CodeGraph:
    """The built graph: entities + relations ready to persist."""

    entities: List[EntityPayload] = field(default_factory=list)
    relations: List[RelationPayload] = field(default_factory=list)


class CodeGraphBuilder:
    """Turn parsed files into Entity/Relation payloads.

    Pure: given a list of `ParseResult` and a project name, produce a `CodeGraph`.
    Call resolution is best-effort: a callee is resolved to a symbol permalink
    when a symbol of that name exists in the same file, else left unresolved
    (`to_permalink=None`, `to_name=name`) so the service can try cross-file
    resolution later. `impacts` is NOT emitted here — it is derived at query time.
    """

    def __init__(self, project: str):
        self.project = project

    def build(self, results: List[ParseResult]) -> CodeGraph:
        graph = CodeGraph()

        # Index symbols by name per file and globally (qualified) for resolution.
        by_file: Dict[str, Dict[str, Symbol]] = {}
        global_by_name: Dict[str, Symbol] = {}
        for r in results:
            by_file[r.rel_path] = {s.name: s for s in r.symbols}
            for s in r.symbols:
                # First-seen wins for the global name index; ambiguous names are
                # resolved by file-local lookup first anyway.
                global_by_name.setdefault(s.name, s)

        for r in results:
            file_permalink = code_permalink(self.project, r.rel_path)
            # File entity (L0 — the source of truth is the file on disk).
            graph.entities.append(
                EntityPayload(
                    entity_type=ENTITY_FILE,
                    title=r.rel_path,
                    file_path=file_permalink,
                    permalink=file_permalink,
                    content_type=CONTENT_TYPE_PYTHON,
                    content=f"# {r.rel_path}\n# module: {r.module_name}\n",
                    entity_metadata={
                        "language": r.language,
                        "module": r.module_name,
                        "symbol_count": len(r.symbols),
                    },
                )
            )
            # Module entity (importable unit), defined_in the file.
            module_permalink = code_permalink(self.project, r.rel_path, symbol=f"module:{r.module_name}")
            graph.entities.append(
                EntityPayload(
                    entity_type=ENTITY_MODULE,
                    title=r.module_name,
                    file_path=module_permalink,
                    permalink=module_permalink,
                    content_type=CONTENT_TYPE_PYTHON,
                    content=f"module {r.module_name}",
                    entity_metadata={"module": r.module_name, "file": r.rel_path},
                )
            )
            graph.relations.append(
                RelationPayload(
                    from_permalink=module_permalink,
                    to_permalink=file_permalink,
                    to_name=r.rel_path,
                    relation_type=REL_DEFINED_IN,
                )
            )

            # Imports: file -> module. Target module is unresolved unless its
            # permalink matches a known file's module in this scan.
            known_modules = {r2.module_name: r2 for r2 in results}
            for imp in r.imports:
                target = known_modules.get(imp.module)
                to_permalink = (
                    code_permalink(self.project, target.rel_path, symbol=f"module:{target.module_name}")
                    if target
                    else None
                )
                graph.relations.append(
                    RelationPayload(
                        from_permalink=file_permalink,
                        to_permalink=to_permalink,
                        to_name=imp.module,
                        relation_type=REL_IMPORTS,
                        context=", ".join(imp.names) if imp.names else None,
                    )
                )

            # Symbols.
            for s in r.symbols:
                sym_permalink = code_permalink(self.project, r.rel_path, symbol=s.name)
                graph.entities.append(
                    EntityPayload(
                        entity_type=s.kind,
                        title=s.name,
                        file_path=sym_permalink,
                        permalink=sym_permalink,
                        content_type=CONTENT_TYPE_PYTHON,
                        content=s.source,
                        entity_metadata={
                            "language": r.language,
                            "module": r.module_name,
                            "qualified_name": s.qualified_name,
                            "start_line": s.start_line,
                            "end_line": s.end_line,
                            "file": r.rel_path,
                        },
                    )
                )
                # defined_in: symbol -> file.
                graph.relations.append(
                    RelationPayload(
                        from_permalink=sym_permalink,
                        to_permalink=file_permalink,
                        to_name=r.rel_path,
                        relation_type=REL_DEFINED_IN,
                    )
                )
                # calls: function -> callee (resolved same-file first, then global).
                if s.kind == ENTITY_FUNCTION:
                    for callee in s.calls:
                        local = by_file[r.rel_path].get(callee)
                        target = local or global_by_name.get(callee)
                        to_permalink = None
                        if target is not None and target is not s:
                            to_permalink = code_permalink(
                                self.project,
                                r.rel_path if target is local else _rel_path_of(global_by_name, target, results),
                                symbol=target.name,
                            )
                        graph.relations.append(
                            RelationPayload(
                                from_permalink=sym_permalink,
                                to_permalink=to_permalink,
                                to_name=callee,
                                relation_type=REL_CALLS,
                            )
                        )

        return graph


def _rel_path_of(global_by_name: Dict[str, Symbol], target: Symbol, results: List[ParseResult]) -> str:
    """Recover the rel_path for a globally-resolved symbol (first match)."""
    for r in results:
        for s in r.symbols:
            if s is target:
                return r.rel_path
    return target.qualified_name.rsplit(".", 1)[0].replace(".", "/")


# --- File scanning helper --------------------------------------------------


def iter_source_files(root: Path, *, languages: Optional[set[str]] = None) -> List[Path]:
    """Yield source files under `root` for the given languages (default: python).

    Skips common junk directories (.git, .venv, __pycache__, node_modules, build
    dirs) so an accidental broad scan doesn't explode. Returns paths sorted for
    deterministic ordering.
    """
    langs = languages or SUPPORTED_LANGUAGES
    skip_dirs = {".git", ".venv", "venv", "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache", "dist", "build"}
    out: List[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        lang = language_for_extension(path.suffix)
        if lang and lang in langs:
            out.append(path)
    return out


def parse_file(path: Path, root: Path) -> ParseResult:
    """Read + parse one source file. Raises CodeGraphError if unsupported."""
    lang = language_for_extension(path.suffix)
    if not lang:
        raise CodeGraphError(f"unsupported file type: {path.suffix!r}")
    rel = path.relative_to(root).as_posix()
    source = path.read_text(encoding="utf-8")
    return get_parser(lang).parse(source, rel_path=rel)