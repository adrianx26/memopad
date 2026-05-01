"""Tests for GraphAnalyticsService.

We exercise the service against the real DB fixtures (SQLite in-memory)
rather than mocking, so the entity ↔ relation loading is covered too.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from memopad.models import Entity, Project, Relation
from memopad.services.graph_analytics_service import (
    Cluster,
    GraphAnalyticsService,
    GraphPath,
    HubNode,
)


# --- Fixtures ---


@pytest_asyncio.fixture(scope="function")
async def graph_service(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> GraphAnalyticsService:
    """A service scoped to the standard test project."""
    return GraphAnalyticsService(session_maker, project_id=test_project.id)


@pytest_asyncio.fixture(scope="function")
async def small_graph(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
):
    """Build a 6-entity graph with two obvious clusters joined by one bridge edge.

    Topology:
        A -- B -- C       (cluster 1)
             |
             D
        E -- F            (cluster 2)
        D -- E            (bridge — keeps the graph connected)

    With min_size=3, Louvain should return one cluster (A,B,C,D) since it's
    a 4-clique-ish dense region. The bridge edge to E,F keeps them in their
    own community of 2 which gets filtered out.
    """
    async with session_maker() as session:
        entities: dict[str, Entity] = {}
        for letter in "ABCDEF":
            e = Entity(
                title=f"Note {letter}",
                permalink=f"note-{letter.lower()}",
                file_path=f"{letter.lower()}.md",
                entity_type="note",
                content_type="text/markdown",
                project_id=test_project.id,
            )
            session.add(e)
            entities[letter] = e
        await session.flush()

        # cluster 1: dense triangle plus D
        edges = [
            ("A", "B", "relates_to"),
            ("B", "C", "relates_to"),
            ("A", "C", "relates_to"),
            ("B", "D", "relates_to"),
            ("D", "A", "relates_to"),
            # cluster 2
            ("E", "F", "relates_to"),
            # bridge
            ("D", "E", "documented_in"),
        ]
        for from_l, to_l, rt in edges:
            session.add(
                Relation(
                    from_id=entities[from_l].id,
                    to_id=entities[to_l].id,
                    to_name=entities[to_l].permalink or entities[to_l].title,
                    relation_type=rt,
                    project_id=test_project.id,
                )
            )
        await session.commit()

        # Re-fetch into a new dict keyed by letter, returning IDs only so the
        # test body doesn't depend on session-bound objects.
        return {letter: e.id for letter, e in entities.items()}


# --- Loading ---


@pytest.mark.asyncio
async def test_load_graph_empty_project(graph_service):
    """A project with no entities or relations should produce an empty graph."""
    graph, info = await graph_service._load_graph()
    assert graph.number_of_nodes() == 0
    assert info == {}


@pytest.mark.asyncio
async def test_load_graph_skips_unresolved_relations(
    session_maker, test_project, graph_service
):
    """Unresolved relations (to_id IS NULL) must not appear in graph output."""
    async with session_maker() as session:
        e = Entity(
            title="Note X",
            permalink="note-x",
            file_path="x.md",
            entity_type="note",
            content_type="text/markdown",
            project_id=test_project.id,
        )
        session.add(e)
        await session.flush()
        # Unresolved: to_id is NULL, only to_name is set.
        session.add(
            Relation(
                from_id=e.id,
                to_id=None,
                to_name="future-note",
                relation_type="links_to",
                project_id=test_project.id,
            )
        )
        await session.commit()

    graph, _ = await graph_service._load_graph()
    assert graph.number_of_nodes() == 1
    assert graph.number_of_edges() == 0  # the unresolved relation must be skipped


# --- Hubs ---


@pytest.mark.asyncio
async def test_find_hubs_ranks_by_degree(graph_service, small_graph):
    """In the small_graph fixture, B and D should be among the top hubs."""
    hubs = await graph_service.find_hubs(top=10)
    assert len(hubs) > 0
    # Highest degree by construction is shared by A (3 edges), B (3), D (3).
    top_titles = {h.node.title for h in hubs[:3]}
    assert top_titles & {"Note A", "Note B", "Note D"}


@pytest.mark.asyncio
async def test_find_hubs_top_param_caps_results(graph_service, small_graph):
    hubs = await graph_service.find_hubs(top=2)
    assert len(hubs) == 2


@pytest.mark.asyncio
async def test_find_hubs_excludes_isolated_nodes(
    session_maker, test_project, graph_service
):
    """A note with no relations shouldn't show up as a hub."""
    async with session_maker() as session:
        session.add(
            Entity(
                title="Lonely",
                permalink="lonely",
                file_path="lonely.md",
                entity_type="note",
                content_type="text/markdown",
                project_id=test_project.id,
            )
        )
        await session.commit()

    hubs = await graph_service.find_hubs(top=10)
    assert all(h.node.title != "Lonely" for h in hubs)


@pytest.mark.asyncio
async def test_find_hubs_in_out_split(graph_service, small_graph):
    """in_degree + out_degree must equal the reported total degree."""
    hubs = await graph_service.find_hubs(top=10)
    for h in hubs:
        assert h.in_degree + h.out_degree == h.degree


# --- Clusters ---


@pytest.mark.asyncio
async def test_find_clusters_returns_cluster_for_dense_subgraph(
    graph_service, small_graph
):
    """The dense {A,B,C,D} subgraph should produce at least one cluster of size ≥ 3."""
    clusters = await graph_service.find_clusters(min_size=3)
    assert any(isinstance(c, Cluster) and c.size >= 3 for c in clusters)


@pytest.mark.asyncio
async def test_find_clusters_filters_small_communities(graph_service, small_graph):
    """min_size=10 should produce nothing on a 6-node graph."""
    clusters = await graph_service.find_clusters(min_size=10)
    assert clusters == []


@pytest.mark.asyncio
async def test_find_clusters_label_is_a_member_title(graph_service, small_graph):
    """Cluster labels should match one of the member titles."""
    clusters = await graph_service.find_clusters(min_size=3)
    for c in clusters:
        member_titles = {m.title for m in c.members}
        assert c.label in member_titles


@pytest.mark.asyncio
async def test_find_clusters_empty_graph_returns_empty(graph_service):
    clusters = await graph_service.find_clusters(min_size=2)
    assert clusters == []


# --- Paths ---


@pytest.mark.asyncio
async def test_find_path_direct_neighbor(graph_service, small_graph):
    """A→B is a direct edge → path length 1."""
    result = await graph_service.find_path(small_graph["A"], small_graph["B"])
    assert isinstance(result, GraphPath)
    assert result.found is True
    assert result.length == 1
    assert result.steps[0].from_node.title == "Note A"
    assert result.steps[0].to_node.title == "Note B"


@pytest.mark.asyncio
async def test_find_path_through_bridge(graph_service, small_graph):
    """A → ... → F crosses the D-E bridge."""
    result = await graph_service.find_path(small_graph["A"], small_graph["F"])
    assert result.found is True
    # Shortest path: A→D→E→F (3 hops) or via B/C, but at most 4.
    assert 1 <= result.length <= 4
    titles = [result.steps[0].from_node.title] + [s.to_node.title for s in result.steps]
    assert titles[0] == "Note A"
    assert titles[-1] == "Note F"


@pytest.mark.asyncio
async def test_find_path_same_node_is_zero_length(graph_service, small_graph):
    result = await graph_service.find_path(small_graph["A"], small_graph["A"])
    assert result.found is True
    assert result.length == 0
    assert result.steps == []


@pytest.mark.asyncio
async def test_find_path_unknown_id_returns_not_found(graph_service, small_graph):
    """Querying with an entity id that doesn't exist should fail gracefully."""
    result = await graph_service.find_path(99999, small_graph["A"])
    assert result.found is False


@pytest.mark.asyncio
async def test_find_path_max_length_rejects_long_paths(
    session_maker, test_project, graph_service
):
    """If the actual shortest path > max_length, return found=False with the
    real length so the caller can decide whether to retry with a higher cap."""
    # Build a 5-node chain: 1 → 2 → 3 → 4 → 5.
    async with session_maker() as session:
        chain = []
        for i in range(5):
            e = Entity(
                title=f"Chain {i}",
                permalink=f"chain-{i}",
                file_path=f"chain-{i}.md",
                entity_type="note",
                content_type="text/markdown",
                project_id=test_project.id,
            )
            session.add(e)
            chain.append(e)
        await session.flush()
        for a, b in zip(chain, chain[1:]):
            session.add(
                Relation(
                    from_id=a.id,
                    to_id=b.id,
                    to_name=b.permalink,
                    relation_type="next",
                    project_id=test_project.id,
                )
            )
        await session.commit()
        ids = [c.id for c in chain]

    # Path from 0 to 4 is 4 hops; rejecting at max_length=2 should report
    # found=False but length=4 so the caller knows the truth.
    result = await graph_service.find_path(ids[0], ids[4], max_length=2)
    assert result.found is False
    assert result.length == 4
