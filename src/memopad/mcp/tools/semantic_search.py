"""Semantic / hybrid search MCP tool.

Adds a new search surface alongside the existing FTS5-backed `search_notes`.
Three modes:
  - "fts":      keyword search via FTS5 BM25 (delegates to existing tool)
  - "semantic": cosine similarity over stored embeddings
  - "hybrid":   Reciprocal Rank Fusion of the two

Embeddings are opt-in. When disabled (the default), `mode="semantic"` and
`mode="hybrid"` return an explanatory error rather than silently degrading,
so the user knows to enable the feature.
"""

from typing import Optional

from fastmcp import Context
from loguru import logger

from memopad.mcp.async_client import get_client
from memopad.mcp.project_context import add_project_metadata, get_active_project
from memopad.mcp.server import mcp
from memopad.services.embedding_service import (
    EMBEDDINGS_ENABLED_ENV,
    is_enabled as embeddings_enabled,
)


@mcp.tool(
    description=(
        "Search notes using semantic similarity (embeddings) or hybrid keyword+semantic. "
        "Requires MEMOPAD_EMBEDDINGS_ENABLED=true and the [embeddings] extra installed."
    ),
)
async def semantic_search(
    query: str,
    mode: str = "hybrid",
    limit: int = 10,
    project: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Search the knowledge base using semantic similarity.

    Args:
        query: Natural-language query.
        mode: "semantic", "hybrid" (default), or "fts" — falls back to keyword search.
        limit: Maximum results to return.
        project: Project name. Optional — server resolves the default.
        context: Optional FastMCP context.

    Returns:
        Markdown list of results with similarity scores.

    Notes:
        Hybrid mode combines BM25 (keyword) and embedding (semantic) rankings via
        Reciprocal Rank Fusion (RRF). This gives robust results across both
        precise term lookups and conceptual queries without weight tuning.
    """
    mode = mode.lower().strip()
    if mode not in ("semantic", "hybrid", "fts"):
        return f"# Error\n\nUnknown mode '{mode}'. Use 'semantic', 'hybrid', or 'fts'."

    # Trigger: caller asked for a mode that needs embeddings
    # Why: prefer an explicit error over silently downgrading to FTS — we don't
    #      want users to think semantic search is working when it isn't.
    if mode in ("semantic", "hybrid") and not embeddings_enabled():
        return (
            "# Embeddings disabled\n\n"
            f"Set `{EMBEDDINGS_ENABLED_ENV}=true` and install the optional extra:\n\n"
            "```\npip install 'memopad[embeddings]'\n```\n\n"
            "Then run `memopad embeddings backfill` to backfill vectors for "
            "existing notes (only writes the embedding table — observations are "
            "not touched). Or pass `mode=\"fts\"` to use keyword search instead."
        )

    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        logger.info(
            f"MCP tool call tool=semantic_search mode={mode} "
            f"project={active_project.name} q={query!r}"
        )

        # The FTS5-only fast path goes through SearchClient like search_notes does.
        # Importing lazily avoids pulling search machinery into modules that don't
        # need it.
        from memopad.mcp.clients import SearchClient

        search_client = SearchClient(client, active_project.external_id)

        if mode == "fts":
            results_data = await search_client.search(query={"query": query}, page_size=limit)
            return add_project_metadata(
                _format_results(query, mode, results_data.results), active_project.name
            )

        # For semantic / hybrid we call the newly implemented semantic endpoint
        try:
            results_data = await search_client.semantic_search(query=query, mode=mode, limit=limit)
            return add_project_metadata(
                _format_results(query, mode, results_data.results), active_project.name
            )
        except Exception as e:
            logger.error(f"Semantic search failed: {e}")
            return f"# Error\n\nSemantic search failed: {e}"


def _format_results(query: str, mode: str, results) -> str:
    """Render a results list as markdown."""
    if not results:
        return f"# No results\n\nQuery: `{query}` (mode: {mode})"

    lines = [
        f"# Search results ({mode})",
        f"query: {query}",
        f"count: {len(results)}",
        "",
    ]
    for i, r in enumerate(results, start=1):
        title = getattr(r, "title", None) or getattr(r, "name", "(untitled)")
        permalink = getattr(r, "permalink", "") or ""
        score = getattr(r, "score", None)
        score_str = f" — score: {score:.3f}" if isinstance(score, float) else ""
        lines.append(f"{i}. [[{permalink or title}|{title}]]{score_str}")
    return "\n".join(lines)
