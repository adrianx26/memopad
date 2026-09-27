# Stage 5: Local Diagram Extraction

**Status:** Deterministic text-diagram slice implemented; visual image pipeline
remains open
**Source:** Phase 5 in [Implementation Roadmap](implementation-roadmap.md)

## Current local-only slice

`memopad.services.diagram_extraction` defines a `VisualDiagramExtractor`
protocol and typed, validated node/relation suggestion results. The built-in
`MermaidFlowchartExtractor` parses a bounded subset of Mermaid flowchart source
locally, without an inference runtime, network access, or additional
dependencies.

Supported sources start with `flowchart` or `graph` and one of `TB`, `TD`, `BT`,
`RL`, or `LR`. It recognizes basic node declarations and directed edges
(`-->`, `-.->`, `==>`), including inline square, round, and curly node
labels. Unsupported statements produce line-specific warnings. Every result
retains the source reference, optional page reference, and source line number.
Input is capped at 100,000 characters, 250 nodes, and 500 relations.

The output is **review-only**. It is not written to MemoPad entities and does
not create graph relations. The confidence value describes deterministic
syntax extraction, not the truth or usefulness of the diagram's contents.
Unsupported lines are explicit warnings and are not converted into inferred
relations.

## Not implemented

- No raster-image decoding, OCR, visual model, SVG geometry, or relationship
  inference from pixels is included. `read_content` image display is not an
  extraction pipeline.
- No MCP/API ingestion surface exposes these suggestions yet.
- There is no labeled diagram evaluation set, so precision/recall and any
  automation quality claim remain unmeasured.

Before implementing raster or SVG extraction, define supported image formats,
maximum byte/pixel dimensions, page-coordinate provenance, a local OCR/parser
runtime and license, and a manually labeled evaluation set. Keep source assets
as L0 and require human approval before creating any graph entities.

## Validation

The focused extractor tests cover provenance, inline labels and edges,
unsupported syntax warnings, invalid headers, conflicting labels, blank
provenance, and resource limits. Results are recorded in the implementation
roadmap after the combined Stage 4/5 validation.
