"""Tests for the local, review-only Mermaid flowchart extractor."""

from __future__ import annotations

import pytest

from memopad.services.diagram_extraction import (
    DiagramExtractionError,
    MermaidFlowchartExtractor,
    MAX_DIAGRAM_NODES,
    MAX_DIAGRAM_SOURCE_CHARS,
)


def test_extracts_nodes_edges_and_source_provenance():
    result = MermaidFlowchartExtractor().extract(
        '%% authored workflow\nflowchart TD\nA["Start"] -->|yes| B{Ready?}\nB --> C(Complete)',
        source_reference="memory://entity/workflow",
        page_reference="page-2",
    )

    assert result.format == "mermaid-flowchart"
    assert result.source_reference == "memory://entity/workflow"
    assert result.page_reference == "page-2"
    assert [(node.id, node.label) for node in result.nodes] == [
        ("A", "Start"),
        ("B", "Ready?"),
        ("C", "Complete"),
    ]
    assert [(edge.source_id, edge.target_id, edge.label) for edge in result.relations] == [
        ("A", "B", "yes"),
        ("B", "C", None),
    ]
    assert all(node.confidence == 1.0 and node.uncertainty == 0.0 for node in result.nodes)
    assert all(edge.source_line > 0 for edge in result.relations)


def test_extracts_semicolon_statements_and_warns_on_unsupported_lines():
    result = MermaidFlowchartExtractor().extract(
        "graph LR\nA[Alpha]; A --> B[Beta]\nsubgraph group\nend",
        source_reference="docs/flow.mmd",
    )

    assert [node.id for node in result.nodes] == ["A", "B"]
    assert len(result.relations) == 1
    assert result.warnings == [
        "line 3: unsupported Mermaid statement ignored",
        "line 4: unsupported Mermaid statement ignored",
    ]


def test_warns_instead_of_assigning_direction_to_undirected_edges():
    result = MermaidFlowchartExtractor().extract(
        "flowchart LR\nA --- B",
        source_reference="diagram.mmd",
    )

    assert result.relations == []
    assert result.warnings == ["line 2: unsupported Mermaid statement ignored"]


@pytest.mark.parametrize("source", ["", "A --> B", "flowchart sideways\nA --> B"])
def test_rejects_sources_without_supported_flowchart_header(source: str):
    with pytest.raises(DiagramExtractionError, match="flowchart header"):
        MermaidFlowchartExtractor().extract(source, source_reference="diagram.mmd")


def test_rejects_conflicting_node_labels():
    with pytest.raises(DiagramExtractionError, match="conflicting labels"):
        MermaidFlowchartExtractor().extract(
            "flowchart TD\nA[First]\nA[Second]",
            source_reference="diagram.mmd",
        )


def test_rejects_blank_provenance_and_oversized_source():
    extractor = MermaidFlowchartExtractor()
    with pytest.raises(DiagramExtractionError, match="source_reference"):
        extractor.extract("flowchart TD", source_reference=" ")
    with pytest.raises(DiagramExtractionError, match="exceeds"):
        extractor.extract(
            "flowchart TD\n" + (" " * MAX_DIAGRAM_SOURCE_CHARS),
            source_reference="diagram.mmd",
        )


def test_enforces_node_count_limit():
    source = "flowchart TD\n" + "\n".join(f"N{i}[Node {i}]" for i in range(MAX_DIAGRAM_NODES + 1))

    with pytest.raises(DiagramExtractionError, match="exceeds 250 nodes"):
        MermaidFlowchartExtractor().extract(source, source_reference="large.mmd")
