"""DB-backed CodeGraph service (Tb G2).

Bridges the pure importer/builder/query logic to the repositories:

* `index_directory` — scan a source tree, parse, build the graph, and persist
  entities (via `entity_repository.upsert_entity`), relations (via
  `relation_repository`), and search-index rows (via `search_repository`) so code
  is searchable through the existing BM25+embedding pipeline. Idempotent: re-running
  on the same tree upserts (file_path is the conflict key) and replaces outgoing
  relations + search rows.
* `find_symbol` / `impact_path` / `code_context` — load a `GraphView` from the
  repositories and delegate to the pure functions in `codegraph_query`.

The whole feature is gated by ``codegraph_enabled`` (default off); the service
self-gates `index_directory` and the MCP tool checks the flag for the queries too.

Watch re-index: ``WatchService.handle_changes`` now calls a full-tree
``index_directory`` at the end of a change batch when ``codegraph_enabled`` is on
and the batch contains source files (see ``watch_service._batch_has_code_files``
+ the gated reindex block). This keeps the code graph fresh during a watch
session without a manual ``index_code`` run. The hook is best-effort and reuses
this idempotent ``index_directory``; manual ``index_code`` remains the documented
fallback when watch is off or a reindex fails (see ``get_codegraph_service``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger

from memopad import db
from memopad.importers.code_importer import (
    ENTITY_CLASS,
    ENTITY_FILE,
    ENTITY_FUNCTION,
    ENTITY_MODULE,
    REL_CALLS,
    REL_DEFINED_IN,
    REL_IMPORTS,
    CodeGraphBuilder,
    CodeGraphError,
    EntityPayload,
    RelationPayload,
    iter_source_files,
    parse_file,
)
from memopad.models import Entity, Observation, Project, Relation
from memopad.repository.entity_repository import EntityRepository
from memopad.repository.observation_repository import ObservationRepository
from memopad.repository.relation_repository import RelationRepository
from memopad.repository.search_index_row import SearchIndexRow
from memopad.repository.search_repository import SearchRepository, create_search_repository
from memopad.schemas.search import SearchItemType
from memopad.services.codegraph_query import (
    CodeContext,
    GraphView,
    ImpactPath,
    SymbolNode,
    build_view,
    code_context as _code_context,
    find_symbol as _find_symbol,
    impact_path as _impact_path,
)

# Observation category used to store a symbol's definition snippet (ground truth
# for `code_context`; the Entity itself has no content column).
DEFINITION_CATEGORY = "definition"

_SYMBOL_TYPES = (ENTITY_FUNCTION, ENTITY_CLASS, ENTITY_MODULE)

# All code-graph entity types, used by the prune step. Includes ENTITY_FILE so a
# deleted .py file's file entity is retired too (not just its symbols). These
# types never overlap with notes/skills, so pruning them never touches non-code
# entities.
_CODE_ENTITY_TYPES = (ENTITY_FILE, ENTITY_MODULE, ENTITY_FUNCTION, ENTITY_CLASS)


@dataclass
class IndexReport:
    """Summary of an `index_directory` run."""

    files: int = 0
    entities: int = 0
    relations: int = 0
    skipped: int = 0
    # Code entities (symbols/modules) removed because their source file is no
    # longer present in the scanned tree. DB-level ON DELETE CASCADE removes
    # their observations + both-direction relations; the search row is dropped
    # explicitly. Zero on a fresh index or when nothing was deleted.
    pruned: int = 0

    def render(self) -> str:
        return (
            f"Indexed {self.files} file(s): {self.entities} entities, "
            f"{self.relations} relations, {self.skipped} skipped"
            f"{f', {self.pruned} pruned' if self.pruned else ''}."
        )


class CodeGraphService:
    """Persists code into the graph and answers CodeGraph queries."""

    def __init__(
        self,
        entity_repository: EntityRepository,
        observation_repository: ObservationRepository,
        relation_repository: RelationRepository,
        search_repository: SearchRepository,
        project_id: int,
        project_name: str,
        *,
        app_config=None,
    ):
        self.entity_repository = entity_repository
        self.observation_repository = observation_repository
        self.relation_repository = relation_repository
        self.search_repository = search_repository
        self.project_id = project_id
        self.project_name = project_name
        self.app_config = app_config

    # --- indexing ----------------------------------------------------------

    async def index_directory(
        self, root: Path, *, languages: Optional[set] = None
    ) -> IndexReport:
        """Parse a source tree and upsert entities + relations + search rows.

        Gated by `codegraph_enabled`: no-op (returns empty report) when off, so
        the MCP tool can call it unconditionally without checking the flag twice.
        """
        if self.app_config is not None and not getattr(self.app_config, "codegraph_enabled", False):
            logger.info("CodeGraph disabled (codegraph_enabled=false); skipping index_directory")
            return IndexReport()

        root = Path(root)
        if not root.is_dir():
            raise CodeGraphError(f"not a directory: {root}")

        report = IndexReport()
        files = iter_source_files(root, languages=languages)
        report.files = len(files)

        results = []
        for path in files:
            try:
                results.append(parse_file(path, root))
            except CodeGraphError as e:
                logger.warning(f"skipping {path}: {e}")
                report.skipped += 1

        graph = CodeGraphBuilder(self.project_name).build(results)

        # 1) Upsert entities; remember permalink -> persisted entity for relations.
        permalink_to_entity: Dict[str, Entity] = {}
        for payload in graph.entities:
            entity = await self._upsert_entity(payload)
            permalink_to_entity[payload.permalink] = entity
            await self._index_search_row(entity, payload)
            if payload.entity_type in (ENTITY_FUNCTION, ENTITY_CLASS):
                await self._store_definition(entity, payload.content)
            report.entities += 1

        # 2) Replace outgoing relations for EVERY upserted entity, then re-add the
        #    new ones. Iterating all upserted entities (not only those that still
        #    have relations) clears stale outgoing edges for a symbol whose
        #    calls/imports all disappeared on re-index — otherwise `impact_path`
        #    would keep reporting callers/callees that no longer exist.
        relations_by_from: Dict[int, List[Relation]] = {}
        for rel_payload in graph.relations:
            from_entity = permalink_to_entity.get(rel_payload.from_permalink)
            if from_entity is None:
                continue  # relation whose source wasn't indexed — skip (fail-safe)
            to_entity = permalink_to_entity.get(rel_payload.to_permalink) if rel_payload.to_permalink else None
            relation = Relation(
                project_id=self.project_id,
                from_id=from_entity.id,
                to_id=to_entity.id if to_entity is not None else None,
                to_name=rel_payload.to_name,
                relation_type=rel_payload.relation_type,
                context=rel_payload.context,
            )
            relations_by_from.setdefault(from_entity.id, []).append(relation)

        for entity in permalink_to_entity.values():
            await self.relation_repository.delete_outgoing_relations_from_entity(entity.id)
        for from_id, rels in relations_by_from.items():
            if rels:
                inserted = await self.relation_repository.add_all_ignore_duplicates(rels)
                report.relations += inserted

        # 3) Prune code entities whose source file is no longer in the scanned
        #    tree. Without this, deleting a .py file would leave its symbol/module
        #    rows + search-index rows + definition observations (and the incoming
        #    call edges from other symbols) orphaned forever — `find_symbol` /
        #    `code_context` would surface deleted symbols. The stale set is the
        #    complement of the freshly-scanned permalinks vs the existing code
        #    symbols of this project. DB-level ON DELETE CASCADE on
        #    observation.entity_id + relation.{from,to}_id removes the children
        #    automatically once the entity row goes; the search row is a separate
        #    table with no FK, so it is dropped explicitly per entity. Order
        #    matters: step 2 already wiped every upserted caller's outgoing edges
        #    (including old edges to the now-stale symbols), so no relation still
        #    references a stale entity.id by the time we delete it.
        scanned_permalinks = {payload.permalink for payload in graph.entities}
        existing_symbols = await self.entity_repository.get_symbol_permalinks(_CODE_ENTITY_TYPES)
        stale_ids = [
            eid for eid, permalink in existing_symbols if permalink not in scanned_permalinks
        ]
        for eid in stale_ids:
            await self.search_repository.delete_by_entity_id(eid)
        if stale_ids:
            await self.entity_repository.delete_by_ids(stale_ids)
        report.pruned = len(stale_ids)

        logger.info(f"CodeGraph index: {report.render()}")
        return report

    async def _upsert_entity(self, payload: EntityPayload) -> Entity:
        entity = Entity(
            title=payload.title,
            entity_type=payload.entity_type,
            permalink=payload.permalink,
            file_path=payload.file_path,
            content_type=payload.content_type,
            entity_metadata=dict(payload.entity_metadata),
            project_id=self.project_id,
        )
        return await self.entity_repository.upsert_entity(entity)

    async def _index_search_row(self, entity: Entity, payload: EntityPayload) -> None:
        row = SearchIndexRow(
            project_id=self.project_id,
            id=entity.id,
            type=SearchItemType.ENTITY.value,
            file_path=entity.file_path,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            permalink=entity.permalink,
            title=entity.title,
            content_snippet=payload.content,
            content_stems=payload.content,
            metadata={"entity_type": entity.entity_type},
        )
        await self.search_repository.index_item(row)

    async def _store_definition(self, entity: Entity, definition: str) -> None:
        """Store the definition snippet as a `definition` observation.

        Replaces any existing definition observation for the entity first, so
        re-indexing doesn't accumulate duplicates.
        """
        existing = await self.observation_repository.find_by_entity(entity.id)
        for obs in existing:
            if obs.category == DEFINITION_CATEGORY:
                await self.observation_repository.delete(obs.id)
        await self.observation_repository.create(
            {
                "entity_id": entity.id,
                "content": definition,
                "category": DEFINITION_CATEGORY,
            }
        )

    # --- query helpers -----------------------------------------------------

    async def _load_symbol_entities(self) -> List[Entity]:
        out: List[Entity] = []
        for etype in _SYMBOL_TYPES:
            out.extend(await self.entity_repository.list_by_entity_type(etype, limit=10000, offset=0))
        return out

    def _to_symbol_node(self, entity: Entity) -> SymbolNode:
        meta = entity.entity_metadata or {}
        return SymbolNode(
            permalink=entity.permalink,
            title=entity.title,
            entity_type=entity.entity_type,
            qualified_name=meta.get("qualified_name"),
            file=meta.get("file"),
        )

    async def _load_graph_view(self) -> GraphView:
        """Load the full code graph slice (symbols + resolved calls + imports)."""
        entities = await self._load_symbol_entities()
        nodes = [self._to_symbol_node(e) for e in entities]
        by_permalink = {e.permalink: e for e in entities}

        # Calls (resolved only — to_id not null).
        # `find_by_type` is not project-scoped (it has no project_id filter), so
        # when >1 project is code-graph-indexed in the same DB it returns every
        # project's relations. Filter by `Relation.project_id` here so impact_path /
        # code_context never pull in callers/callees from a foreign project.
        calls = await self.relation_repository.find_by_type(REL_CALLS)
        call_pairs: List[Tuple[str, str]] = []
        for rel in calls:
            if rel.project_id != self.project_id:
                continue
            if rel.to_id is None or rel.from_entity is None or rel.to_entity is None:
                continue
            if rel.from_entity.permalink and rel.to_entity.permalink:
                call_pairs.append((rel.from_entity.permalink, rel.to_entity.permalink))

        # Imports + defined_in for richer context (file-level relations; resolved).
        imports = await self.relation_repository.find_by_type(REL_IMPORTS)
        import_pairs: List[Tuple[str, str]] = []
        for rel in imports:
            if rel.project_id != self.project_id:
                continue
            if rel.to_id is None or rel.from_entity is None or rel.to_entity is None:
                continue
            if rel.from_entity.permalink and rel.to_entity.permalink:
                import_pairs.append((rel.from_entity.permalink, rel.to_entity.permalink))

        defined_in = await self.relation_repository.find_by_type(REL_DEFINED_IN)
        defined_pairs: List[Tuple[str, str]] = []
        for rel in defined_in:
            if rel.project_id != self.project_id:
                continue
            if rel.from_entity is None or rel.to_entity is None:
                continue
            if rel.from_entity.permalink and rel.to_entity.permalink:
                defined_pairs.append((rel.from_entity.permalink, rel.to_entity.permalink))

        return build_view(
            nodes,
            calls=call_pairs,
            imports=import_pairs,
            defined_in=defined_pairs,
        )

    # --- queries -----------------------------------------------------------

    async def find_symbol(self, name: str, *, exact: bool = False) -> List[SymbolNode]:
        """Return symbols matching `name` (functions/classes/modules)."""
        view = await self._load_graph_view()
        return _find_symbol(view, name, exact=exact)

    async def impact_path(self, permalink: str, *, max_hops: int = 5) -> ImpactPath:
        """BFS over reverse `calls`: what does changing `permalink` affect?"""
        view = await self._load_graph_view()
        return _impact_path(view, permalink, max_hops=max_hops)

    async def _fill_context(
        self, view: GraphView, permalink: str, *, max_tokens: int = 0
    ) -> Optional[CodeContext]:
        """Build a `CodeContext` from an already-loaded view and fill its definition.

        This is the shared body of `code_context` / `render_code_context`: both
        load the view once and delegate here, so the graph is never loaded twice
        per request. Honors the `Optional` return contract — a permalink absent
        from the view yields `None` (the pure `_code_context` raises `KeyError`,
        caught here) rather than propagating an unhandled `KeyError` to a caller
        that was told the result may be `None`. The definition text is filled
        from the stored `definition` observation (the pure view has no content).
        """
        try:
            ctx = _code_context(view, permalink, max_tokens=max_tokens)
        except KeyError:
            return None
        entity = await self.entity_repository.get_by_permalink(permalink)
        if entity is not None:
            ctx.definition = await self._load_definition(entity)
        return ctx

    async def code_context(self, permalink: str, *, max_tokens: int = 0) -> Optional[CodeContext]:
        """Assemble definition + neighbors for a symbol permalink."""
        view = await self._load_graph_view()
        return await self._fill_context(view, permalink, max_tokens=max_tokens)

    async def _load_definition(self, entity: Entity) -> str:
        observations = await self.observation_repository.find_by_entity(entity.id)
        for obs in observations:
            if obs.category == DEFINITION_CATEGORY:
                return obs.content
        return ""

    # --- rendering convenience (used by MCP tools) -------------------------

    async def render_find_symbol(self, name: str, *, exact: bool = False) -> str:
        hits = await self.find_symbol(name, exact=exact)
        if not hits:
            return f"No symbols found matching '{name}'."
        lines = [f"# Symbols matching '{name}'", ""]
        for n in hits:
            q = f" — `{n.qualified_name}`" if n.qualified_name else ""
            lines.append(f"- `{n.permalink}` ({n.entity_type}){q}")
        lines.append(
            "\nUse `impact_path(permalink=...)` to see what a change affects, "
            "or `code_context(permalink=...)` for definition + dependencies."
        )
        return "\n".join(lines)

    async def render_impact_path(self, permalink: str, *, max_hops: int = 5) -> str:
        view = await self._load_graph_view()
        if permalink not in view.symbols:
            return f"No code symbol at `{permalink}`. Use `find_symbol` to discover permalinks."
        ip = await self.impact_path(permalink, max_hops=max_hops)
        return ip.render(view)

    async def render_code_context(self, permalink: str, *, max_tokens: int = 0) -> str:
        view = await self._load_graph_view()
        ctx = await self._fill_context(view, permalink, max_tokens=max_tokens)
        if ctx is None:
            return f"No code symbol at `{permalink}`. Use `find_symbol` to discover permalinks."
        return ctx.render(view, max_tokens=max_tokens)


async def get_codegraph_service(project: Project) -> CodeGraphService:  # pragma: no cover
    """Build a ``CodeGraphService`` wired to the project's DB.

    Mirror of ``sync_service.get_sync_service``: a standalone factory so the
    watch service can obtain a code-graph indexer without dragging CodeGraph
    concerns into ``SyncService.__init__`` (which would touch the functional
    sync flow). Used by ``WatchService._get_codegraph_service`` as the default
    factory when no explicit one is injected.

    Integration-only — exercised by ``memopad watch`` against a live DB, hence
    the coverage pragma matching ``get_sync_service``. The manual fallback when
    watch is off (or a reindex fails) is the ``index_code`` MCP tool / CLI,
    which builds its own service the same way.
    """
    from memopad.config import ConfigManager

    app_config = ConfigManager().config
    _, session_maker = await db.get_or_create_db(
        db_path=app_config.database_path, db_type=db.DatabaseType.FILESYSTEM
    )

    entity_repository = EntityRepository(session_maker, project_id=project.id)
    observation_repository = ObservationRepository(session_maker, project_id=project.id)
    relation_repository = RelationRepository(session_maker, project_id=project.id)
    search_repository = create_search_repository(session_maker, project_id=project.id)

    return CodeGraphService(
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        search_repository=search_repository,
        project_id=project.id,
        project_name=project.name,
        app_config=app_config,
    )