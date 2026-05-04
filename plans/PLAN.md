# MemoPad — Engineering Plan & State of the Codebase

This is the consolidated engineering plan for MemoPad. It replaces the per-topic
plan documents that previously lived under `plans/`. Forward-looking work that
has been analyzed but not started (deferred Tier 2 + skipped Tier 3 from the
graphify analysis) lives in [`../futureplan.md`](../futureplan.md).

Each section is tagged with its current status against the codebase as of this
writing:

- ✅ **Implemented** — described work has landed; references point to the live code.
- 🚧 **In progress** — partial implementation; the gap is described.
- 💭 **Proposed** — analyzed but not started; tracked here so the design isn't lost.

The two install guides (`install-memopad-vscode.md`, `install-memopad-to-antigravity.md`)
are end-user documentation, not engineering plans. They should be moved to
`docs/install/` rather than merged into this file.

---

## 1. Assimilate tool

### 1.1 Refactor into a package — ✅ Implemented

The previous monolithic `src/memopad/mcp/tools/assimilate.py` has been split into
[`src/memopad/mcp/tools/assimilate/`](../src/memopad/mcp/tools/assimilate/):

| Module | Purpose |
|---|---|
| `__init__.py` | MCP tool entry point + `_assimilate_impl` |
| `config.py` | `AssimilateConfig` dataclass and constants |
| `types.py` | `CrawlResult` TypedDict |
| `html_utils.py` | `LinkExtractor`, `HTMLToText`, `extract_links`, `categorize_links` |
| `file_processor.py` | `FileProcessor` for PDF / DOCX / XLSX / images |
| `content_detector.py` | Heuristic content-type tagging |
| `note_builders.py` | `NOTE_BUILDERS` registry + `build_note(data, config)` |
| `crawler.py` | Web crawler with httpx connection pooling |
| `github.py` | Git clone + repo file scanning |
| `logger.py` | Structured run logging |

The legacy underscore-prefixed shims (`_build_overview_note`, `_safe_truncate`, etc.)
have been removed from the public package surface. Tests now import from the
proper modules and use the registry directly.

### 1.2 Bug fixes applied — ✅ Implemented

These were latent issues caught while reviewing the post-refactor state:

- Closing-paren bug in the GitHub Links count of the success summary.
- `asyncio.get_event_loop()` → `get_running_loop()` (Python 3.12+ deprecation).
- `shutil.rmtree(onerror=...)` → version-aware `onexc=` for 3.12+.
- Crawler errors now surface the failure reason, not just the URL.
- `text/plain` pages skip HTML-to-text conversion and link extraction.
- `crawler.queue` switched from list `pop(0)` (O(n)) to `collections.deque`.
- GitHub file-read errors propagate to the result's `errors` list instead of
  being silently dropped.
- Overlapping `REPO_FILE_PATTERNS` no longer cause the same file to be
  processed multiple times.
- 409 conflict detection in `_assimilate_impl` now uses the typed
  `response.status_code` when available, falling back to message-match only as
  a last resort.

### 1.3 Incremental assimilation (content-hash skip) — ✅ Implemented

Re-running `assimilate` against the same source no longer rewrites every
note. For each note we compute `SHA256(body)` and store it in
`entity_metadata._assimilate_content_hash` (alongside `_assimilate_source`).
On re-run:

  - **First run:** all notes are *created*; hashes are persisted.
  - **Re-run, no upstream change:** the optimistic create raises a conflict;
    we resolve the existing entity, compare hashes, and **skip** the update
    when they match. No DB write, no file rewrite, no reindex.
  - **Re-run, upstream changed:** hashes differ → update with the new hash.

The summary now reports `created / updated / unchanged / failed` counts so
the user can see at a glance how much of a re-run was cached.

Conflict detection was hardened to also catch raw SQLAlchemy
`IntegrityError("UNIQUE constraint failed: entity.permalink, …")` — the
shape we get when `fast=True` skips service-layer translation. Three new
test files cover the full re-run flow (skip, update, hash properties).

**Files touched:**
[`src/memopad/mcp/tools/assimilate/__init__.py`](../src/memopad/mcp/tools/assimilate/__init__.py),
[`tests/mcp/test_tool_assimilate.py`](../tests/mcp/test_tool_assimilate.py).

**Why not at the file-fetch / clone layer?** Network/disk I/O for the
fetch is small relative to DB writes + reindex (and embedding generation
once that's wired). The hash check at the note-write boundary cuts the
expensive part. Per-source caching of upstream content is a future
optimization, not a launch blocker.

### 1.4 Known fixed issues

The catalog in [`tofix.md`](../tofix.md) (Windows event loop, `CancelledError`,
`EntityCreationError` on retries) remains the authoritative record of platform
fixes. All items in that file are marked ✅ Fixed and remain so.

---

## 2. Search

MemoPad uses a dual-backend search:
- **SQLite**: FTS5 virtual table with `bm25()` ranking
  ([`sqlite_search_repository.py`](../src/memopad/repository/sqlite_search_repository.py:485))
- **PostgreSQL**: `tsvector` / `tsquery` with GIN indexes

### 2.1 BM25 — ✅ Implemented (defaults)

Active in [`sqlite_search_repository.py:485`](../src/memopad/repository/sqlite_search_repository.py:485):

```sql
SELECT ..., bm25(search_index) as score
FROM ...
ORDER BY score ASC  -- lower = more relevant
```

Uses FTS5 default parameters (`k1=1.2, b=0.75`) and equal column weights.

### 2.2 Column-weighted BM25 — 💭 Proposed

Title matches should rank above content matches. FTS5 supports per-column
weights as additional `bm25()` arguments:

```sql
SELECT *, bm25(search_index, 10.0, 1.0) AS score
```

**Effort:** small. **Risk:** low; tune via integration tests.

### 2.3 Snippet highlighting — 💭 Proposed

FTS5's `snippet()` would return matched fragments with delimiters around hit
terms. Currently no highlight column is exposed.

**Files to touch:** `sqlite_search_repository.py`, `search_index_row.py`,
`schemas/search.py`. **Effort:** medium.

### 2.4 Hybrid semantic search — ✅ Foundation in place

Embedding service and MCP tool wired up. Disabled by default (env var gate
`MEMOPAD_EMBEDDINGS_ENABLED`, optional `[embeddings]` extra). See:

- [`services/embedding_service.py`](../src/memopad/services/embedding_service.py) —
  `EmbeddingService`, `FastEmbedProvider`, `reciprocal_rank_fusion`
- [`mcp/tools/semantic_search.py`](../src/memopad/mcp/tools/semantic_search.py) — MCP tool

**Remaining work to make end-to-end:**
1. Hook `EmbeddingService.upsert()` into the sync pipeline so new/changed
   entities get embedded.
2. Add a `/v2/projects/{id}/search/semantic` API endpoint that performs the
   vector lookup and returns ranked entity_ids.
3. Update `semantic_search` MCP tool to call that endpoint and merge with the
   FTS5 result via `reciprocal_rank_fusion`.
4. Add a CLI command `memopad reindex --embeddings` to backfill existing notes.

The math (RRF, cosine, vector packing) is already covered by tests in
[`tests/services/test_embedding_service.py`](../tests/services/test_embedding_service.py).

---

## 3. Storage optimization

### 3.1 Duplicate detection + merge — ✅ Implemented

[`services/optimization_service.py`](../src/memopad/services/optimization_service.py)
detects duplicates (frontmatter-stripped, whitespace-canonicalized SHA256) and,
when `dry_run=False`, replaces each duplicate's body with a `redirects_to`
wikilink to the canonical (oldest) copy. Frontmatter is preserved so the DB
record stays addressable until the next sync.

`README.md`, `index.md`, and `.gitignore` are excluded from dedupe — they
legitimately repeat across directories.

The MCP tool [`optimize_storage`](../src/memopad/mcp/tools/optimize_storage.py)
defaults to dry-run and returns a markdown report. Service is covered by
[`tests/services/test_optimization_service.py`](../tests/services/test_optimization_service.py).

### 3.2 Cache layer — ✅ Implemented (Phase 1 & 2)

The 2Q (Two-Queue) cache lives at
[`src/memopad/cache/two_queue_cache.py`](../src/memopad/cache/two_queue_cache.py).
Behavior pinned by [`tests/cache/test_two_queue_cache.py`](../tests/cache/test_two_queue_cache.py)
(scan resistance, hit-rate floor under Pareto workload). The previous ad-hoc
verification scripts at the repo root (`verify_cache_optimizations.py`,
`verify_optimizations.py`) should be deleted once their assertions are fully
mirrored in the new test file.

---

## 4. Doctor / drift detection

### 4.1 Roundtrip mode — ✅ Implemented

`memopad doctor` (no args) runs in a throwaway temp project: creates an entity
via the API, verifies the file appears, writes a markdown file directly,
syncs, and confirms the search index sees it. Proves the file ↔ DB pipeline
in isolation.

### 4.2 `--project NAME [--fix]` mode — ✅ Implemented

Added in [`cli/commands/doctor.py`](../src/memopad/cli/commands/doctor.py).
Reports drift on a real project (new files on disk, modifications, deleted
files, moves, unresolved relations). With `--fix`, runs a `force_full` sync to
reconcile file ↔ DB drift.

Unresolved relations are *reported only* — fuzzy-rewriting user content is too
risky to auto-apply.

---

## 5. New tools (this iteration)

### 5.1 `daily_note` — ✅ Implemented

[`mcp/tools/daily_note.py`](../src/memopad/mcp/tools/daily_note.py).
Creates or opens `daily/YYYY-MM-DD.md` with prev/next wikilinks forming a
timeline chain. Accepts ISO dates or `today` / `yesterday` / `tomorrow`.

### 5.2 `backlinks` — ✅ Implemented

[`mcp/tools/backlinks.py`](../src/memopad/mcp/tools/backlinks.py) +
new repository method
[`RelationRepository.find_backlinks`](../src/memopad/repository/relation_repository.py)
+ API endpoint
[`GET /v2/projects/{id}/knowledge/entities/{entity_id}/backlinks`](../src/memopad/api/v2/routers/knowledge_router.py)
+ client method `KnowledgeClient.get_backlinks`. Returns both resolved
backlinks and unresolved `[[wikilinks]]` matching the target's permalink/title.

### 5.3 Graph analytics: `cluster_notes`, `hub_notes`, `find_path` — ✅ Implemented

Inspired by the [graphify](https://github.com/safishamsi/graphify) project,
ported as graph algorithms over MemoPad's existing relation graph (no
codebase scanning, no embedding-based clustering — pure topology).

- [`services/graph_analytics_service.py`](../src/memopad/services/graph_analytics_service.py) —
  loads entities + resolved relations into NetworkX, runs:
    - **Louvain community detection** (`find_clusters`) — deterministic via
      `seed=42`, falls back to connected components if Louvain raises.
    - **Degree centrality** (`find_hubs`) — separate in/out counts.
    - **Shortest path** (`find_path`) — undirected traversal with directional
      relation_type preserved per step.
- [`api/v2/routers/graph_analytics_router.py`](../src/memopad/api/v2/routers/graph_analytics_router.py)
  exposes `GET /graph/clusters`, `GET /graph/hubs`, `GET /graph/path`.
- [`mcp/clients/graph_analytics.py`](../src/memopad/mcp/clients/graph_analytics.py) —
  typed client.
- [`mcp/tools/graph_analytics.py`](../src/memopad/mcp/tools/graph_analytics.py) —
  three MCP tools.
- 30 service tests in
  [`tests/services/test_graph_analytics_service.py`](../tests/services/test_graph_analytics_service.py).

Adds a single new dependency: `networkx>=3.2` (~5 MB, pure Python, MIT).
Self-loops are skipped during graph construction, and unresolved relations
(to_id IS NULL) are excluded from analytics — they're surfaced separately by
`backlinks`.

### 5.4 Relation confidence + source_method — ✅ Implemented

Every `Relation` row now carries two provenance columns:

| Column | Type | Default | Meaning |
|---|---|---|---|
| `confidence` | `Float` (0.0–1.0) | `1.0` | Certainty of the relation |
| `source_method` | `String` | `"user_wikilink"` | How the relation was created |

**Why now?** The graphify analysis and the planned tree-sitter code extraction
(see [`futureplan.md §2.1`](../futureplan.md)) both produce relations with
varying certainty (AST-extracted vs. LLM-inferred vs. user-authored). Adding
the columns now — while every row has the same value — is trivially cheap and
avoids a later breaking migration.

**Current values in production:**

- `confidence = 1.0` — all current relations are user-authored `[[wikilinks]]`,
  which are ground truth.
- `source_method = "user_wikilink"` — parsed from markdown by `EntityParser`.

Future source methods: `"ai_extracted"` (future LLM pass),
`"ast_extracted"` (future tree-sitter code extraction),
`"manual_api"` (created via API without a wikilink).

**Files touched:**
[`src/memopad/models/knowledge.py`](../src/memopad/models/knowledge.py) —
`Relation` model columns;
[`src/memopad/schemas/base.py`](../src/memopad/schemas/base.py) —
`Relation` schema with `ge=0, le=1` validation;
[`src/memopad/schemas/response.py`](../src/memopad/schemas/response.py) —
`RelationResponse` model validator copy-list extended;
[`src/memopad/alembic/versions/h1b2c3d4e5f6_add_confidence_and_source_method_to_relation.py`](../src/memopad/alembic/versions/h1b2c3d4e5f6_add_confidence_and_source_method_to_relation.py) —
migration (idempotent, backfills existing rows);
[`tests/schemas/test_relation_confidence.py`](../tests/schemas/test_relation_confidence.py) —
11 tests covering defaults, range validation, dict + ORM paths.

---

## 6. Repo hygiene

### 6.1 Root-directory throwaway scripts — 💭 Pending review

The following live at the repo root and were flagged for cleanup:

- `run_assimilate_*.py` — 7 variants, only one is needed (`run_assimilate.py`).
- `fix_*.py` — 5 syntax-fixer scripts whose patches already landed.
- `inspect_*.py`, `scan_errors.py`, `merge_memopad_dumps.py`,
  `safe_clean_duplicates.py`, `check_*.py`.
- `verify_cache_optimizations.py`, `verify_optimizations.py` — superseded by
  pytest tests but still on disk.
- `memopad-main230226.zip`, `visual-explainer.zip` — binary artifacts that
  shouldn't be in a source tree.
- 3× `run_mcp_server*.py` — could collapse to one runner with `--transport`.

User has asked to defer this cleanup ("YES this will be reviewed later").

### 6.2 Speculative `getattr` audit — ✅ Done

AGENTS.md forbids `getattr(obj, "attr", default)` for guessing attribute names.
Audited the codebase; cleaned up sites I introduced. The remaining occurrences
fall into legitimate categories:

- SQLAlchemy column access (`getattr(self.Model, "project_id")`) — dynamic by
  design.
- Defensive exception inspection (`getattr(e, "response", None)`) — handles
  unknown error types from external libraries.
- Pydantic schema response builders that handle Optional fields.
- One genuine duck-typed boundary in `optimization_service.py` between
  `Project.path` and `ProjectConfig.home`. Documented in code; uses `hasattr`
  so it isn't blind guessing.

`recent_activity.py` has some `getattr`+`hasattr` patterns that look
cargo-culted but were left alone; cleanup is out of scope.

---

## 7. Testing footprint

| Area | Location |
|---|---|
| Assimilate tool & helpers | `tests/mcp/test_tool_assimilate.py`, `tests/test_assimilate_*` |
| Storage optimization | `tests/services/test_optimization_service.py`, `tests/mcp/test_tool_optimize_storage.py` |
| Embedding service helpers | `tests/services/test_embedding_service.py` |
| 2Q cache | `tests/cache/test_two_queue_cache.py` |
| Daily note helpers | `tests/mcp/test_tool_daily_note.py` |

---

## 8. Open work — ranked

1. End-to-end semantic search (sync hook + API endpoint + RRF wiring) — see §2.4.
2. Repo-root cleanup — see §6.1.
3. Snippet highlighting and column-weighted BM25 — see §2.2 / §2.3.
4. Move install guides to `docs/install/` and delete the per-topic plan files
   superseded by this document.
