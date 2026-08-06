"""Pure graph-query logic for CodeGraph (Tb G2).

The DB-backed `CodeGraphService` loads a small adjacency view from the
repositories and hands it to these functions, which are pure and deterministic —
so the impact analysis (the part that has to be correct) is tested without a
database.

Three queries:

* `find_symbol` — name match over symbol entities (function/class/module).
* `impact_path` — BFS over the **reverse** `calls` graph: who depends on X,
  directly or transitively. This is the "if I change X, what breaks?" answer.
  `impacts` is derived here from `calls`, never stored, so it cannot drift.
* `code_context` — a symbol's definition plus its direct dependencies (imports +
  callees) and direct callers, token-budgeted for injection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set

from memopad.importers.code_importer import (
    ENTITY_CLASS,
    ENTITY_FUNCTION,
    ENTITY_MODULE,
    REL_CALLS,
    REL_DEFINED_IN,
    REL_IMPORTS,
)


@dataclass
class SymbolNode:
    """A minimal symbol view for query logic (permalink + display name + kind)."""

    permalink: str
    title: str
    entity_type: str
    qualified_name: Optional[str] = None
    file: Optional[str] = None


@dataclass
class GraphView:
    """An in-memory slice of the code graph the pure functions operate on.

    `calls_forward` maps caller permalink -> [callee permalink] (only resolved
    relations; unresolved `calls` are omitted — their impact is unknown). The
    reverse adjacency is derived on demand.
    """

    symbols: Dict[str, SymbolNode] = field(default_factory=dict)
    calls_forward: Dict[str, List[str]] = field(default_factory=dict)
    # per-symbol direct imports (module permalinks) and defined_in file
    imports: Dict[str, List[str]] = field(default_factory=dict)
    defined_in: Dict[str, str] = field(default_factory=dict)

    def add_symbol(self, node: SymbolNode) -> None:
        self.symbols[node.permalink] = node

    def add_call(self, caller: str, callee: str) -> None:
        self.calls_forward.setdefault(caller, []).append(callee)

    def add_import(self, symbol: str, module: str) -> None:
        self.imports.setdefault(symbol, []).append(module)

    def set_defined_in(self, symbol: str, file_permalink: str) -> None:
        self.defined_in[symbol] = file_permalink

    def reverse_calls(self) -> Dict[str, List[str]]:
        """callee -> [callers]. Built once per query; cheap for small graphs."""
        rev: Dict[str, List[str]] = {}
        for caller, callees in self.calls_forward.items():
            for callee in callees:
                rev.setdefault(callee, []).append(caller)
        return rev


# --- find_symbol -----------------------------------------------------------


def find_symbol(view: GraphView, name: str, *, exact: bool = False) -> List[SymbolNode]:
    """Return symbols whose title matches `name` (case-insensitive).

    Functions, classes, and modules are searchable; file entities are excluded
    (use `list_directory` for files). `exact` requires an exact title match.
    """
    needle = name.lower()
    out: List[SymbolNode] = []
    for node in view.symbols.values():
        if node.entity_type not in (ENTITY_FUNCTION, ENTITY_CLASS, ENTITY_MODULE):
            continue
        title = node.title.lower()
        if (title == needle) if exact else (needle in title):
            out.append(node)
    # Stable order: functions, then classes, then modules; alphabetical within.
    rank = {ENTITY_FUNCTION: 0, ENTITY_CLASS: 1, ENTITY_MODULE: 2}
    out.sort(key=lambda n: (rank.get(n.entity_type, 9), n.title.lower(), n.permalink))
    return out


# --- impact_path (BFS over reverse calls) ----------------------------------


@dataclass
class ImpactPath:
    """The result of an impact analysis: reachable dependents per hop."""

    root: str
    # dependent permalink -> distance (hops) from the root
    distances: Dict[str, int] = field(default_factory=dict)
    # dependent permalink -> the direct caller that led to it (for reconstruction)
    predecessors: Dict[str, Optional[str]] = field(default_factory=dict)

    @property
    def impacted(self) -> List[str]:
        return sorted(self.distances, key=lambda p: (self.distances[p], p))

    def render(self, view: GraphView) -> str:
        """Render the impact set as markdown (grouped by hop distance)."""
        if not self.distances:
            return f"No dependents found for `{self.root}` (changing it has no known impact)."
        by_hop: Dict[int, List[str]] = {}
        for permalink, dist in self.distances.items():
            by_hop.setdefault(dist, []).append(permalink)
        lines = [f"# Impact path for `{self.root}`", ""]
        for hop in sorted(by_hop):
            label = "direct callers" if hop == 1 else f"{hop}-hop transitive"
            lines.append(f"**{label}** ({len(by_hop[hop])}):")
            for permalink in sorted(by_hop[hop]):
                node = view.symbols.get(permalink)
                name = node.title if node else permalink
                lines.append(f"- `{permalink}` — {name}")
            lines.append("")
        return "\n".join(lines).rstrip()


def impact_path(view: GraphView, root: str, *, max_hops: int = 5) -> ImpactPath:
    """BFS over the reverse `calls` graph from `root`.

    Returns every symbol that (transitively) calls `root`, with the hop distance.
    `root` itself is excluded from the result. `max_hops` bounds the walk so a
    cyclic graph can't loop forever — cycles are also guarded with a visited set.
    """
    rev = view.reverse_calls()
    result = ImpactPath(root=root)
    visited: Set[str] = {root}
    frontier: List[str] = [root]
    for hop in range(1, max_hops + 1):
        next_frontier: List[str] = []
        for node in frontier:
            for caller in rev.get(node, []):
                if caller in visited:
                    continue
                visited.add(caller)
                result.distances[caller] = hop
                result.predecessors[caller] = node
                next_frontier.append(caller)
        if not next_frontier:
            break
        frontier = next_frontier
    return result


# --- code_context ----------------------------------------------------------


@dataclass
class CodeContext:
    """A symbol's definition + direct dependencies + direct callers."""

    symbol: SymbolNode
    definition: str
    defined_in: Optional[str]
    imports: List[str] = field(default_factory=list)
    callees: List[str] = field(default_factory=list)
    callers: List[str] = field(default_factory=list)

    def render(self, view: GraphView, *, max_tokens: int = 0) -> str:
        """Render the context as markdown, optionally token-budgeted.

        `max_tokens <= 0` means unlimited. Token estimate is the crude len//4.
        """
        lines = [
            f"# Code context: `{self.symbol.title}` ({self.symbol.entity_type})",
            f"**Permalink:** `{self.symbol.permalink}`",
        ]
        if self.symbol.qualified_name:
            lines.append(f"**Qualified:** `{self.symbol.qualified_name}`")
        if self.defined_in:
            lines.append(f"**Defined in:** `{self.defined_in}`")

        def _label(permalink: str) -> str:
            node = view.symbols.get(permalink)
            return f"`{permalink}` — {node.title}" if node else f"`{permalink}`"

        if self.callers:
            lines.append("\n**Called by:**")
            for c in self.callers:
                lines.append(f"- {_label(c)}")
        else:
            lines.append("\n*No direct callers.*")

        if self.callees:
            lines.append("\n**Calls:**")
            for c in self.callees:
                lines.append(f"- {_label(c)}")

        if self.imports:
            lines.append("\n**Imports:**")
            for imp in self.imports:
                lines.append(f"- {_label(imp)}")

        lines.append("\n**Definition:**")
        lines.append("```python")
        lines.append(self.definition)
        lines.append("```")

        rendered = "\n".join(lines)
        if max_tokens > 0:
            budget_chars = max_tokens * 4
            if len(rendered) > budget_chars:
                # Clamp the slice start at 0: for very small budgets (max_tokens
                # 1-4 → budget_chars 4-16) `budget_chars - 20` is negative, and a
                # negative slice would cut from the *end* of the string, garbling
                # the output and appending the truncation marker to a tail. Taking
                # a clean (possibly empty) prefix keeps the marker meaningful.
                cut = max(0, budget_chars - 20)
                rendered = rendered[:cut] + "\n…[truncated]\n```"
        return rendered


def code_context(view: GraphView, permalink: str, *, max_tokens: int = 0) -> CodeContext:
    """Assemble the context for a symbol permalink."""
    node = view.symbols.get(permalink)
    if node is None:
        raise KeyError(f"no symbol for permalink {permalink!r}")

    callees = list(view.calls_forward.get(permalink, []))
    callers = list(view.reverse_calls().get(permalink, []))
    imports = list(view.imports.get(permalink, []))
    defined_in = view.defined_in.get(permalink)

    # The definition text isn't in the pure view (it lives on the entity); the
    # service fills it in. Here we leave it empty so the pure function has no
    # dependency on stored content.
    return CodeContext(
        symbol=node,
        definition="",
        defined_in=defined_in,
        imports=imports,
        callees=callees,
        callers=callers,
    )


def build_view(
    symbols: Iterable[SymbolNode],
    *,
    calls: Iterable[tuple[str, str]] = (),
    imports: Iterable[tuple[str, str]] = (),
    defined_in: Iterable[tuple[str, str]] = (),
) -> GraphView:
    """Convenience constructor for tests and for the service's load step."""
    view = GraphView()
    for s in symbols:
        view.add_symbol(s)
    for caller, callee in calls:
        view.add_call(caller, callee)
    for sym, mod in imports:
        view.add_import(sym, mod)
    for sym, file_permalink in defined_in:
        view.set_defined_in(sym, file_permalink)
    return view