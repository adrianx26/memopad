# MemGraphRAG-Inspired Improvements to MemoPad

Implements four data-quality upgrades inspired by the MemGraphRAG (KDD 2026) framework,
addressing the three core GraphRAG failure modes — **noise**, **contradictions**, and
**fragmentation** — while preserving MemoPad's local-first, file-as-source-of-truth design.

---

## User Review Required

> [!IMPORTANT]
> **Backward Compatibility:** All database changes are additive (new tables + nullable columns).
> No existing markdown files or entity data will be altered.
> Migrations are reversible with `downgrade()`.

> [!WARNING]
> **Phase 3 (EntityAlias) is the riskiest change.** Fuzzy alias resolution in `link_resolver.py`
> touches the critical path for all WikiLink `[[Target]]` lookups. It must be behind a
> `strict=False` guard (already exists) and must never break existing exact-match resolution.

> [!NOTE]
> **No LLM calls added.** All four phases are rule-based or DB-query-based.
> Zero additional API cost per operation.

---

## Open Questions

1. **Conflict threshold** — For Phase 1, should the conflict trigger be purely category-based
   (same entity + same category = potential conflict) or also require semantic distance
   (needs embeddings)? The plan below defaults to category-based (no embeddings required)
   with an opt-in embedding path when `MEMOPAD_EMBEDDINGS_ENABLED=true`.

2. **Schema normalization strictness** — For Phase 2, should unknown categories be **silently
   created** in the registry or **flagged** to the LLM in tool output? Plan defaults to
   silent creation + frequency tracking, with the flag appearing only in `list_observation_schemas`.

3. **Alias source** — For Phase 3, should aliases only come from frontmatter (`aliases:` key)
   or also be auto-generated from title variants (e.g., "Isaac Newton" → alias "Newton")?
   Plan defaults to **frontmatter only** (explicit, safe). Auto-generation is a future option.

4. **Phase order** — Do you want all four phases in one PR or shipped incrementally (one phase per PR)?
   Plan assumes **incremental** — each phase is independently mergeable.

---

## Proposed Changes

---

### Phase 1 — Observation Conflict Detection

**Goal:** When the same entity has two observations in the same `[category]` with
meaningfully different content, mark them as conflicting so the LLM can see and resolve the conflict.

**What triggers a conflict:** Two `Observation` rows on the same `entity_id` with the same
`category` but content that differs by more than a whitespace-normalised string comparison.
If embeddings are enabled, also flag when cosine similarity < 0.85.

---

#### [NEW] Migration: `add_observation_conflict_fields`

File: `src/memopad/alembic/versions/<hash>_add_observation_conflict_fields.py`

```python
# New nullable columns on `observation` table
conflict_score: Float nullable       # 0.0–1.0; higher = more conflicting
conflicting_obs_id: Integer nullable # FK → observation.id (SET NULL on delete)
conflict_resolved: Boolean default False
provenance_path: String nullable     # file_path of originating source
```

---

#### [MODIFY] [knowledge.py](file:///c:/ANTI/memopad/src/memopad/models/knowledge.py)

Add four fields to the `Observation` model:

```python
conflict_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
conflicting_obs_id: Mapped[Optional[int]] = mapped_column(
    Integer, ForeignKey("observation.id", ondelete="SET NULL"), nullable=True
)
conflict_resolved: Mapped[bool] = mapped_column(Boolean, default=False)
provenance_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
```

Also add a self-referential relationship:

```python
conflicting_observation = relationship(
    "Observation", foreign_keys=[conflicting_obs_id], remote_side="Observation.id"
)
```

---

#### [NEW] `conflict_service.py`

File: `src/memopad/services/conflict_service.py`

Responsibilities:
- `detect_observation_conflicts(entity_id, new_obs: Observation, session) → list[ConflictResult]`
  - Fetches all existing observations for `entity_id` in the same `category`
  - Compares content (string distance + optional embedding cosine)
  - Returns `ConflictResult(obs_a_id, obs_b_id, score)`
- `mark_conflicts(conflicts: list[ConflictResult], session)` — writes `conflict_score` + `conflicting_obs_id`
- `resolve_conflict(obs_id: int, session)` — sets `conflict_resolved=True`, clears partner's flag

---

#### [MODIFY] [entity_service.py](file:///c:/ANTI/memopad/src/memopad/services/entity_service.py)

In `update_entity_and_observations()` (line ~694), after `await self.observation_repository.add_all(observations)`:

```python
# --- Conflict Detection ---
# Trigger: entity already had observations; new write may introduce contradictions
# Why: catch "active" vs "deprecated" conflicts before they corrupt LLM context
# Outcome: conflicting pairs are flagged in DB; surfaced via read_note/build_context
if self.conflict_service:
    for obs in observations:
        conflicts = await self.conflict_service.detect_observation_conflicts(
            db_entity.id, obs, session
        )
        if conflicts:
            await self.conflict_service.mark_conflicts(conflicts, session)
```

Add `conflict_service: Optional[ConflictService] = None` to `__init__` signature.

---

#### [MODIFY] [context_service.py](file:///c:/ANTI/memopad/src/memopad/services/context_service.py)

In `build_context()`, when building `ContextResultRow` for observations (line ~188),
add `conflict_score` and `conflict_resolved` to the returned data so MCP tool output
can surface conflicts.

---

#### [MODIFY] MCP tool — [build_context.py](file:///c:/ANTI/memopad/src/memopad/mcp/tools/build_context.py)

In the formatted output, add a `⚠️ CONFLICT` marker next to observations where
`conflict_score > 0.5` and `conflict_resolved = False`. Include the conflicting
observation content inline so the LLM can compare and resolve.

---

#### [MODIFY] MCP tool — [read_note.py](file:///c:/ANTI/memopad/src/memopad/mcp/tools/read_note.py)

Same conflict marker in the note body output.

---

#### [NEW] `conflict_service.py` Unit Tests

File: `tests/services/test_conflict_service.py`

- `test_no_conflict_different_category` — same entity, different category → no flag
- `test_conflict_same_category_different_content` — "active" vs "deprecated" → flagged
- `test_no_conflict_same_category_same_content` — identical observations → no flag
- `test_resolve_conflict` — marks resolved, clears partner flag
- `test_conflict_cascade_delete` — deleting flagged obs clears partner's `conflicting_obs_id`

---

### Phase 2 — ObservationSchema Registry (Noise Gate)

**Goal:** Track all `[category]` labels used per project. Normalize aliases to canonical
names on write. Surface unknown/low-frequency categories so the LLM can prune noise.

---

#### [NEW] Migration: `add_observation_schema_table`

File: `src/memopad/alembic/versions/<hash>_add_observation_schema_table.py`

```sql
CREATE TABLE observation_schema (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    name        VARCHAR NOT NULL,           -- canonical category name (e.g. "status")
    aliases     JSON NOT NULL DEFAULT '[]', -- e.g. ["Status", "state", "STATUS"]
    frequency   INTEGER NOT NULL DEFAULT 1, -- times this category has been used
    created_at  DATETIME NOT NULL,
    updated_at  DATETIME NOT NULL,
    UNIQUE (project_id, name)
);
CREATE INDEX ix_obs_schema_project ON observation_schema (project_id);
```

---

#### [NEW] `observation_schema.py` (model)

File: `src/memopad/models/observation_schema.py`

```python
class ObservationSchema(Base):
    __tablename__ = "observation_schema"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("project.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    frequency: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = ...
    updated_at: Mapped[datetime] = ...
```

---

#### [NEW] `observation_schema_repository.py`

File: `src/memopad/repository/observation_schema_repository.py`

Key methods:
- `find_by_project(project_id)` → all schemas for a project
- `find_by_name_or_alias(project_id, category)` → canonical schema or None
- `upsert_schema(project_id, name)` → create or increment frequency
- `add_alias(schema_id, alias)` → register a new alias

---

#### [NEW] `schema_service.py`

File: `src/memopad/services/schema_service.py`

Key methods:
- `normalize_category(project_id, raw_category) → str` — looks up alias table;
  returns canonical name if found, otherwise registers raw_category as new schema
- `get_schemas(project_id) → list[ObservationSchema]` — for MCP tool
- `suggest_consolidation(project_id) → list[ConsolidationSuggestion]` — finds schemas
  with frequency=1 that are likely typos/variants of existing high-frequency schemas

---

#### [MODIFY] [entity_service.py](file:///c:/ANTI/memopad/src/memopad/services/entity_service.py)

In `update_entity_and_observations()`, before building the `Observation` objects,
normalize each `obs.category` via `SchemaService.normalize_category()`:

```python
if self.schema_service:
    obs.category = await self.schema_service.normalize_category(
        self.observation_repository.project_id, obs.category
    )
```

Add `schema_service: Optional[SchemaService] = None` to `__init__`.

---

#### [NEW] MCP Tool — `list_observation_schemas`

File: `src/memopad/mcp/tools/list_observation_schemas.py`

Returns a table of all canonical categories for the current project, sorted by frequency
descending. Flags schemas with `frequency == 1` as potential noise. Output format:

```
## Observation Schemas (project: my-project)

| Category     | Frequency | Aliases        | Status   |
|------------- |-----------|----------------|----------|
| status       | 47        | Status, state  | ✅ stable|
| tech         | 23        |                | ✅ stable|
| tmp-note     | 1         |                | ⚠️ rare  |
```

Register in `src/memopad/mcp/tools/__init__.py`.

---

#### Tests

File: `tests/services/test_schema_service.py`

- `test_normalize_known_category` — "Status" → "status" via alias
- `test_normalize_unknown_creates_new` — new category registered with frequency=1
- `test_frequency_increments` — second use of same category increments count
- `test_suggest_consolidation_finds_rare` — frequency=1 schemas flagged

---

### Phase 3 — EntityAlias Table & Fuzzy WikiLink Resolution

**Goal:** Store per-entity aliases (from frontmatter `aliases:` key). When a WikiLink
fails exact title/permalink match, fall back to alias search before returning None.

---

#### [NEW] Migration: `add_entity_alias_table`

File: `src/memopad/alembic/versions/<hash>_add_entity_alias_table.py`

```sql
CREATE TABLE entity_alias (
    id         INTEGER PRIMARY KEY,
    entity_id  INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    alias      VARCHAR NOT NULL,
    source     VARCHAR NOT NULL DEFAULT 'frontmatter', -- 'frontmatter' | 'manual'
    created_at DATETIME NOT NULL,
    UNIQUE (project_id, alias)
);
CREATE INDEX ix_entity_alias_entity ON entity_alias (entity_id);
CREATE INDEX ix_entity_alias_alias  ON entity_alias (project_id, alias);
```

---

#### [NEW] `entity_alias.py` (model)

File: `src/memopad/models/entity_alias.py`

```python
class EntityAlias(Base):
    __tablename__ = "entity_alias"
    id: Mapped[int] = ...
    entity_id: Mapped[int] = mapped_column(ForeignKey("entity.id", ondelete="CASCADE"))
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, default="frontmatter")
    created_at: Mapped[datetime] = ...
    entity = relationship("Entity", back_populates="aliases")
```

Add `aliases` relationship to `Entity`:
```python
aliases = relationship("EntityAlias", back_populates="entity", cascade="all, delete-orphan")
```

---

#### [NEW] `entity_alias_repository.py`

File: `src/memopad/repository/entity_alias_repository.py`

Key methods:
- `find_by_alias(project_id, alias) → Optional[Entity]`
- `upsert_aliases(entity_id, project_id, aliases: list[str], source: str)`
- `delete_by_entity(entity_id)`

---

#### [MODIFY] [entity_parser.py](file:///c:/ANTI/memopad/src/memopad/markdown/entity_parser.py)

In `parse_markdown_content()`, extract `aliases` from frontmatter and include in returned `EntityMarkdown`:

```python
aliases = parse_tags(metadata.get("aliases", []))
# EntityMarkdown already has a generic frontmatter dict; no schema change needed
# aliases are stored in entity_frontmatter.metadata["aliases"]
```

---

#### [MODIFY] [entity_service.py](file:///c:/ANTI/memopad/src/memopad/services/entity_service.py)

In `upsert_entity_from_markdown()`, after entity is saved, sync aliases:

```python
if self.alias_repository:
    raw_aliases = markdown.frontmatter.metadata.get("aliases", [])
    if raw_aliases:
        await self.alias_repository.upsert_aliases(
            entity_id=created.id,
            project_id=self.repository.project_id,
            aliases=raw_aliases,
            source="frontmatter",
        )
```

---

#### [MODIFY] [link_resolver.py](file:///c:/ANTI/memopad/src/memopad/services/link_resolver.py)

Add alias lookup as step 2.5 — between title match and file path match:

```python
# 2.5 Try alias match (frontmatter aliases)
if self.alias_repository:
    alias_entity = await self.alias_repository.find_by_alias(
        self.entity_repository.project_id, clean_text
    )
    if alias_entity:
        logger.debug(f"Found entity via alias: {alias_entity.title}")
        return alias_entity
```

Add `alias_repository: Optional[EntityAliasRepository] = None` to `__init__`.

---

#### Tests

File: `tests/services/test_entity_alias.py`

- `test_alias_stored_from_frontmatter` — parsing `aliases: [Newton]` creates alias row
- `test_link_resolves_via_alias` — `[[Newton]]` resolves to "Isaac Newton" entity
- `test_alias_deleted_on_entity_delete` — cascade delete cleans alias rows
- `test_duplicate_alias_upsert` — re-registering same alias is idempotent

---

### Phase 4 — Hub-Aware Context Scoring

**Goal:** Post-process BFS results from `context_service.find_related()` to penalize
highly-connected hub nodes (e.g. a "Meeting" entity linked to 200 others) and boost
information-dense leaf nodes. No schema changes required.

---

#### [MODIFY] [context_service.py](file:///c:/ANTI/memopad/src/memopad/services/context_service.py)

**Step A — Degree query helper (new private method):**

```python
async def _fetch_entity_degrees(self, entity_ids: list[int]) -> dict[int, int]:
    """Return {entity_id: total_relation_count} for hub scoring."""
    # Single aggregate query on the relation table
    result = await self.search_repository.execute_query(text(f"""
        SELECT entity_id, COUNT(*) as degree FROM (
            SELECT from_id as entity_id FROM relation
            WHERE from_id IN ({','.join(str(i) for i in entity_ids)})
            AND project_id = :project_id
            UNION ALL
            SELECT to_id as entity_id FROM relation
            WHERE to_id IN ({','.join(str(i) for i in entity_ids)})
            AND project_id = :project_id
        ) sub GROUP BY entity_id
    """), params={"project_id": self.search_repository.project_id})
    return {row.entity_id: row.degree for row in result.all()}
```

**Step B — Scoring pass (new private method):**

```python
def _apply_hub_penalty(
    self,
    rows: list[ContextResultRow],
    degrees: dict[int, int],
    depth_weight: float = 0.5,
) -> list[ContextResultRow]:
    """Re-rank by combining depth penalty with inverse-degree hub suppression.

    score = (1 / (depth + 1)) * (1 / sqrt(degree + 1))

    Trigger: post-BFS re-ranking requested
    Why: high-degree hub nodes (connected to hundreds of entities) carry less
         information per link than rare leaf nodes; suppress them to surface
         specific, contextually rich results
    Outcome: leaf nodes with few relations float to the top; hub nodes sink
    """
    import math
    for row in rows:
        degree = degrees.get(row.id, 1)
        hub_penalty = 1.0 / math.sqrt(degree + 1)
        depth_penalty = 1.0 / (row.depth + 1)
        row.relevance_score = depth_penalty * hub_penalty  # type: ignore[attr-defined]
    return sorted(rows, key=lambda r: getattr(r, "relevance_score", 0), reverse=True)
```

**Step C — Wire into `find_related()`:**

After fetching `context_rows` (line ~323), add:

```python
if context_rows:
    entity_ids = [r.id for r in context_rows if r.type == "entity"]
    if entity_ids:
        degrees = await self._fetch_entity_degrees(entity_ids)
        context_rows = self._apply_hub_penalty(context_rows, degrees)
```

---

#### Tests

File: `tests/services/test_context_service_hub_scoring.py`

- `test_hub_node_ranked_lower` — entity with 50 relations ranks below entity with 2
- `test_leaf_node_ranked_higher` — leaf node with depth=2 beats hub at depth=1
- `test_no_entities_no_crash` — all-relation rows handled gracefully
- `test_degree_query_correctness` — aggregate query returns correct in+out degrees

---

## Verification Plan

### Automated Tests

```bash
# Run full test suite after each phase
just test-sqlite

# Run only impacted tests during development
just testmon

# Run smoke test to verify MCP tools work end-to-end
just test-smoke

# Run full check (lint + typecheck + tests)
just check
```

### Per-Phase Verification

| Phase | Key check |
|-------|-----------|
| 1 | Write two observations with same entity + same category + different content → both have `conflict_score > 0` and `conflicting_obs_id` set |
| 1 | `build_context` output includes `⚠️ CONFLICT` marker |
| 2 | Write observation with category `"Status"` → stored as `"status"` if alias registered |
| 2 | `list_observation_schemas` MCP tool returns correct table with frequency counts |
| 3 | Entity with frontmatter `aliases: [Newton]` → `[[Newton]]` WikiLink resolves correctly |
| 3 | Existing exact-match links unaffected (regression test) |
| 4 | `build_context` on a hub-heavy graph returns leaf nodes before hub nodes |

### Manual Verification

1. Run `memopad doctor` after each phase — confirms file ↔ DB loop is intact
2. Open a test project in Obsidian/editor, create conflicting notes, call `build_context` via MCP Inspector — confirm conflict markers appear
3. Add `aliases: [Newton]` to a note frontmatter, call `[[Newton]]` WikiLink — confirm resolution in MCP Inspector

---

## Implementation Order

```
Phase 1 (Conflict Detection)   → most impact, self-contained service
Phase 2 (Schema Registry)      → depends on Phase 1 conflict normalization idea
Phase 3 (EntityAlias)          → independent of 1 & 2, safe to parallelise
Phase 4 (Hub Scoring)          → no schema changes, quickest to ship
```

Each phase produces a standalone PR that passes `just check` before the next begins.
