"""Graph analytics over the relation graph.

Three operations, all read-only and all running locally on top of NetworkX:

  1. cluster_notes — Louvain community detection. Surfaces topical clusters
     that emerge from how the user has wikilinked their notes.
  2. hub_notes     — degree centrality. The "god nodes" in graphify parlance:
     entities that act as connection hubs.
  3. find_path     — shortest path between two entities through the relation
     graph, returning the chain of notes + relation types.

Inspired by the graphify project (safishamsi/graphify), but operates on the
graph that MemoPad already maintains rather than building one from scratch.

The graph is loaded once per call. For large vaults (≫10k entities) we'd
want to cache the NetworkX object and invalidate on sync — that's a future
optimization, not a launch blocker. Most personal vaults have hundreds to
low-thousands of entities, where a fresh load takes well under a second.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import networkx as nx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from memopad import db
from memopad.models import Entity, Relation


# --- Data shapes ---


@dataclass(frozen=True)
class NodeInfo:
    """Lightweight projection of an entity for analytics output."""

    entity_id: int
    external_id: str
    title: str
    permalink: str | None


@dataclass
class Cluster:
    """One community in the relation graph."""

    cluster_id: int
    label: str  # Title of the highest-degree member — a human-readable handle
    members: list[NodeInfo]
    size: int
    internal_edges: int  # Edges fully contained in this cluster


@dataclass
class HubNode:
    """An entity ranked by total degree (incoming + outgoing relations)."""

    node: NodeInfo
    degree: int
    in_degree: int
    out_degree: int


@dataclass
class PathStep:
    """One hop along a path between entities."""

    from_node: NodeInfo
    to_node: NodeInfo
    relation_type: str


@dataclass
class GraphPath:
    """A chain of relations connecting source → target."""

    found: bool
    length: int  # Number of hops; 0 if source == target, -1 if not found
    steps: list[PathStep]


# --- Service ---


class GraphAnalyticsService:
    """Loads a project's relation graph into NetworkX and runs analyses on it.

    Always treats relations as undirected for clustering & centrality. Direction
    matters semantically (`depends_on` is not symmetric with `enables`) but for
    "what topics are connected" questions, treating the link as a connection
    is what users expect. `find_path` keeps direction in the step output even
    though traversal is undirected — so the user sees the relation_type as it
    was authored.
    """

    def __init__(self, session_maker: async_sessionmaker, project_id: int):
        self.session_maker = session_maker
        self.project_id = project_id

    # ------- graph loading -------

    async def _load_graph(self) -> tuple[nx.MultiGraph, dict[int, NodeInfo]]:
        """Fetch all entities + resolved relations for the project.

        Returns:
            (graph, id_to_node) where graph nodes are entity.id integers and
            id_to_node provides the human-readable info for each node.

        We use MultiGraph so multiple relations between the same pair of
        entities (e.g. both `depends_on` and `documented_in`) are preserved
        rather than collapsing to one edge.
        """
        async with db.scoped_session(self.session_maker) as session:
            # Entities — only this project. We need id, external_id, title,
            # permalink for the output projection.
            entity_result = await session.execute(
                select(
                    Entity.id, Entity.external_id, Entity.title, Entity.permalink
                ).where(Entity.project_id == self.project_id)
            )
            entity_rows = entity_result.all()

            # Resolved relations only — unresolved ones (to_id IS NULL) point
            # at notes that don't exist yet, so they can't appear in graph
            # analytics. Backlinks tool surfaces them separately.
            relation_result = await session.execute(
                select(
                    Relation.from_id, Relation.to_id, Relation.relation_type
                ).where(
                    Relation.project_id == self.project_id,
                    Relation.to_id.is_not(None),
                )
            )
            relation_rows = relation_result.all()

        id_to_node: dict[int, NodeInfo] = {
            row.id: NodeInfo(
                entity_id=row.id,
                external_id=row.external_id,
                title=row.title,
                permalink=row.permalink,
            )
            for row in entity_rows
        }

        graph: nx.MultiGraph = nx.MultiGraph()
        graph.add_nodes_from(id_to_node.keys())
        for row in relation_rows:
            # Skip self-loops — they aren't meaningful for clustering and
            # break some centrality interpretations.
            if row.from_id == row.to_id:
                continue
            graph.add_edge(row.from_id, row.to_id, relation_type=row.relation_type)

        logger.debug(
            f"GraphAnalytics: loaded {graph.number_of_nodes()} nodes / "
            f"{graph.number_of_edges()} edges for project_id={self.project_id}"
        )
        return graph, id_to_node

    # ------- analyses -------

    async def find_clusters(self, min_size: int = 3) -> list[Cluster]:
        """Run Louvain community detection on the relation graph.

        Args:
            min_size: Drop clusters with fewer than this many nodes. Two-node
                      clusters are usually noise (a pair of notes wikilinked
                      to each other and nothing else).

        Returns:
            Clusters sorted descending by size.
        """
        graph, id_to_node = await self._load_graph()
        if graph.number_of_edges() == 0:
            return []

        # Louvain requires a simple graph (no parallel edges). Collapse the
        # MultiGraph by merging parallel edges and using their count as weight,
        # so two notes with three different relations end up "more clustered."
        simple = nx.Graph()
        simple.add_nodes_from(graph.nodes())
        for u, v, _data in graph.edges(data=True):
            if simple.has_edge(u, v):
                simple[u][v]["weight"] += 1
            else:
                simple.add_edge(u, v, weight=1)

        # nx.community.louvain_communities is deterministic when seed is set —
        # important so the same vault produces the same clusters across runs.
        try:
            communities = nx.community.louvain_communities(simple, seed=42)
        except Exception as e:  # pragma: no cover
            # NetworkX raises on disconnected components in some versions.
            # Fall back to connected components, which is a degenerate but
            # always-correct community partition.
            logger.warning(f"Louvain failed ({e}); falling back to connected components")
            communities = list(nx.connected_components(simple))

        clusters: list[Cluster] = []
        for cid, community in enumerate(communities):
            if len(community) < min_size:
                continue

            members = [id_to_node[n] for n in community if n in id_to_node]
            # Label the cluster by the highest-degree member — gives the user
            # a recognizable handle without forcing them to invent names.
            label_node = max(community, key=lambda n: simple.degree(n))
            label = id_to_node[label_node].title if label_node in id_to_node else f"cluster {cid}"

            internal = sum(
                1
                for u, v in simple.edges()
                if u in community and v in community
            )

            clusters.append(
                Cluster(
                    cluster_id=cid,
                    label=label,
                    members=members,
                    size=len(members),
                    internal_edges=internal,
                )
            )

        clusters.sort(key=lambda c: c.size, reverse=True)
        return clusters

    async def find_hubs(self, top: int = 10) -> list[HubNode]:
        """Return the top-N entities by total degree (incoming + outgoing).

        Args:
            top: Maximum number of hubs to return.

        Returns:
            HubNodes sorted descending by degree, then by title for stable
            ordering when degrees tie.
        """
        graph, id_to_node = await self._load_graph()
        if graph.number_of_nodes() == 0:
            return []

        # Build a directed view for in/out degree breakdown. We loaded the
        # graph as MultiGraph (undirected) for clustering — for hubs the
        # direction is informative ("which notes do other notes depend on?"
        # is different from "which notes link out the most").
        async with db.scoped_session(self.session_maker) as session:
            relation_result = await session.execute(
                select(Relation.from_id, Relation.to_id).where(
                    Relation.project_id == self.project_id,
                    Relation.to_id.is_not(None),
                )
            )
            edges = relation_result.all()

        in_deg: dict[int, int] = {}
        out_deg: dict[int, int] = {}
        for row in edges:
            if row.from_id == row.to_id:
                continue
            out_deg[row.from_id] = out_deg.get(row.from_id, 0) + 1
            in_deg[row.to_id] = in_deg.get(row.to_id, 0) + 1

        hubs: list[HubNode] = []
        for entity_id, info in id_to_node.items():
            i = in_deg.get(entity_id, 0)
            o = out_deg.get(entity_id, 0)
            total = i + o
            if total == 0:
                continue
            hubs.append(HubNode(node=info, degree=total, in_degree=i, out_degree=o))

        hubs.sort(key=lambda h: (-h.degree, h.node.title))
        return hubs[:top]

    async def find_path(
        self, from_entity_id: int, to_entity_id: int, max_length: int = 6
    ) -> GraphPath:
        """Shortest path between two entities through the relation graph.

        Args:
            from_entity_id: Source entity.id (internal int, not external_id).
            to_entity_id: Target entity.id.
            max_length: Reject paths longer than this. Six is the network-
                       science "small world" threshold and keeps results
                       intuitive — anything longer is rarely useful.

        Returns:
            GraphPath. `found=False` if no path exists or it exceeds
            max_length. Hops are returned in source→target order with the
            authored relation_type for each step.
        """
        graph, id_to_node = await self._load_graph()

        if from_entity_id not in id_to_node or to_entity_id not in id_to_node:
            return GraphPath(found=False, length=-1, steps=[])

        if from_entity_id == to_entity_id:
            return GraphPath(found=True, length=0, steps=[])

        try:
            path_nodes: list[int] = nx.shortest_path(
                graph, source=from_entity_id, target=to_entity_id
            )
        except nx.NetworkXNoPath:
            return GraphPath(found=False, length=-1, steps=[])
        except nx.NodeNotFound:  # pragma: no cover
            return GraphPath(found=False, length=-1, steps=[])

        if len(path_nodes) - 1 > max_length:
            return GraphPath(found=False, length=len(path_nodes) - 1, steps=[])

        # For each consecutive pair in the path, pick the first relation type
        # connecting them. Multiple edges may exist (MultiGraph); we pick the
        # first deterministically by sorting on relation_type.
        steps: list[PathStep] = []
        for u, v in zip(path_nodes, path_nodes[1:]):
            edge_data = graph.get_edge_data(u, v) or {}
            rel_types = sorted(d.get("relation_type", "related_to") for d in edge_data.values())
            steps.append(
                PathStep(
                    from_node=id_to_node[u],
                    to_node=id_to_node[v],
                    relation_type=rel_types[0] if rel_types else "related_to",
                )
            )

        return GraphPath(found=True, length=len(steps), steps=steps)
