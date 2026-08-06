"""Provenance service — reversible distillation chain (Tb G5).

MemoPad's L0–L3 plan (`memopad-levels-implementation-plan.md` §3.2) stores each derived
memory's sources in markdown frontmatter as a `source_entities` list (e.g.
`memory://entity/carte-electrotehnica-cap3`). The file-as-source-of-truth model means
those keys flow into the `entity_metadata` JSON column at sync/write time
(see `services/entity_service.py` `create_or_update_entity`).

This service provides two things on top of that convention:

1. **Fail-fast provenance invariant.** A derived entity (level L1/L2/L3) MUST carry a
   non-empty `source_entities`. Distillation without provenance is a bug, not a
   warning — it breaks the "top symbol -> mid index -> raw text" reversibility chain
   that TbDB-Agent-Memory guarantees. Enforced only when `levels_enabled` is on,
   so existing note creation (which has no `level` field) is untouched.

2. **drill_down chain traversal.** Given a starting entity, follow `source_entities`
   (and `derived_from` relations as a secondary path) recursively down to L0,
   returning a token-budgeted chain so a caller can reach ground-truth evidence from
   any distilled abstraction.

Design notes:
- No DB migration required: `source_entities` and `level` live in `entity_metadata`
  (JSON), and `relation_type` is a free string so `derived_from` needs no schema change.
- Reading happens via repositories (get_by_permalink / get_by_title), not file I/O,
  so the traversal works uniformly across SQLite / Postgres.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from loguru import logger

from memopad.repository.entity_repository import EntityRepository
from memopad.repository.relation_repository import RelationRepository

# --- Constants ---------------------------------------------------------------

# Reversibility chain: any of these levels is a *derived* memory and must trace back
# to L0. L0 is ground truth (raw files / assets) and needs no source.
LEVEL_L0 = "L0"
DERIVED_LEVELS = {"L1", "L2", "L3"}
ALL_LEVELS = {"L0", "L1", "L2", "L3"}

# A native MemoPad relation (free string, no migration needed) that records the
# "this memory was distilled from that source" backref, mirroring the frontmatter
# `source_entities` list as a queryable graph edge.
DERIVED_FROM_RELATION_TYPE = "derived_from"

# Frontmatter / metadata keys.
LEVEL_KEY = "level"
SOURCE_ENTITIES_KEY = "source_entities"


class ProvenanceError(ValueError):
    """Raised when a derived memory lacks the provenance required by the invariant.

    Fail-fast: this is a bug in the distillation pipeline, not a recoverable state.
    """


@dataclass
class DrillDownNode:
    """One hop in a provenance chain."""

    external_id: Optional[str]
    title: str
    permalink: Optional[str]
    level: str
    file_path: Optional[str]
    resolved: bool
    # Per-hop snippet of the source content (reserved for future use; currently
    # the chain surfaces file_path so callers read bodies via read_note).
    snippet: Optional[str] = None
    via: str = "source_entities"
    # The raw source string as written in frontmatter / relation target.
    source_ref: Optional[str] = None
    children: List["DrillDownNode"] = field(default_factory=list)


# --- Validation (fail-fast invariant) ---------------------------------------


def validate_provenance(
    entity_metadata: Optional[dict], *, levels_enabled: bool
) -> None:
    """Enforce that a derived (L1/L2/L3) entity carries non-empty `source_entities`.

    No-op when `levels_enabled` is False (default) so existing flows are untouched.
    No-op for L0 and for entities without a `level` field.

    Raises:
        ProvenanceError: if `level` is in {L1, L2, L3} and `source_entities` is
            missing or empty. Distillation without provenance breaks reversibility.
    """
    if not levels_enabled or not entity_metadata:
        return

    level = entity_metadata.get(LEVEL_KEY)
    if level not in DERIVED_LEVELS:
        return  # L0 or unlevelled — no provenance required.

    sources = entity_metadata.get(SOURCE_ENTITIES_KEY)
    if not sources or not (sources if isinstance(sources, list) else [sources]):
        raise ProvenanceError(
            f"Entity with level '{level}' must declare a non-empty "
            f"'{SOURCE_ENTITIES_KEY}' (frontmatter) so the distillation chain is "
            f"reversible back to L0. Refusing to persist a derived memory without "
            f"provenance."
        )


# --- Source parsing ---------------------------------------------------------


def _strip_memory_prefix(source: str) -> str:
    """Turn a `memory://entity/<permalink>` (or bare permalink) into a permalink slug."""
    s = source.strip()
    for prefix in ("memory://entity/", "memory://"):
        if s.startswith(prefix):
            return s[len(prefix):]
    return s


def parse_source_entities(entity_metadata: Optional[dict]) -> List[str]:
    """Extract and normalize the `source_entities` list from entity metadata.

    Accepts a list (preferred) or a single string; strips `memory://` prefixes.
    Returns a list of permalink/title slugs ready for repository resolution.
    """
    if not entity_metadata:
        return []
    sources = entity_metadata.get(SOURCE_ENTITIES_KEY)
    if not sources:
        return []
    if isinstance(sources, str):
        sources = [sources]
    if not isinstance(sources, list):
        return []
    return [_strip_memory_prefix(s) for s in sources if isinstance(s, str) and s.strip()]


def entity_level(entity_metadata: Optional[dict]) -> str:
    """Return the canonical level of an entity, defaulting to L0 when unset."""
    if not entity_metadata:
        return LEVEL_L0
    level = entity_metadata.get(LEVEL_KEY)
    return level if level in ALL_LEVELS else LEVEL_L0


# --- Chain traversal (drill_down) -------------------------------------------


async def _resolve_source(
    entity_repository: EntityRepository, source_ref: str
) -> Optional[object]:
    """Resolve a source reference (permalink or title) to an Entity, or None."""
    ref = _strip_memory_prefix(source_ref)
    if not ref:
        return None
    # Try permalink first (the plan's canonical form).
    entity = await entity_repository.get_by_permalink(ref)
    if entity is not None:
        return entity
    # get_by_title returns a Sequence (multiple entities may share a title);
    # take the shortest-path first, or None when there is no match at all.
    by_title = await entity_repository.get_by_title(ref)
    if not by_title:
        return None
    return by_title[0]


async def build_drill_down_chain(
    entity_repository: EntityRepository,
    relation_repository: RelationRepository,
    start_entity: object,
    *,
    target_level: str = LEVEL_L0,
    max_depth: int = 5,
) -> DrillDownNode:
    """Build the provenance chain from `start_entity` down to `target_level` (L0).

    The chain follows, in order:
      1. frontmatter `source_entities` (authoritative — the plan's provenance field)
      2. native `derived_from` relations (secondary backref path)

    Stops recursing a branch when it reaches `target_level`, when a source cannot be
    resolved (recorded as an unresolved leaf), or at `max_depth` to bound cycles.

    Args:
        entity_repository: project-scoped entity repo (for permalink/title resolution).
        relation_repository: project-scoped relation repo (for derived_from edges).
        start_entity: the Entity model to start from.
        target_level: stop descending at this level (default L0).
        max_depth: safety bound on recursion depth (guards against cyclic provenance).

    Returns:
        DrillDownNode tree rooted at the start entity.
    """
    visited: set[int] = set()

    async def _walk(entity: object, depth: int) -> DrillDownNode:
        metadata = getattr(entity, "entity_metadata", None) or {}
        level = entity_level(metadata)
        eid = getattr(entity, "external_id", None)
        node = DrillDownNode(
            external_id=eid,
            title=getattr(entity, "title", "(untitled)"),
            permalink=getattr(entity, "permalink", None),
            level=level,
            file_path=getattr(entity, "file_path", None),
            resolved=True,
            # snippet left empty: drill_down surfaces file_path so the caller can
            # read the full source via the existing read_note tool rather than
            # embedding bodies here (keeps the response token-light).
            snippet=None,
        )

        # Stop conditions: reached target level, depth exhausted, or cycle.
        if level == target_level or depth <= 0:
            return node
        if eid is not None and getattr(entity, "id", None) is not None:
            eid_int = entity.id  # type: ignore[attr-defined]
            if eid_int in visited:
                return node
            visited.add(eid_int)

        # 1) Authoritative frontmatter source_entities.
        for ref in parse_source_entities(metadata):
            child_entity = await _resolve_source(entity_repository, ref)
            if child_entity is None:
                node.children.append(
                    DrillDownNode(
                        external_id=None,
                        title=ref,
                        permalink=ref,
                        level="?",
                        file_path=None,
                        resolved=False,
                        via="source_entities",
                        source_ref=ref,
                    )
                )
                continue
            child = await _walk(child_entity, depth - 1)
            child.via = "source_entities"
            child.source_ref = ref
            node.children.append(child)

        # 2) Secondary path: derived_from relations (from this entity -> its sources).
        #    Only followed if frontmatter didn't already provide sources, to avoid
        #    double-counting. The canonical provenance lives in frontmatter
        #    `source_entities`; relations are a queryable backref complement.
        if not node.children:
            try:
                derived_rels = await relation_repository.find_by_type(
                    DERIVED_FROM_RELATION_TYPE
                )
            except Exception:  # pragma: no cover
                derived_rels = []
            for rel in derived_rels:
                if getattr(rel, "from_id", None) != getattr(entity, "id", None):
                    continue
                to_id = getattr(rel, "to_id", None)
                if to_id is None:
                    continue
                child_entity = await entity_repository.find_by_id(to_id)
                if child_entity is None:
                    continue
                child = await _walk(child_entity, depth - 1)
                child.via = "derived_from"
                child.source_ref = getattr(rel, "to_name", None) or child.permalink
                node.children.append(child)

        return node

    return await _walk(start_entity, max_depth)


def render_drill_down_chain(root: DrillDownNode) -> str:
    """Render the provenance chain as Markdown (indented by depth)."""
    lines: list[str] = []

    def _render(node: DrillDownNode, depth: int) -> None:
        indent = "  " * depth
        resolved_marker = "" if node.resolved else " _[unresolved]_"
        via_marker = f" _[{node.via}]_" if depth > 0 and node.via == "derived_from" else ""
        permalink = node.permalink or node.title
        header = f"{indent}- `[{node.level}]` [[{permalink}|{node.title}]]{resolved_marker}{via_marker}"
        if node.file_path and node.resolved:
            header += f"  `{node.file_path}`"
        lines.append(header)
        for child in node.children:
            _render(child, depth + 1)

    _render(root, 0)
    return "\n".join(lines)