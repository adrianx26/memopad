"""Graph analytics MCP tools.

Three read-only operations over the relation graph:
  - cluster_notes: Louvain community detection — what topics is the user thinking about?
  - hub_notes:     degree-centrality ranking — which notes are connection hubs?
  - find_path:     shortest path between two notes — how are X and Y related?

Inspired by the graphify project (safishamsi/graphify), but operates on
MemoPad's existing wikilink-derived relation graph.
"""

from typing import Optional

from fastmcp import Context
from loguru import logger

from memopad.mcp.async_client import get_client
from memopad.mcp.project_context import add_project_metadata, get_active_project
from memopad.mcp.server import mcp


# --- cluster_notes ---


@mcp.tool(
    description=(
        "Detect topical clusters in the relation graph using Louvain community detection. "
        "Surfaces what topics the user is actually thinking about, derived from how they've "
        "wikilinked their notes."
    ),
)
async def cluster_notes(
    min_size: int = 3,
    project: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Detect topic clusters in the project's relation graph.

    Args:
        min_size: Drop clusters with fewer than this many nodes (default 3 —
                  two-node clusters are usually noise).
        project: Project name. Optional — server resolves the default.
        context: Optional FastMCP context.

    Returns:
        Markdown-formatted cluster report. Each cluster is labeled by its
        highest-degree member's title.
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        logger.info(
            f"cluster_notes: project={active_project.name} min_size={min_size}"
        )

        from memopad.mcp.clients import GraphAnalyticsClient

        graph_client = GraphAnalyticsClient(client, active_project.external_id)
        data = await graph_client.get_clusters(min_size=min_size)

        clusters = data.get("clusters", [])
        if not clusters:
            return add_project_metadata(
                f"# Topic clusters in {active_project.name}\n\n"
                f"_No clusters of size ≥ {min_size} found. "
                "Either the vault is small, or notes aren't linked enough yet._",
                active_project.name,
            )

        lines = [
            f"# Topic clusters in {active_project.name}",
            f"_Found {len(clusters)} cluster(s) with ≥ {min_size} members_",
            "",
        ]
        for c in clusters:
            lines.append(
                f"## {c['label']} ({c['size']} notes, {c['internal_edges']} internal links)"
            )
            for member in c["members"][:25]:
                permalink = member.get("permalink") or member["title"]
                lines.append(f"- [[{permalink}|{member['title']}]]")
            if c["size"] > 25:
                lines.append(f"- _... and {c['size'] - 25} more_")
            lines.append("")

        return add_project_metadata("\n".join(lines), active_project.name)


# --- hub_notes ---


@mcp.tool(
    description=(
        "List the top-N entities by total degree (incoming + outgoing relations). "
        "These are the connection hubs of the knowledge graph — the notes most other "
        "notes link to or from."
    ),
)
async def hub_notes(
    top: int = 10,
    project: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Find the most-connected entities in the project.

    Args:
        top: Number of hubs to return.
        project: Project name. Optional — server resolves the default.
        context: Optional FastMCP context.

    Returns:
        Markdown-formatted ranking table.
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        logger.info(f"hub_notes: project={active_project.name} top={top}")

        from memopad.mcp.clients import GraphAnalyticsClient

        graph_client = GraphAnalyticsClient(client, active_project.external_id)
        data = await graph_client.get_hubs(top=top)

        hubs = data.get("hubs", [])
        if not hubs:
            return add_project_metadata(
                f"# Hub notes in {active_project.name}\n\n"
                "_No connections found. Add some [[wikilinks]] between notes._",
                active_project.name,
            )

        lines = [
            f"# Hub notes in {active_project.name}",
            f"_Top {len(hubs)} by total degree_",
            "",
            "| # | Note | Total | In | Out |",
            "|---|------|------:|---:|----:|",
        ]
        for i, h in enumerate(hubs, start=1):
            node = h["node"]
            permalink = node.get("permalink") or node["title"]
            lines.append(
                f"| {i} | [[{permalink}\\|{node['title']}]] | "
                f"{h['degree']} | {h['in_degree']} | {h['out_degree']} |"
            )

        return add_project_metadata("\n".join(lines), active_project.name)


# --- find_path ---


@mcp.tool(
    description=(
        "Find the shortest path between two notes through the relation graph. "
        "Answers questions like 'how is X connected to Y?'."
    ),
)
async def find_path(
    from_identifier: str,
    to_identifier: str,
    max_length: int = 6,
    project: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Find the shortest chain of relations connecting two notes.

    Args:
        from_identifier: Source note (permalink, title, or external_id).
        to_identifier: Target note (permalink, title, or external_id).
        max_length: Maximum hops to consider (default 6 — the small-world
                    threshold; longer paths are rarely useful).
        project: Project name. Optional — server resolves the default.
        context: Optional FastMCP context.

    Returns:
        Markdown showing the chain of notes + relation types from source
        to target, or a "no path" message.
    """
    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        logger.info(
            f"find_path: project={active_project.name} "
            f"from={from_identifier} to={to_identifier} max_length={max_length}"
        )

        from memopad.mcp.clients import GraphAnalyticsClient, KnowledgeClient

        # Resolve human identifiers (permalink/title) to external_ids first.
        # The graph endpoint takes external_ids so it doesn't need to know
        # about MemoPad's identifier resolution rules.
        knowledge_client = KnowledgeClient(client, active_project.external_id)
        try:
            from_external_id = await knowledge_client.resolve_entity(from_identifier)
        except Exception as e:
            return f"# Error\n\nCould not resolve source '{from_identifier}': {e}"
        try:
            to_external_id = await knowledge_client.resolve_entity(to_identifier)
        except Exception as e:
            return f"# Error\n\nCould not resolve target '{to_identifier}': {e}"

        graph_client = GraphAnalyticsClient(client, active_project.external_id)
        data = await graph_client.get_path(
            from_external_id, to_external_id, max_length=max_length
        )

        if not data.get("found"):
            length = data.get("length", -1)
            if length > max_length:
                msg = (
                    f"A path exists but is longer than max_length={max_length} "
                    f"(actual length: {length}). Increase max_length to see it."
                )
            else:
                msg = (
                    "No path connects these notes through the relation graph. "
                    "They may be in disconnected parts of the knowledge graph."
                )
            return add_project_metadata(
                f"# No path from '{from_identifier}' to '{to_identifier}'\n\n_{msg}_",
                active_project.name,
            )

        steps = data.get("steps", [])
        if not steps:
            # length=0 case — caller asked for a path from a note to itself.
            return add_project_metadata(
                f"# Same note\n\n'{from_identifier}' and '{to_identifier}' resolve "
                "to the same entity.",
                active_project.name,
            )

        lines = [
            f"# Path: '{from_identifier}' → '{to_identifier}'",
            f"_{len(steps)} hop(s)_",
            "",
        ]
        # First node — only the source side of step 0.
        first = steps[0]["from_node"]
        first_link = first.get("permalink") or first["title"]
        lines.append(f"- [[{first_link}|{first['title']}]]")
        for step in steps:
            to_node = step["to_node"]
            to_link = to_node.get("permalink") or to_node["title"]
            lines.append(f"  - _via_ `{step['relation_type']}`")
            lines.append(f"- [[{to_link}|{to_node['title']}]]")

        return add_project_metadata("\n".join(lines), active_project.name)
