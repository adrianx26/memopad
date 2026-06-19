# MemGraphRAG-Inspired MemoPad Quality Layer

This document consolidates the review of `implementation_plan101.md` and the current MemoPad implementation state.

The goal is to take the useful ideas from MemGraphRAG and adapt them to MemoPad without copying MemGraphRAG's full multi-agent, LLM-heavy graph construction pipeline.

MemoPad should remain:

- local-first
- markdown-as-source-of-truth
- file-centric
- deterministic
- MCP/API compatible
- safe for human-in-the-loop knowledge management

---

# Recommended MemoPad-native framing

Use this framing:

```text
MemGraphRAG-inspired quality layer for MemoPad
```

Avoid this framing:

```text
MemGraphRAG implementation inside MemoPad
```

MemoPad is not a multi-agent RAG pipeline. It is a local-first knowledge graph editor. The right adaptation is to add MemGraphRAG-inspired quality signals around MemoPad's existing markdown indexing pipeline.

---

# Core design

```text
Markdown files remain source of truth.
MemoPad indexes markdown into Entity + Observation + Relation.
MemGraphRAG-inspired services add quality metadata around that graph.
MCP tools expose quality signals in context output.
```

Instead of:

```text
LLM agents build the graph
```

MemoPad should do:

```text
User/LLM writes markdown
MemoPad indexes markdown
MemoPad detects possible schema noise, alias ambiguity, hub dominance, and conflicts
MemoPad exposes warnings and better context to the LLM
```

---

# What to borrow from MemGraphRAG

## 1. Three-layer memory concept

MemGraphRAG uses:

- Schema Layer
- Fact Layer
- Passage Layer

MemoPad equivalent:

```text
Schema Layer      -> observation_schema registry
Fact Layer        -> Observation rows
Passage Layer     -> provenance_path + source markdown file
```

## 2. Conflict awareness

MemoPad should detect or surface possible contradictions before they corrupt retrieval context.

Important constraint:

```text
MemoPad should surface conflicts, not auto-resolve them.
```

## 3. Ontology/noise control

MemoPad should track observation categories per project and normalize common variants.

Example:

```text
Status
status
STATE
```

can normalize to:

```text
status
```

## 4. Connectivity-aware retrieval

MemoPad should avoid over-ranking hub nodes that connect to too many other notes.

Example:

```text
Meeting -> linked to 200 notes
```

should not always dominate context over a specific note linked to 2 relevant notes.

## 5. Passage grounding

Every observation should be traceable back to the markdown file that produced it.

---

# What not to copy from MemGraphRAG

MemoPad should not directly copy:

- spaCy transformer entity extraction
- OpenAI-based relation extraction
- autonomous multi-agent graph construction
- LLM-based conflict resolution
- vector embedding dependency unless MemoPad already has a local embedding strategy
- automatic rewriting of user markdown
- replacing user markdown with generated graph facts

MemoPad already has a strong human-in-the-loop model. MemGraphRAG concepts should become quality signals, not autonomous agents.

---

# Current implementation status

## Summary table

| Feature | Status | Notes |
|---|---:|---|
| Preserve markdown as source of truth | Mostly implemented | Markdown remains the source. Schema normalization affects indexed observations, not source markdown. |
| Observation schema registry | Implemented | Model, repository, service, migration, API endpoint, MCP tool, and tests exist. |
| Frontmatter alias resolution | Implemented | Explicit frontmatter aliases are parsed, stored, and used by `LinkResolver`. |
| Passage grounding | Partially implemented | `provenance_path` is stored, but not fully exposed in API/MCP output. |
| Hub-aware context ranking | Implemented | `ContextService` applies degree-based hub penalties. |
| Conflict surfacing | Implemented, but aggressive | Conflict fields and `read_note` markers exist, but detection is too broad for production. |

---

# Feature 1: Preserve markdown as source of truth

## Status

Mostly implemented.

## Current implementation

MemoPad continues to parse and write markdown files through:

```text
src/memopad/markdown/entity_parser.py
src/memopad/services/entity_service.py
```

The schema registry normalizes category labels when indexing observations, but it does not rewrite the markdown file.

Example source markdown:

```markdown
- [Status] active
```

may be indexed as:

```text
category = status
```

but the source file still contains:

```markdown
- [Status] active
```

## Recommendation

Keep this behavior.

Do not silently rewrite user markdown for schema normalization. If MemoPad ever suggests source rewrites, they should be explicit edit suggestions, not automatic DB-side normalization.

---

# Feature 2: Observation schema registry

## Status

Implemented.

## Key files

```text
src/memopad/models/observation_schema.py
src/memopad/repository/observation_schema_repository.py
src/memopad/services/schema_service.py
src/memopad/api/v2/routers/memory_router.py
src/memopad/mcp/tools/list_observation_schemas.py
```

## Migration

```text
src/memopad/alembic/versions/i2c3d4e5f6a7_add_observation_schema_table.py
```

## What it does

The registry stores canonical observation categories per project.

Example table:

| Category | Frequency | Aliases | Status |
|---|---:|---|---|
| status | 47 | Status, STATE | stable |
| tech | 23 |  | stable |
| tmp-note | 1 |  | rare |

## MCP tool

```text
list_observation_schemas
```

This gives the LLM visibility into the project's observation vocabulary.

## Recommendation

Keep the registry, but make normalization conservative.

Recommended behavior:

- normalize exact case variants
- normalize explicit aliases
- flag rare categories
- avoid aggressive semantic merging
- expose consolidation suggestions as suggestions, not automatic rewrites

---

# Feature 3: Frontmatter alias resolution

## Status

Implemented.

## Key files

```text
src/memopad/models/entity_alias.py
src/memopad/repository/entity_alias_repository.py
src/memopad/markdown/entity_parser.py
src/memopad/services/entity_service.py
src/memopad/services/link_resolver.py
```

## Migration

```text
src/memopad/alembic/versions/j3d4e5f6a7b8_add_entity_alias_table.py
```

## What it does

MemoPad parses aliases from markdown frontmatter:

```yaml
---
title: Isaac Newton
aliases:
  - Newton
  - Sir Isaac
---
```

Then WikiLinks can resolve through aliases:

```markdown
[[Newton]]
```

resolves to:

```text
Isaac Newton
```

## Current resolution order

The current `LinkResolver` uses:

1. exact permalink
2. exact title
3. frontmatter alias
4. file path
5. search fallback when not in strict mode

## Recommendation

Keep exact alias matching only.

Do not add fuzzy alias matching unless it is behind an explicit feature flag. Fuzzy matching can create incorrect links and damage graph integrity.

---

# Feature 4: Passage grounding

## Status

Partially implemented.

## Key files

```text
src/memopad/models/knowledge.py
src/memopad/services/entity_service.py
src/memopad/alembic/versions/h1b2c3d4e5f6_add_observation_conflict_fields.py
```

## What it does

The `Observation` model includes:

```python
provenance_path
```

The entity service stores the source markdown path when indexing observations:

```python
provenance_path=file_path.as_posix()
```

## Recommendation

Finish the implementation by exposing provenance in API and MCP context output.

Add `provenance_path` to observation summaries where appropriate.

This would make conflict review much easier:

```text
Observation: active
Source: projects/memopad/quality-layer.md
Possible conflict with: deprecated
Conflict source: projects/memopad/roadmap.md
```

---

# Feature 5: Hub-aware context ranking

## Status

Implemented.

## Key file

```text
src/memopad/services/context_service.py
```

## Implemented methods

```python
_fetch_entity_degrees()
_apply_hub_penalty()
```

## What it does

MemoPad calculates relation degree for candidate entities and applies a hub penalty.

Conceptual formula:

```text
score = depth_score * inverse_degree_score
```

This prevents highly connected hub nodes from dominating context.

Example:

```text
Hub node: Meeting, degree 200
Leaf node: API auth decision, degree 2
```

The leaf node should often rank higher because it is more specific and information-dense.

## Recommendation

Keep this feature, but make it configurable.

Possible future settings:

```text
hub_penalty_enabled = true
hub_penalty_weight = 0.5
hub_degree_threshold = 25
```

This prevents users from being surprised by ranking changes in large graphs.

---

# Feature 6: Conflict surfacing

## Status

Implemented, but needs hardening.

## Key files

```text
src/memopad/models/knowledge.py
src/memopad/services/conflict_service.py
src/memopad/services/entity_service.py
src/memopad/schemas/memory.py
src/memopad/mcp/tools/read_note.py
```

## Migration

```text
src/memopad/alembic/versions/h1b2c3d4e5f6_add_observation_conflict_fields.py
```

## Conflict fields

```python
conflict_score
conflicting_obs_id
conflict_resolved
provenance_path
```

## Current behavior

The current `ConflictService` flags observations when:

```text
same entity + same category + different content
```

This is too broad.

Many legitimate observations can share a category and differ in content.

Example:

```markdown
- [status] active
- [status] deprecated
```

may be a conflict.

But this may not be:

```markdown
- [context] Alice said the API uses OAuth
- [context] Bob said the API uses SSO
```

Those are different sources reporting different statements, not necessarily a contradiction.

## Recommendation

Change conflict handling to be conservative.

Recommended first version:

1. Surface explicit conflict markers.
2. Surface low-confidence possible conflicts as review hints.
3. Do not automatically resolve conflicts.
4. Do not assume same-category difference means contradiction.
5. Consider replacing single `conflicting_obs_id` with an `observation_conflict` join table.

Better conflict model:

```text
observation_conflict
- id
- obs_a_id
- obs_b_id
- conflict_type
- confidence
- status
- created_at
- resolved_at
```

This supports multiple conflicts per observation.

---

# Recommended final architecture

```text
Markdown Parser
  ↓
EntityService
  ↓
Entity / Observation / Relation DB models
  ↓
Quality Layer
  ├── SchemaService
  ├── ConflictService
  ├── EntityAliasRepository
  └── Hub scoring in ContextService
  ↓
MCP Tools
  ├── build_context
  ├── read_note
  ├── list_observation_schemas
  └── search_notes
```

---

# Recommended implementation order

## Phase 0: Clean up framing

Rename the implementation plan from:

```text
MemGraphRAG-Inspired Improvements to MemoPad
```

to:

```text
MemGraphRAG-Inspired Quality Layer for MemoPad
```

This makes the scope accurate.

## Phase 1: Passage grounding completion

Finish exposing `provenance_path` in:

- API schemas
- `build_context`
- `read_note`
- conflict output

Goal:

```text
Every surfaced observation can be traced back to its source markdown file.
```

## Phase 2: Frontmatter alias hardening

Keep exact alias resolution.

Add tests for:

- duplicate aliases across entities
- case sensitivity
- alias removal when frontmatter is removed
- strict mode behavior

## Phase 3: Schema registry hardening

Keep conservative normalization.

Add:

- API/MCP endpoint for consolidation suggestions
- rare category visibility
- optional manual alias registration
- no automatic source markdown rewrites

## Phase 4: Hub-aware ranking hardening

Keep hub penalty.

Add:

- configuration flag
- degree threshold
- tests against large hub graphs
- documentation in MCP output if ranking is affected

## Phase 5: Conflict surfacing rewrite

Replace aggressive conflict detection with conservative surfacing.

Recommended conflict sources:

1. explicit markdown conflict markers
2. explicit user-created conflict records
3. low-confidence possible conflict hints
4. future embedding-assisted review only if MemoPad has a local embedding strategy

Do not auto-resolve conflicts.

---

# Recommended tests

Existing tests cover the first-pass implementation:

```text
tests/services/test_conflict_service.py
tests/services/test_schema_service.py
tests/services/test_entity_alias.py
tests/services/test_context_service_hub_scoring.py
```

Recommended additional tests:

## Passage grounding

- observation stores `provenance_path`
- `build_context` includes provenance
- `read_note` conflict output includes source paths

## Schema registry

- rare category appears in `list_observation_schemas`
- consolidation suggestions are exposed through API/MCP
- source markdown is not rewritten during normalization

## Alias resolution

- alias resolution works in strict mode
- fuzzy search fallback remains disabled in strict mode
- duplicate alias conflict behavior is explicit and tested

## Conflict surfacing

- explicit conflict marker is surfaced
- same-category different-content observations are not always treated as conflicts
- multiple conflicts per observation are handled by a join table
- resolved conflicts no longer appear in MCP output

## Hub scoring

- hub node is penalized
- leaf node can outrank shallow hub
- hub penalty is configurable
- no crash when entity has zero relations

---

# Recommended verification commands

During development:

```bash
just fast-check
```

For targeted tests:

```bash
pytest tests/services/test_conflict_service.py tests/services/test_schema_service.py tests/services/test_entity_alias.py tests/services/test_context_service_hub_scoring.py -q
```

Before merge:

```bash
just test-sqlite
just test-postgres
just doctor
just check
```

If `just test-postgres` is not available or Docker is not running, use:

```bash
just test-sqlite
just doctor
```

---

# Main risks

## Risk 1: False-positive conflicts

Current conflict detection may flag too many normal observations.

Mitigation:

```text
Use explicit conflict markers and conservative possible-conflict hints.
```

## Risk 2: Hidden category changes

Schema normalization changes DB-indexed categories but not markdown source.

Mitigation:

```text
Document that normalization affects retrieval/indexing, not source markdown.
```

## Risk 3: Alias collisions

Aliases can accidentally resolve to the wrong entity if not managed carefully.

Mitigation:

```text
Keep exact matching only and make duplicate alias behavior explicit.
```

## Risk 4: Ranking surprises

Hub-aware scoring changes context ordering.

Mitigation:

```text
Make hub scoring configurable and document the behavior.
```

## Risk 5: Incomplete provenance UX

`provenance_path` exists but is not fully surfaced.

Mitigation:

```text
Expose provenance in API and MCP context output.
```

---

# Final recommendation

The current implementation is a valid first pass.

The best next step is not to add more MemGraphRAG features. The best next step is to harden the existing MemoPad-native adaptation:

```text
1. expose provenance
2. make conflict detection conservative
3. add consolidation suggestions endpoint
4. make hub scoring configurable
5. document that markdown remains source of truth
```

This keeps MemoPad local-first, deterministic, and user-controlled while still gaining the most useful MemGraphRAG ideas: schema awareness, passage grounding, alias-aware connectivity, hub-aware retrieval, and conflict surfacing.
