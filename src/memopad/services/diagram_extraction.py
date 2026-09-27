"""Local, review-only extraction of simple Mermaid flowchart source."""

from __future__ import annotations

import re
from typing import Literal, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field

_FLOWCHART_HEADER = re.compile(r"^(?:flowchart|graph)\s+(?:TB|TD|BT|RL|LR)\s*;?$", re.I)
_EDGE_STATEMENT = re.compile(
    r"^(?P<source>[A-Za-z][A-Za-z0-9_-]*)"
    r'(?:\s*(?:\["(?P<source_quoted>[^"]*)"\]|\[(?P<source_square>[^\]]*)\]'
    r"|\((?P<source_round>[^)]*)\)|\{(?P<source_curly>[^}]*)\}))?\s*"
    r"(?P<operator>-->|-.->|==>)\s*"
    r"(?:\|(?P<label>[^|]*)\|\s*)?"
    r"(?P<target>[A-Za-z][A-Za-z0-9_-]*)"
    r'(?:\s*(?:\["(?P<target_quoted>[^"]*)"\]|\[(?P<target_square>[^\]]*)\]'
    r"|\((?P<target_round>[^)]*)\)|\{(?P<target_curly>[^}]*)\}))?$"
)
_NODE_STATEMENT = re.compile(
    r"^(?P<id>[A-Za-z][A-Za-z0-9_-]*)\s*"
    r"(?P<open>[\[\(\{])(?P<label>.*)(?P<close>[\]\)\}])$"
)
_NODE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_NODE_CLOSE = {"[": "]", "(": ")", "{": "}"}
MAX_DIAGRAM_SOURCE_CHARS = 100_000
MAX_DIAGRAM_NODES = 250
MAX_DIAGRAM_RELATIONS = 500
MAX_DIAGRAM_WARNINGS = 100


class DiagramExtractionError(ValueError):
    """Raised when a diagram source violates format or resource limits."""


class DiagramNodeSuggestion(BaseModel):
    """A parsed diagram node; line and source reference preserve provenance."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    label: str = Field(min_length=1, max_length=500)
    source_line: int = Field(ge=1)
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainty: float = Field(ge=0.0, le=1.0)


class DiagramRelationSuggestion(BaseModel):
    """A directed relation stated by a flowchart edge."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    source_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    target_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    label: Optional[str] = Field(default=None, max_length=500)
    source_line: int = Field(ge=1)
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainty: float = Field(ge=0.0, le=1.0)


class DiagramExtraction(BaseModel):
    """Validated extraction result; suggestions are not persisted to the graph."""

    model_config = ConfigDict(extra="forbid")

    format: Literal["mermaid-flowchart"] = "mermaid-flowchart"
    source_reference: str = Field(min_length=1, max_length=4096)
    page_reference: Optional[str] = Field(default=None, max_length=256)
    nodes: list[DiagramNodeSuggestion]
    relations: list[DiagramRelationSuggestion]
    warnings: list[str]


class VisualDiagramExtractor(Protocol):
    """Contract for local diagram extractors that return provenance-bearing suggestions."""

    def extract(
        self,
        source_text: str,
        *,
        source_reference: str,
        page_reference: Optional[str] = None,
    ) -> DiagramExtraction: ...


class MermaidFlowchartExtractor:
    """Parse a deliberately small, deterministic Mermaid flowchart subset.

    Supported statements are labeled nodes (square, round, or curly forms) and
    directed edges using ``-->``, ``-.->``, or ``==>``. Unsupported
    statements are returned as line-specific warnings, not silently treated as
    graph facts. Raster images and Mermaid rendering are out of scope.
    """

    def extract(
        self,
        source_text: str,
        *,
        source_reference: str,
        page_reference: Optional[str] = None,
    ) -> DiagramExtraction:
        if not source_reference.strip():
            raise DiagramExtractionError("source_reference must not be blank")
        if len(source_text) > MAX_DIAGRAM_SOURCE_CHARS:
            raise DiagramExtractionError(
                f"diagram source exceeds {MAX_DIAGRAM_SOURCE_CHARS} characters"
            )

        source_lines = source_text.splitlines()
        header_index = next(
            (
                index
                for index, line in enumerate(source_lines)
                if line.strip() and not line.lstrip().startswith("%%")
            ),
            None,
        )
        if header_index is None or not _FLOWCHART_HEADER.fullmatch(
            source_lines[header_index].strip()
        ):
            raise DiagramExtractionError("source must begin with a Mermaid flowchart header")

        nodes: dict[str, DiagramNodeSuggestion] = {}
        relations: list[DiagramRelationSuggestion] = []
        warnings: list[str] = []

        def edge_endpoint_label(match: re.Match[str], prefix: str) -> str:
            return next(
                (
                    match.group(f"{prefix}_{shape}")
                    for shape in ("quoted", "square", "round", "curly")
                    if match.group(f"{prefix}_{shape}") is not None
                ),
                match.group(prefix),
            )

        def edge_endpoint_is_labeled(match: re.Match[str], prefix: str) -> bool:
            return any(
                match.group(f"{prefix}_{shape}") is not None
                for shape in ("quoted", "square", "round", "curly")
            )

        explicitly_labeled_nodes: set[str] = set()

        def add_node(node_id: str, label: str, line_number: int, *, explicit: bool = False) -> None:
            normalized_label = label.strip()
            if not normalized_label:
                normalized_label = node_id
            current = nodes.get(node_id)
            if current is not None:
                if not explicit:
                    return
                if node_id in explicitly_labeled_nodes and current.label != normalized_label:
                    raise DiagramExtractionError(
                        f"node {node_id!r} has conflicting labels on lines "
                        f"{current.source_line} and {line_number}"
                    )
                if node_id not in explicitly_labeled_nodes:
                    nodes[node_id] = current.model_copy(
                        update={"label": normalized_label, "source_line": line_number}
                    )
                explicitly_labeled_nodes.add(node_id)
                return
            if len(nodes) >= MAX_DIAGRAM_NODES:
                raise DiagramExtractionError(f"diagram exceeds {MAX_DIAGRAM_NODES} nodes")
            nodes[node_id] = DiagramNodeSuggestion(
                id=node_id,
                label=normalized_label,
                source_line=line_number,
                confidence=1.0,
                uncertainty=0.0,
            )
            if explicit:
                explicitly_labeled_nodes.add(node_id)

        for line_number, raw_line in enumerate(source_lines, start=1):
            if line_number <= header_index + 1:
                continue
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("%%"):
                continue
            for statement in (part.strip() for part in stripped.split(";")):
                if not statement:
                    continue
                edge_match = _EDGE_STATEMENT.fullmatch(statement)
                if edge_match:
                    source_id = edge_match.group("source")
                    target_id = edge_match.group("target")
                    add_node(
                        source_id,
                        edge_endpoint_label(edge_match, "source"),
                        line_number,
                        explicit=edge_endpoint_is_labeled(edge_match, "source"),
                    )
                    add_node(
                        target_id,
                        edge_endpoint_label(edge_match, "target"),
                        line_number,
                        explicit=edge_endpoint_is_labeled(edge_match, "target"),
                    )
                    if len(relations) >= MAX_DIAGRAM_RELATIONS:
                        raise DiagramExtractionError(
                            f"diagram exceeds {MAX_DIAGRAM_RELATIONS} relations"
                        )
                    edge_label = edge_match.group("label")
                    relations.append(
                        DiagramRelationSuggestion(
                            id=f"edge-{len(relations) + 1}",
                            source_id=source_id,
                            target_id=target_id,
                            label=edge_label.strip() if edge_label else None,
                            source_line=line_number,
                            confidence=1.0,
                            uncertainty=0.0,
                        )
                    )
                    continue

                node_match = _NODE_STATEMENT.fullmatch(statement)
                if node_match and _NODE_CLOSE[node_match.group("open")] == node_match.group(
                    "close"
                ):
                    add_node(
                        node_match.group("id"),
                        node_match.group("label"),
                        line_number,
                        explicit=True,
                    )
                    continue
                if _NODE_ID.fullmatch(statement) and statement.lower() != "end":
                    add_node(statement, statement, line_number)
                    continue
                if len(warnings) < MAX_DIAGRAM_WARNINGS:
                    warnings.append(f"line {line_number}: unsupported Mermaid statement ignored")

        return DiagramExtraction(
            source_reference=source_reference.strip(),
            page_reference=page_reference,
            nodes=list(nodes.values()),
            relations=relations,
            warnings=warnings,
        )
