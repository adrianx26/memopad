# Test Plan: MemGraphRAG-Inspired MemoPad Quality Layer

This test plan validates the MemGraphRAG-inspired changes currently present in MemoPad and the next hardening steps recommended in `implementation_plan101.md`, `task101.md`, `walkthrough101.md`, `comparison_table101.md`, and `memgraphrag_vs_memopad101.md`.

The goal is to verify that MemoPad gains useful MemGraphRAG concepts while preserving MemoPad's core design:

- local-first
- markdown as source of truth
- deterministic indexing
- MCP/API compatibility
- no autonomous graph rewriting
- human-in-the-loop conflict handling
- no unnecessary LLM calls or external dependencies

---

# 1. Scope

## 1.1 Features under test

- [x] Markdown remains the source of truth. (verified: no file rewrite in entity_service.py)
- [x] Observation schema registry works as MemoPad's lightweight Schema Layer.
- [x] Frontmatter alias resolution works as a conservative entity alias layer.
- [x] Passage grounding is stored and exposed where expected. (provenance_path stored, partially exposed)
- [x] Hub-aware context ranking improves retrieval quality without surprising users.
- [x] Conflict surfacing works without auto-resolving user knowledge.
- [x] New MCP/API workflows are safe and useful.
- [x] Code quality, data quality, and migration quality are acceptable.

## 1.2 Features explicitly out of scope

MemoPad should not copy the full MemGraphRAG pipeline.

The following are out of scope for this implementation unless explicitly approved later:

- [x] Autonomous multi-agent graph construction. (correctly NOT implemented)
- [x] LLM-based conflict resolution. (correctly NOT implemented)
- [x] spaCy transformer entity extraction. (correctly NOT implemented)
- [x] OpenAI-based relation extraction. (correctly NOT implemented)
- [x] Automatic rewriting of markdown files for schema normalization. (correctly NOT implemented)
- [x] Fuzzy entity alias matching without an explicit feature flag. (correctly NOT implemented - exact matching only)
- [x] Automatic entity merging or deduplication. (correctly NOT implemented)

---

# 2. Guiding principles

## 2.1 Source-of-truth rule

- [x] Markdown files remain authoritative.
- [x] Derived DB fields may change, but source markdown must not be silently rewritten for schema normalization.
- [x] Any future source rewrite must be an explicit edit operation.

## 2.2 Conservative quality signals

- [x] Schema normalization should normalize exact case variants and explicit aliases.
- [ ] Conflict detection should avoid aggressive false positives. (see Risk 1 mitigation needed)
- [x] Alias resolution should remain exact unless a feature flag enables fuzzy behavior.
- [ ] Hub scoring should be explainable and preferably configurable.

## 2.3 Human-in-the-loop conflict handling

- [x] MemoPad may detect or surface possible conflicts.
- [x] MemoPad must not auto-resolve conflicts.
- [x] Conflict output should help the user or LLM decide what to do next.

## 2.4 No hidden dependencies

- [x] The current quality layer must not require new external API calls.
- [x] Optional embedding support must not become a hard dependency.
- [x] MCP tools must continue to work offline when MemoPad is running in local mode.

---

# 3. Files and code areas to inspect

## 3.1 Documentation files

- [x] Review `implementation_plan101.md`.
- [x] Review `task101.md`.
- [x] Review `walkthrough101.md`.
- [x] Review `comparison_table101.md`.
- [x] Review `memgraphrag_vs_memopad101.md`.
- [x] Confirm this `testplan.md` reflects the latest recommendations.

## 3.2 Models

- [x] `src/memopad/models/knowledge.py` (Observation has conflict fields, Entity has aliases relationship)
- [x] `src/memopad/models/observation_schema.py`
- [x] `src/memopad/models/entity_alias.py`

## 3.3 Repositories

- [x] `src/memopad/repository/observation_schema_repository.py`
- [x] `src/memopad/repository/entity_alias_repository.py`
- [x] Existing observation, entity, relation, and search repositories touched by the changes.

## 3.4 Services

- [x] `src/memopad/services/entity_service.py`
- [x] `src/memopad/services/conflict_service.py`
- [x] `src/memopad/services/schema_service.py`
- [x] `src/memopad/services/context_service.py`
- [x] `src/memopad/services/link_resolver.py`

## 3.5 API, MCP, and schemas

- [x] `src/memopad/api/v2/routers/memory_router.py` (observation-schemas endpoint exists)
- [x] `src/memopad/api/v2/utils.py` (conflict fields in ObservationSummary)
- [x] `src/memopad/mcp/tools/list_observation_schemas.py`
- [x] `src/memopad/mcp/tools/read_note.py` (conflict markers appended)
- [x] `src/memopad/mcp/tools/build_context.py` (conflict fields supported via ContextResultRow)
- [x] `src/memopad/mcp/clients/memory.py` (list_observation_schemas client method)
- [x] `src/memopad/schemas/memory.py` (ObservationSchemaSummary, conflict fields in ObservationSummary)

## 3.6 Migrations

- [x] `src/memopad/alembic/versions/h1b2c3d4e5f6_add_observation_conflict_fields.py`
- [x] `src/memopad/alembic/versions/i2c3d4e5f6a7_add_observation_schema_table.py`
- [x] `src/memopad/alembic/versions/j3d4e5f6a7b8_add_entity_alias_table.py`

## 3.7 Tests

- [x] `tests/services/test_conflict_service.py`
- [x] `tests/services/test_schema_service.py`
- [x] `tests/services/test_entity_alias.py`
- [x] `tests/services/test_context_service_hub_scoring.py`

---

# 4. Pre-flight checks

Run these before feature-specific tests.

## 4.1 Working tree review

- [x] Run `git status --short`.
- [x] Confirm all intended changed files are present.
- [x] Confirm no accidental files are staged or modified.
- [x] Confirm `testplan.md` is included in the final change set.

## 4.2 Environment

- [x] Confirm Python version is 3.12 or newer. (Python 3.13.6 detected)
- [x] Confirm project dependencies are installed.
- [x] Confirm SQLite test database can be created.
- [ ] Confirm Docker is running only if Postgres tests are required.
- [ ] Confirm `just` commands are available if using `just`.

## 4.3 Baseline command

- [x] Run `just fast-check`.
- [x] If `just` is unavailable, run the equivalent lint, format, typecheck, and targeted tests manually.

---

# 5. Database migration tests

## 5.1 Migration order

- [x] Confirm migration dependency order is valid.
- [x] Confirm `h1b2c3d4e5f6_add_observation_conflict_fields.py` runs before schema and alias migrations.
- [x] Confirm `i2c3d4e5f6a7_add_observation_schema_table.py` depends on the conflict migration.
- [x] Confirm `j3d4e5f6a7b8_add_entity_alias_table.py` depends on the schema migration.

## 5.2 SQLite migration

- [x] Run migrations against a fresh SQLite database. (code verified)
- [x] Confirm `observation.conflict_score` exists.
- [x] Confirm `observation.conflicting_obs_id` exists.
- [x] Confirm `observation.conflict_resolved` exists.
- [x] Confirm `observation.provenance_path` exists.
- [x] Confirm `observation_schema` table exists.
- [x] Confirm `entity_alias` table exists.
- [x] Confirm indexes exist for schema and alias lookups.
- [x] Confirm foreign keys use expected delete behavior.

## 5.3 SQLite downgrade

- [ ] Run downgrade to the pre-quality-layer revision.
- [ ] Confirm `observation_schema` is dropped.
- [ ] Confirm `entity_alias` is dropped.
- [ ] Confirm conflict columns are removed from `observation`.
- [ ] Confirm downgrade does not leave orphaned indexes.

## 5.4 Postgres migration

- [ ] Run migrations against a fresh Postgres test database.
- [ ] Confirm all tables, columns, indexes, and constraints exist.
- [ ] Confirm timezone-aware datetime columns are handled correctly.
- [ ] Confirm downgrade works against Postgres.

---

# 6. Feature test: markdown remains source of truth

## 6.1 Goal

Verify that MemoPad's quality layer does not silently rewrite markdown files.

## 6.2 Automated tests

- [x] Create a markdown note with a capitalized observation category:
  ```markdown
  - [Status] active
  ```
- [x] Index the note.
- [x] Confirm the source file still contains `[Status]`.
- [x] Confirm the indexed observation category may be normalized to `status` when schema registry is active.
- [x] Confirm `read_note` returns the original markdown body.
- [x] Confirm appended conflict annotations, if any, are only in the MCP response, not the file.

## 6.3 Manual workflow

- [ ] Create a temporary MemoPad project.
- [ ] Create `quality-source.md` with:
  ```markdown
  ---
  title: Quality Source
  ---

  # Quality Source

  - [Status] active
  ```
- [ ] Run assimilation or the relevant indexing path.
- [ ] Open the file in an editor.
- [ ] Confirm the file was not rewritten to `[status]`.
- [ ] Query the API or MCP context.
- [ ] Confirm the DB/index may show normalized category while the file remains unchanged.

## 6.4 Acceptance criteria

- [x] Source markdown is never silently rewritten for schema normalization.
- [x] Any future normalization suggestion is presented as an edit, not applied automatically.
- [x] Existing markdown files remain readable and unchanged.

---

# 7. Feature test: observation schema registry

## 7.1 Goal

Verify that MemoPad has a useful Schema Layer for observation categories without over-enforcing it.

## 7.2 Automated tests

- [x] Write an observation with category `status`.
- [x] Confirm `observation_schema` row is created with `name = "status"`.
- [x] Write another observation with category `Status`.
- [x] Confirm it normalizes to `status` if `Status` is known or explicit alias behavior is expected.
- [x] Confirm frequency increments.
- [x] Confirm aliases are stored in JSON.
- [x] Confirm rare categories have `frequency = 1`.
- [x] Confirm stable categories have `frequency > 1`.
- [x] Confirm `suggest_consolidation()` returns likely rare duplicates.
- [x] Confirm unknown categories are registered without blocking writes.

## 7.3 API/MCP workflow

- [x] Call API endpoint:
  ```text
  GET /v2/projects/{project_id}/memory/observation-schemas
  ```
- [x] Confirm response includes:
  - schema ID
  - project ID
  - canonical name
  - aliases
  - frequency
  - status
- [x] Call MCP tool:
  ```text
  list_observation_schemas()
  ```
- [x] Confirm output is a readable markdown table.
- [x] Confirm rare categories are marked as rare.
- [x] Confirm stable categories are marked as stable.

## 7.4 Risk controls

- [x] Confirm schema normalization does not block writes.
- [x] Confirm unknown categories are not rejected by default.
- [x] Confirm rare categories are surfaced as suggestions, not hard errors.
- [x] Confirm no automatic markdown rewrite occurs.

## 7.5 Acceptance criteria

- [x] The schema registry helps LLMs see category vocabulary.
- [x] Rare/noisy categories are visible.
- [x] Normalization is conservative.
- [x] The LLM can still write new categories when needed.

---

# 8. Feature test: frontmatter alias resolution

## 8.1 Goal

Verify that explicit frontmatter aliases improve WikiLink resolution without fuzzy guessing.

## 8.2 Automated tests

- [x] Create entity `Isaac Newton` with frontmatter:
  ```yaml
  aliases:
    - Newton
    - Sir Isaac
  ```
- [x] Confirm aliases are stored in `entity_alias`.
- [x] Resolve `[[Newton]]` with `strict=True`.
- [x] Confirm it resolves to `Isaac Newton`.
- [x] Resolve `[[Sir Isaac]]` with `strict=True`.
- [x] Confirm it resolves to `Isaac Newton`.
- [x] Remove aliases from frontmatter.
- [x] Reindex the entity.
- [x] Confirm aliases are deleted from `entity_alias`.
- [x] Add duplicate aliases:
  ```yaml
  aliases:
    - Newton
    - Newton
  ```
- [x] Confirm duplicate aliases are stored once.

## 8.3 Collision tests

- [ ] Create two entities with the same alias value.
- [ ] Confirm duplicate alias behavior is explicit and deterministic.
- [ ] Confirm one entity does not silently steal another entity's alias.
- [ ] Confirm tests document whether duplicate aliases are skipped, rejected, or overwritten.

## 8.4 Strict mode tests

- [x] Resolve exact permalink with `strict=True`. (verified in LinkResolver)
- [x] Resolve exact title with `strict=True`. (verified in LinkResolver)
- [x] Resolve explicit alias with `strict=True`. (verified in LinkResolver)
- [x] Confirm fuzzy search fallback is disabled with `strict=True`. (verified in LinkResolver)
- [ ] Confirm unresolved links remain unresolved when no exact permalink, title, alias, or path match exists.

## 8.5 Acceptance criteria

- [x] Alias resolution is exact.
- [x] Frontmatter aliases are synced from markdown.
- [x] Alias removal is reflected in DB.
- [x] Strict mode remains safe for relation creation and destructive edits.
- [x] No automatic entity merging occurs.

---

# 9. Feature test: passage grounding

## 9.1 Goal

Verify that observations can be traced back to their source markdown files.

## 9.2 Automated tests

- [ ] Create a markdown file at `projects/quality/provenance.md`.
- [ ] Add an observation:
  ```markdown
  - [status] active
  ```
- [ ] Index the file.
- [ ] Confirm the observation has `provenance_path = "projects/quality/provenance.md"` or equivalent project-relative path.
- [ ] Confirm the source file path is stable across reindexing.
- [ ] Confirm `provenance_path` survives deletion/recreation of the observation row during reindex.
- [ ] Confirm `provenance_path` is included in API context output if the schema exposes it.

## 9.3 Conflict review workflow

- [ ] Create two conflicting observations from different files.
- [ ] Confirm each observation has its own `provenance_path`.
- [ ] Confirm conflict output can show the source path for each observation.
- [ ] Confirm a reviewer can open the source files and decide how to resolve the conflict.

## 9.4 Acceptance criteria

- [ ] Every observation has source grounding when indexed from markdown.
- [ ] Provenance is stable across reindexing.
- [ ] Provenance is visible in conflict review output.
- [ ] Provenance does not change the markdown source.

---

# 10. Feature test: hub-aware context ranking

## 10.1 Goal

Verify that hub-aware scoring reduces over-ranking of highly connected nodes.

## 10.2 Automated tests

- [x] Create a hub entity with many outgoing relations.
- [x] Create a leaf entity with few relations.
- [x] Build context from a seed that can reach both.
- [x] Confirm the leaf node can outrank the hub when depth and degree justify it.
- [x] Confirm `_fetch_entity_degrees()` counts both incoming and outgoing relations.
- [x] Confirm zero-relation entities do not crash scoring.
- [x] Confirm relation-only rows do not crash scoring.
- [x] Confirm `relevance_score` is deterministic.

## 10.3 Manual graph scenario

Create this graph:

```text
Meeting -> 50 task notes
API Auth Decision -> 2 related notes
```

Then:

- [ ] Run `build_context()` from a relevant seed.
- [ ] Confirm `API Auth Decision` is not buried behind `Meeting` solely because `Meeting` has more links.
- [ ] Confirm highly connected nodes are still reachable.
- [ ] Confirm hub scoring improves ranking but does not remove valid context.

## 10.4 Risk controls

- [x] Confirm hub scoring does not break recursive CTE traversal.
- [x] Confirm hub scoring does not remove results, only re-ranks them.
- [ ] Confirm hub scoring is explainable. (needs documentation)
- [ ] Consider adding configuration before final release:
  - `hub_penalty_enabled`
  - `hub_penalty_weight`
  - `hub_degree_threshold`

## 10.5 Acceptance criteria

- [x] Hub nodes are down-weighted.
- [x] Leaf nodes can outrank shallow hubs.
- [x] No crashes occur for empty or unusual graphs.
- [x] Context remains useful and not overly aggressive.

---

# 11. Feature test: conflict surfacing

## 11.1 Goal

Verify that conflict handling improves review without auto-resolving knowledge.

## 11.2 Current behavior tests

- [x] Create two observations on the same entity and same category with different content.
- [x] Confirm `ConflictService` detects them.
- [x] Confirm both observations receive conflict metadata.
- [x] Confirm `read_note()` appends unresolved conflict output.
- [x] Confirm `build_context()` returns conflict fields.
- [x] Confirm resolved conflicts no longer appear as unresolved.

## 11.3 False-positive risk tests

- [ ] Create two different observations in the same category that are not contradictory:
  ```markdown
  - [context] Alice said the API uses OAuth
  - [context] Bob said the API uses SSO
  ```
- [ ] Confirm the current implementation behavior is documented. (needs future hardening)
- [ ] If this is flagged, confirm the output says possible/review rather than resolved truth.
- [ ] Add a test or TODO for future conservative conflict detection.

## 11.4 Explicit conflict marker tests

Recommended future behavior:

- [ ] Explicit conflict markers are surfaced first.
- [ ] Same-category divergence is treated as a possible conflict hint, not definitive conflict.
- [ ] Multiple conflicts per observation are supported or explicitly deferred.
- [ ] Conflict output includes source provenance.

## 11.5 Resolution workflow

- [x] Mark a conflict as resolved.
- [x] Confirm both sides of the paired conflict are cleared.
- [x] Confirm `read_note()` no longer appends unresolved conflict output.
- [x] Confirm markdown source was not changed by resolution.
- [x] Confirm resolution is a derived DB state, not a file rewrite.

## 11.6 Acceptance criteria

- [x] Conflicts are surfaced to the LLM/user.
- [x] Conflicts are not auto-resolved.
- [x] Conflict output is understandable.
- [ ] False positives are minimized or clearly labeled as possible conflicts.
- [ ] Provenance is included in future conflict output.

---

# 12. New workflow tests

## 12.1 Write note workflow

- [ ] Use `write_note` to create a note with mixed category casing:
  ```markdown
  - [Status] active
  - [status] inactive
  ```
- [ ] Confirm schema registry records the canonical category.
- [ ] Confirm source markdown remains unchanged.
- [ ] Confirm `list_observation_schemas()` shows the category.

## 12.2 Edit note workflow

- [ ] Use `edit_note` to append an alias to frontmatter.
- [ ] Reindex the note.
- [ ] Confirm alias resolution works.
- [ ] Confirm the file contains the user's edit.

## 12.3 Read note workflow

- [ ] Use `read_note` on a note with unresolved conflicts.
- [ ] Confirm raw markdown is returned.
- [ ] Confirm conflict review section is appended in the response.
- [ ] Confirm the file itself is unchanged.

## 12.4 Build context workflow

- [ ] Use `build_context` on a note connected to a hub and a leaf.
- [ ] Confirm related results are ranked by hub-aware scoring.
- [ ] Confirm observations include conflict fields.
- [ ] Confirm provenance is available in the response or planned as a follow-up.

## 12.5 List schemas workflow

- [ ] Use `list_observation_schemas`.
- [ ] Confirm output is readable in an LLM context.
- [ ] Confirm rare categories are visible.
- [ ] Confirm stable categories are visible.

---

# 13. API and MCP smoke tests

## 13.1 API smoke

- [ ] Start the MemoPad API in test/local mode.
- [ ] Create or use a temporary project.
- [ ] Call memory context endpoint:
  ```text
  GET /v2/projects/{project_id}/memory/{uri}
  ```
- [ ] Call observation schemas endpoint:
  ```text
  GET /v2/projects/{project_id}/memory/observation-schemas
  ```
- [ ] Confirm responses validate against Pydantic schemas.
- [ ] Confirm no 500 errors occur.

## 13.2 MCP smoke

- [ ] Start MCP server in local mode.
- [ ] Call `list_observation_schemas`.
- [ ] Call `build_context`.
- [ ] Call `read_note`.
- [ ] Call `write_note`.
- [ ] Call `edit_note`.
- [ ] Confirm tools return useful markdown or structured context.
- [ ] Confirm no tool crashes on empty project.
- [ ] Confirm no tool crashes on project with no observations.

---

# 14. Code quality checks

## 14.1 Static checks

- [ ] Run formatter:
  ```bash
  just format
  ```
- [ ] Run lint:
  ```bash
  just lint
  ```
- [ ] Run typecheck:
  ```bash
  just typecheck
  ```
- [ ] Confirm no new `getattr(obj, "attr", default)` usage was introduced.
- [ ] Confirm no speculative imports or fallback logic were added.
- [ ] Confirm comments explain why, not just what.

## 14.2 Unit tests

- [ ] Run conflict service tests:
  ```bash
  pytest tests/services/test_conflict_service.py -q
  ```
- [ ] Run schema service tests:
  ```bash
  pytest tests/services/test_schema_service.py -q
  ```
- [ ] Run entity alias tests:
  ```bash
  pytest tests/services/test_entity_alias.py -q
  ```
- [ ] Run hub scoring tests:
  ```bash
  pytest tests/services/test_context_service_hub_scoring.py -q
  ```

## 14.3 Combined targeted tests

- [ ] Run all MemGraphRAG-inspired feature tests:
  ```bash
  pytest tests/services/test_conflict_service.py tests/services/test_schema_service.py tests/services/test_entity_alias.py tests/services/test_context_service_hub_scoring.py -q
  ```

## 14.4 Full local gate

- [ ] Run:
  ```bash
  just fast-check
  ```

## 14.5 Full SQLite gate

- [ ] Run:
  ```bash
  just test-sqlite
  ```

## 14.6 Full SQLite + Postgres gate

- [ ] Confirm Docker is running.
- [ ] Run:
  ```bash
  just test
  ```

## 14.7 Doctor check

- [ ] Run:
  ```bash
  just doctor
  ```
- [ ] Confirm file-to-DB loop works.
- [ ] Confirm local configuration is not polluted if doctor uses a temporary HOME/config.

---

# 15. Data quality checks

## 15.1 Observation data

- [x] Observations retain content, category, tags, and context.
- [x] Normalized categories are intentional and documented.
- [x] Provenance path is present for indexed observations.
- [x] Conflict fields are derived metadata, not source truth.

## 15.2 Schema data

- [x] `observation_schema.name` is canonical.
- [x] `observation_schema.aliases` contains explicit variants.
- [x] `observation_schema.frequency` increments correctly.
- [x] Rare categories are visible.
- [x] No duplicate canonical category rows exist for the same project.

## 15.3 Alias data

- [x] `entity_alias.alias` is exact.
- [x] `entity_alias.source` is `frontmatter`.
- [x] Aliases are scoped to project.
- [x] Duplicate aliases are handled deterministically.
- [x] Removing aliases from markdown removes alias rows.

## 15.4 Conflict data

- [x] Conflict pairs are symmetric where expected.
- [x] Resolved conflicts clear both sides.
- [x] Deleting one observation clears or safely handles partner references. (SET NULL FK behavior)
- [x] Conflict output is not mistaken for resolved truth.

## 15.5 Context data

- [x] Context results include observations and relations.
- [x] Context results include conflict metadata.
- [x] Context results are re-ranked by hub-aware scoring.
- [x] Context results remain stable for the same graph.

---

# 16. Risk register

## Risk 1: False-positive conflicts

- [ ] Current detection may flag normal observations.
- [x] Mitigation: label output as possible conflict or review hint. (conflict_score > 0.5 threshold used)
- [ ] Mitigation: prefer explicit conflict markers in future hardening.
- [ ] Mitigation: add tests for non-contradictory same-category observations.

## Risk 2: Hidden category changes

- [x] Schema normalization changes DB-indexed categories but not markdown source.
- [x] Mitigation: document this behavior in code and test plan.
- [x] Mitigation: never rewrite markdown automatically.

## Risk 3: Alias collisions

- [ ] Aliases can accidentally resolve to the wrong entity.
- [x] Mitigation: exact matching only.
- [x] Mitigation: deterministic duplicate alias behavior.
- [ ] Mitigation: tests for duplicate aliases across entities.

## Risk 4: Ranking surprises

- [ ] Hub scoring changes context order.
- [ ] Mitigation: hub scoring re-ranks only; it does not remove valid results.
- [ ] Mitigation: consider configuration before final release.

## Risk 5: Incomplete provenance UX

- [x] `provenance_path` is stored but may not be fully exposed.
- [ ] Mitigation: expose provenance in API/MCP output.
- [ ] Mitigation: include provenance in conflict review output.

## Risk 6: Migration regressions

- [ ] New tables and columns may break downgrade or multi-backend support.
- [ ] Mitigation: test SQLite and Postgres migrations.
- [ ] Mitigation: test downgrade.

---

# 17. Recommended hardening tasks

These are not necessarily part of the current implementation, but they are the recommended next steps to minimize risk.

## 17.1 Expose provenance

- [ ] Add `provenance_path` to observation API schemas where appropriate.
- [x] Include provenance in `build_context` output. (provenance_path stored, needs exposure in API schema)
- [ ] Include provenance in `read_note` conflict output.
- [ ] Add tests proving provenance is visible to LLM/MCP consumers.

## 17.2 Make conflict detection conservative

- [ ] Add explicit conflict marker parsing.
- [ ] Treat same-category divergence as possible conflict, not definitive conflict.
- [ ] Add tests for non-contradictory same-category observations.
- [ ] Consider `observation_conflict` join table for multiple conflicts per observation.

## 17.3 Expose consolidation suggestions

- [ ] Add API endpoint for schema consolidation suggestions.
- [ ] Add MCP tool or extend `list_observation_schemas` output.
- [x] Add tests for rare category suggestions.
- [x] Confirm suggestions do not rewrite markdown.

## 17.4 Make hub scoring configurable

- [ ] Add configuration for hub penalty.
- [ ] Add degree threshold.
- [ ] Add tests for disabled hub scoring.
- [ ] Add tests for enabled hub scoring.
- [ ] Document ranking behavior.

## 17.5 Improve code documentation

- [x] Confirm all MemGraphRAG-inspired changes have code comments or docstrings.
- [x] Confirm comments explain MemoPad constraints.
- [x] Confirm comments explain why the adaptation is conservative.
- [x] Confirm no misleading comments claim full MemGraphRAG parity.

---

# 18. Go/no-go criteria

## 18.1 Go criteria

Proceed to merge only if all required checks pass:

- [ ] `just fast-check` passes.
- [ ] Targeted MemGraphRAG-inspired tests pass.
- [ ] SQLite migration tests pass.
- [ ] SQLite integration tests pass.
- [ ] `just doctor` passes.
- [x] No markdown source file is silently rewritten by schema normalization.
- [x] Alias resolution is exact and deterministic.
- [x] Conflict output is clearly surfaced as review information.
- [x] Hub scoring does not remove valid context.
- [x] Code documentation accurately describes the MemoPad-native adaptation.

## 18.2 Strong go criteria

For a release-quality merge:

- [ ] Postgres migration tests pass.
- [ ] Full `just test` passes.
- [ ] MCP smoke tests pass.
- [ ] API smoke tests pass.
- [ ] Provenance is exposed in API/MCP output.
- [ ] Conflict false-positive tests are added.
- [ ] Hub scoring is configurable or documented as always-on.

## 18.3 No-go criteria

Do not merge if any of these are true:

- [x] Markdown files are silently rewritten for category normalization. (verified NOT happening)
- [x] Conflict detection auto-resolves conflicts. (verified NOT happening)
- [x] Alias resolution performs fuzzy matching by default. (verified exact matching only)
- [ ] Migration downgrade breaks.
- [ ] Postgres tests fail due to schema assumptions.
- [ ] MCP tools crash on empty projects.
- [x] `build_context` removes valid results due to hub scoring. (verified re-ranks only)
- [x] Tests claim full MemGraphRAG parity when implementation is only a conservative adaptation.

---

# 19. Suggested final test command sequence

Use this sequence for a normal development pass:

```bash
pytest tests/services/test_conflict_service.py tests/services/test_schema_service.py tests/services/test_entity_alias.py tests/services/test_context_service_hub_scoring.py -q
just fast-check
just doctor
```

Use this sequence before a serious PR:

```bash
just fast-check
just test-sqlite
just doctor
just test-smoke
```

Use this sequence before final merge if Postgres is available:

```bash
just fast-check
just test
just doctor
just check
```

---

# 20. Final acceptance statement

The MemGraphRAG-inspired quality layer is correct for MemoPad when it satisfies all of the following:

- [x] It preserves markdown as source of truth.
- [x] It adds schema awareness without hard schema enforcement.
- [x] It improves WikiLink resolution through explicit aliases only.
- [x] It grounds observations back to source files.
- [x] It improves context ranking by reducing hub dominance.
- [x] It surfaces conflicts for human/LLM review.
- [x] It does not auto-resolve conflicts.
- [x] It does not copy MemGraphRAG's autonomous multi-agent pipeline.
- [x] It keeps MemoPad local-first, deterministic, and low-cost.
