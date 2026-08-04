# CHANGES — optimax.md execution (Phases 0–6)

This document describes every code change made while executing `optimax.md`,
organized by phase. Each entry lists the file(s), what changed, and why. The
plan itself (`optimax.md`) holds the per-item rationale and deferral notes; this
file is the code-side companion.

All work is currently **uncommitted** on `main`.

---

## Phase 0 — Triage & Baseline / repo hygiene

### `scripts/archive/` (new) + 27 `git mv` renames
Moved 27 tracked scratch scripts off the repo root into `scripts/archive/`:
`run_assimilate*.py` (×7), `fix_*.py` (×6), `run_mcp_server*.py` (×3),
`check_*.py`, `inspect_*.py`, `scan_errors.py`, `merge_memopad_dumps.py`,
`migrate_path.py`, `safe_clean_duplicates.py`, `verify_*.py` (×2), `test_fts5.py`.
Why: root held only throwaway developer scripts, none imported by `src/`/`tests/`/`cli/`/CI
(verified). `git mv` preserves history. Added `scripts/archive/README.md`
categorizing them.

### `docs/ARCHITECTURE.md`, `docs/testing-coverage.md`, `docs/ai-assistant-guide-extended.md`
Replaced all legacy `basic_memory` → `memopad` (dir trees, import examples, shim
examples, and a GitHub-blob URL in the AI-assistant guide — verified
`src/memopad/mcp/resources/ai_assistant_guide.md` exists). No legacy refs remain
under `docs/`.

---

## Phase 1 — Correctness bugs

### `src/memopad/services/context_service.py`
- Hub-penalty config (1.2): `ContextService` honors `app_config.hub_penalty_*`
  (enabled / weight / degree threshold). `app_config` is now injected by the DI
  factories (see `deps/services.py`); previously all three factories omitted it,
  so the penalty config was silently dropped.
- SQL correctness: the degree-query `IN (:entity_ids)` now uses
  `bindparams(bindparam("entity_ids", expanding=True))` with a `list` (not a
  `tuple`), the form SQLAlchemy requires for expanding `IN`-clause bind params
  under async.

### `src/memopad/deps/services.py`
- Wired `app_config` into all three `get_context_service` factories
  (base / v2 / v2-external) so the hub-penalty knobs actually reach the service (1.2).
- All three `get_search_service` factories now accept and forward
  `session_maker` + `project_id` so `SearchService` can lazily build an
  `EmbeddingService` and write semantic vectors during indexing (Phase 3).

### `src/memopad/services/entity_service.py`
- `move_entity` rollback (1.3): a DB update that returns `None` (no-op for a
  record that should be present) now raises typed `EntityUpdateError` (Phase 2.1)
  and rolls back the filesystem move, instead of leaving file↔DB desynced behind a
  bare `ValueError`.
- `detect_file_path_conflicts` (4.1, see Phase 4): now uses a targeted
  `find_path_conflict_candidates(file_path)` query instead of `find_all()` + a
  full Python scan. Diagnostic-only; never alters the permalink.
- `invalidate_permalink_cache` (4.2): evicts only the affected `path:<key>` entry
  via `TwoQueueCache.remove` (fallback `clear()`), so bulk import keeps the cache warm.
- `update_entity_and_observations` (4.3): dedupes categories within a single note
  before applying, cutting redundant normalize work / DB churn.
- Removed `# pragma: no cover` from the `_prepend_after_frontmatter` fallback
  branches (now covered by `tests/services/test_pragma_covered_paths.py`, Phase 5.2).

### `src/memopad/mcp/tools/semantic_search.py`
- Removed the dead `memopad reindex --embeddings` instruction that pointed at a
  command that didn't exist (1.4); the instruction now matches the real CLI added
  in Phase 3 (`memopad reindex --embeddings`).
- Embeddings-disabled guard: `mode="semantic"`/`"hybrid"` return an explicit
  install/enable hint instead of silently degrading to FTS.

### `src/memopad/sync/sync_service.py`
- Sync no longer swallows failures (1.5): a sync task that raises is recorded on
  `SyncReport.failed` as a `SyncFailure(path, error_class, message)` (new
  dataclass), not just logged and dropped. `SyncReportResponse` surfaces it
  (see `schemas/sync_report.py`).
- `get_sync_service` now constructs `SearchService` with `session_maker` + `project.id`
  (Phase 3 wiring).
- `tests/conftest.py` `sync_service` fixture updated to the new `SyncService`
  signature (passes `search_service` instead of the individual sub-services).

### `src/memopad/cli/commands/db.py`
- `reindex_all` guard (1.6): the destructive `search_index` drop is guarded.
- New `reindex --embeddings` command (Phase 3): force-enables
  `MEMOPAD_EMBEDDINGS_ENABLED`, checks the embeddings extra is installed (exits with
  a hint if not), prints a backfill notice, and runs `_reindex_all_projects(...,
  embeddings=True)` which reconciles + calls `search_service.reindex_all()` per project.

### `src/memopad/mcp/tools/daily_note.py`
- Conflict detection by string match (1.7) replaced with a typed
  `EntityAlreadyExistsError` (Phase 2.1) from `KnowledgeClient`, so the
  "already exists" branch no longer greps exception messages / HTTP status.
- Daily-note wikilinks are now scoped under the `daily/` namespace
  (`[[daily/YYYY-MM-DD]]`) so an unrelated note titled `YYYY-MM-DD` elsewhere in the
  vault doesn't resolve in place of the daily note.

---

## Phase 2 — Type safety & error surfaces

### `src/memopad/services/exceptions.py`
- New `EntityUpdateError` (2.1): distinct from `EntityCreationError` so callers can
  tell "create failed" / "update failed" / "bad input" (still `ValueError`) apart.
- New `EntityAlreadyExistsError(EntityCreationError)` (2.1): for duplicate-title /
  duplicate-permalink creates. Subclasses `EntityCreationError` so existing
  `except EntityCreationError` code keeps working, while routers/clients can map it
  to HTTP 409 instead of leaking as a 500.
- `DirectoryOperationError` docstring/usage tightened (2.2).

### `src/memopad/api/v2/routers/knowledge_router.py`
- `create_entity` catches `EntityAlreadyExistsError` and maps it to HTTP 409
  Conflict (was a 500 via the global handler), so clients branch on a typed
  status instead of string-matching.

### `src/memopad/mcp/clients/knowledge.py`
- `create_entity` unwraps `ToolError.__cause__` (the real httpx
  `HTTPStatusError`) and re-raises a 409 as typed `EntityAlreadyExistsError`, so
  tool callers (`daily_note`, `write_note`, `assimilate`) branch on a typed
  exception instead of grepping the message.

### `src/memopad/mcp/tools/write_note.py`
- Conflict branch now catches `EntityAlreadyExistsError` (typed) instead of
  substring-matching `"409"`/`"conflict"`/`"already exists"` (fragile across
  transports/locales). Removed the now-unneeded `# pragma: no cover` branches.

### `src/memopad/mcp/tools/backlinks.py`
- `relation_type` access guarded (2.3): rows missing `relation_type` default to
  `"related"` instead of `KeyError`-ing the whole tool. Unresolved rows are tagged.

### Importers — `base.py`, `chatgpt_importer.py`, `claude_conversations_importer.py`,
### `claude_projects_importer.py`, `memory_json_importer.py` (2.4)
- Corrected the `import_data` docstring contract drift: every importer's docstring
  claimed `source_path` (a file path) but most actually expect already-parsed /
  deserialized data (callers `json.load` first). Docstrings now describe the real
  contract per subclass. (`MarkdownImporter` is the one that genuinely takes a
  directory path.)

### `src/memopad/importers/markdown_importer.py` (2.5 + 6.1)
- Stale-permalink fix (2.5): the imported copy's permalink is re-resolved against
  the destination path so it doesn't carry a source-derived permalink pointing back
  at the source vault.
- Per-file error capture (6.1): failures inside the per-file loop are appended to a
  new `errors: list[str]` on the result (previously only logged + a skip count — the
  messages were lost). Surfaced by the `batch_import` tool.

### `src/memopad/schemas/sync_report.py`
- New `SyncFailureResponse` + `SyncReportResponse.failed` (1.5) so the API can
  report files whose sync task raised (not just skipped).

### `src/memopad/schemas/importer.py`
- Added `errors: List[str] = Field(default_factory=list)` to `ImportResult` (6.1,
  additive/backward-compatible) for per-item error capture.

---

## Phase 3 — Ship embeddings

### `src/memopad/services/embedding_service.py`
- Process-level model cache: module-level `_PROVIDER_CACHE` + `_get_provider()` /
  `reset_provider_cache()` so the ONNX model isn't reloaded per `hybrid_search` call.
- `EmbeddingService` lazy store init (`_ensure_store`), explicit commits on
  `init_store`/`upsert`/`delete`, and `maybe_create` takes `model_name`.

### `src/memopad/services/search_service.py`
- `SearchService.__init__` takes optional `session_maker`/`project_id` and lazily
  builds an `EmbeddingService` (`_get_embedding_service`).
- `index_entity_markdown` upserts an embedding after indexing
  (`_upsert_embedding`, best-effort, logs on failure); `handle_delete` deletes it.
- `hybrid_search` uses the embedding service for semantic/hybrid (RRF of BM25 +
  cosine), raising a clear `ValueError` if embeddings are unavailable.
- `_generate_variants` memoized with `@staticmethod @lru_cache(maxsize=4096)`
  returning a `frozenset` (4.6) — duplicate strings in a note reuse the variant set.
- Removed `# pragma: no cover` from the `index_entity_data` re-raise path (5.2,
  now tested).

### `src/memopad/deps/services.py`
- `get_search_service` (all three variants) forward `session_maker` + `project_id`.

### `src/memopad/sync/sync_service.py`
- `get_sync_service` constructs `SearchService(..., session_maker, project.id)`.

### `src/memopad/alembic/versions/k4e5f6a7b8c9_add_embedding_table.py` (new)
- Migration adding the `embedding` table (dialect-aware: Postgres BYTEA/
  timestamptz/now(); SQLite LargeBinary/String/datetime('now')), mirroring
  `EmbeddingService.init_store` exactly (no FKs).

### `src/memopad/alembic/versions/l5e6f7a8b9c0_merge_heads.py` (new)
- No-op merge migration restoring a single alembic head. The repo had a pre-existing
  multi-head (`d7e8f9a0b1c2` vs the `h1b2→i2c3→j3d4` chain added on merge-parent
  `g9a0`) that blocked `command.upgrade(config, "head")` — and thus the CLI
  reset/reindex paths. This merge closes it.

### `src/memopad/cli/commands/db.py`
- `reindex --embeddings` CLI backfill command (see Phase 1).

### `src/memopad/mcp/tools/auto_tag.py` (3.4 hardening)
- Prompt-injection guard: instructions moved BEFORE the note content; content is
  fenced with a backtick run longer than any run inside it (`_code_fence_for`),
  so a note containing a ``` block can't break out and inject instructions.

### `test-int/test_embeddings_integration.py` (new)
- 3 integration tests with a deterministic `FakeProvider`: indexing writes an
  `embedding` row, hybrid search returns the semantic hit, and disabled embeddings
  are a no-op for indexing.

---

## Phase 4 — Performance

### `src/memopad/repository/entity_repository.py` (4.1)
- New `find_path_conflict_candidates(file_path)`: computes the parent dir and runs a
  `LIKE` prefix query so conflict detection inspects only siblings under the same
  folder, not every entity in the project (was O(N²) over `find_all()`).

### `src/memopad/cache/two_queue_cache.py` (4.2)
- New `remove(key)` for targeted single-key eviction (the cache only had `clear()`),
  enabling per-key permalink-cache invalidation without nuking the warm cache.

### `src/memopad/services/entity_service.py` (4.1 / 4.2 / 4.3)
- See Phase 1/2 entries above (targeted conflict candidates, selective cache
  invalidation, within-note category dedupe).

### `src/memopad/services/conflict_service.py` (4.4)
- `detect_and_mark` all-pairs comparison unchanged (tested-and-correct: two new
  contradictory observations in one edit should conflict).
- `_write_conflicts` batched from 2N UPDATEs (one per side per conflict pair) into
  a single `UPDATE … WHERE id IN (...)` with `CASE` expressions for per-row
  partner/score — one statement, one transaction/commit.

### `src/memopad/services/link_resolver.py` (4.5)
- `resolve_link` parallelizes independent lookups with `asyncio.gather` while
  preserving exact precedence via priority in result selection: the context-aware
  block (`get_by_permalink` + `get_by_title`) and the relative-path block (two
  `get_by_file_path`). Imports `asyncio`.

### `src/memopad/services/search_service.py` (4.6)
- `_generate_variants` memoized (see Phase 3).

---

## Phase 5 — Test coverage

### `tests/api/v2/test_di_graph.py` (new, 5.1)
- DI-graph regression guard: one synthetic probe route on the real app depends on
  six top-level v2-external services at once, so one GET walks the entire external
  DI graph and asserts each returns the expected type. The explicit c487b5e-class
  guard.

### `tests/services/test_pragma_covered_paths.py` (new, 5.2)
- 5 tests for `_prepend_after_frontmatter` (no-frontmatter, valid-frontmatter,
  parse-failure fallback) and `index_entity_data` repository-failure re-raise.
  Pragmas removed from the now-covered source branches.

### `tests/mcp/test_tool_zero_coverage.py` (new, 5.3)
- 17 tests for previously-zero-coverage tools: `semantic_search`
  (`_format_results`, unknown-mode, embeddings-disabled), `memory_summarizer`
  (no-results, all-reads-fail, happy), `auto_tag` (`_code_fence_for`, error branch,
  fenced-content injection guard), `backlinks` (empty, resolve failure, grouping +
  unresolved marker + missing-relation_type guard).

### `tests/services/test_conflict_service.py` (5.2/4.4)
- New tests pinning the batched `_write_conflicts` (exactly one execute()) and the
  empty no-op.

### `tests/cache/test_two_queue_cache.py` (4.2)
- New tests for per-key `remove` from A1 / Am / missing key.

### `tests/services/test_link_resolver.py` (4.5)
- Existing 32 tests still pass under the `gather` change (results-only assertions).

### 5.4 — Deferred
- Consolidating the triplicated v2/v2-external DI factories. The non-external `_v2`
  factories are not dead (`deps/importers.py` wires them into the v2 importer
  graph); the full consolidation is a risky refactor of tested DI wiring with no
  correctness/perf upside. See `optimax.md` 5.4 for rationale.

---

## Phase 6 — Usefulness polish

### `src/memopad/text_similarity.py` (new, 6.3)
- Shared `character_overlap(a, b)` (Sørensen–Dice over character bags) — the core
  that `conflict_service._similarity_ratio` and `schema_service._name_overlap`
  both duplicated. (`memopad.utils` is a single module, not a package, so a
  dedicated sibling module was used instead of `utils/text_similarity.py`.)

### `src/memopad/services/conflict_service.py` (6.2 / 6.3)
- `_similarity_ratio` delegates to `character_overlap` (keeps its own
  case-fold + whitespace-collapse + equality short-circuit).
- Module docstring rewritten to describe the real metric and state that no
  embeddings are used today + why (needs a labeled sample to recalibrate); the
  long-text false-positive limitation and the threshold-recalibration requirement
  are documented on `_CONFLICT_THRESHOLD`.

### `src/memopad/services/schema_service.py` (6.3)
- `_name_overlap` is now a thin wrapper over `character_overlap`.

### `src/memopad/mcp/tools/batch_import.py` (6.1)
- Success summary now surfaces the per-file `errors` the importer collects
  (previously a lossy hardcoded string showing only the skip count). The DI-rewire
  half (route through the container / v2 importer endpoint) is deferred — see
  `optimax.md` 6.1.

### `tests/utils/test_text_similarity.py` (new, 6.3)
- 8 tests pinning the shared metric contract (one metric, tested once).

### `tests/importers/test_markdown_importer_errors.py` (new, 6.1)
- 2 tests: per-file error capture (write fails → error captured + skipped) and
  empty `errors` on a clean import.

### `tests/mcp/test_tool_assimilate.py` (6.4)
- `test_assimilate_handles_cancelled_error`: injects `asyncio.CancelledError` via
  the mocked `crawl` and asserts the tool returns the non-propagating cancellation
  guidance (the `assimilate/__init__.py` Windows hardening handler), not a crash.

### `tests/services/test_context_service.py` (1.2)
- Tests that the hub-penalty config is honored when enabled and not applied when
  disabled.

### `tests/schemas/test_sync_report.py` (new, 1.5)
- Tests for `SyncFailure` / `SyncReport.failed` surfacing.

### `tests/mcp/test_tool_daily_note.py` (1.7)
- Updated to assert the `daily/`-scoped wikilinks.

### `tests/services/test_entity_service.py` (1.3 / 2.1)
- `test_move_entity_rollback_on_database_failure` now asserts typed
  `EntityUpdateError` (was `ValueError`).

---

## Deferrals (documented in `optimax.md`)
- 4.4 cross-transaction obs/conflict atomicity; 4.5 single-joined-query collapse;
  5.4 DI-factory consolidation; 6.1 batch_import DI rewire; 6.2 metric recalibration.
  Each is high-risk / marginal-payoff in a codebase that already carries pre-existing
  failures (the two `test_entity_service` permalink-desync tests and the
  missing-`freezegun` collection error are pre-existing, not caused by this work).

---

## Phase 7 — Embedding service CPU/perf fixes

Driven by `embedding_service.py` consuming large amounts of CPU during indexing
and search. Five fixes; full detail in [`CHANGES-embedding-perf.md`](CHANGES-embedding-perf.md).

### `src/memopad/services/embedding_service.py` (Fix 1, 2, 5)
- **Fix 1 — off-loop inference + thread cap:** `FastEmbedProvider` accepts
  `num_threads` (passed to `fastembed.TextEmbedding(threads=...)`), default
  `min(4, cpu_count)` via `MEMOPAD_EMBEDDING_THREADS` (`0` = all cores). New
  `_embed()` runs `provider.embed` through `asyncio.to_thread`; `upsert_batch`
  and `similar` await it instead of calling ONNX sync on the event loop.
- **Fix 2 — content-hash dedup:** new `content_hash` column (SHA-256 of embedded
  text); `upsert_batch` skips re-embedding items whose text+model are unchanged
  (`_fetch_hashes` / `_content_hash`). Lazy `_ensure_content_hash_column`
  backfills the column on migration-skipping DBs.
- **Fix 5 — query cache:** bounded LRU `_QUERY_EMBED_CACHE` (256, keyed by
  `(model, query)`); `similar` serves repeat queries from cache.
  `reset_provider_cache()` clears it too.

### `src/memopad/alembic/versions/p9d1e2f3a4b5_add_embedding_content_hash.py` (new, Fix 2)
- Adds nullable `content_hash` to `embedding` (dialect-aware, idempotent).
  `down_revision = "o9c0d1e2f3a4"`; new single head.

### `src/memopad/db.py` (Fix 3a)
- `_load_sqlite_vec` now detects aiosqlite (coroutine `enable_load_extension`)
  and drives the load via `run_async` — vec0 was silently never loading on the
  async SQLite path (the sync branch was taken, the awaitable dropped). Outcome
  logged once (info on success, warning + install hint on failure) instead of a
  silent debug line.

### `src/memopad/services/search_service.py` (Fix 4)
- `SearchService` gains an opt-in embedding buffer
  (`begin_embedding_batch` / `flush_embedding_buffer`); `_upsert_entity_embeddings`
  buffers in batch mode (auto-flush at `BACKFILL_BATCH_DEFAULT`), embeds
  immediately otherwise.

### `src/memopad/sync/sync_service.py` (Fix 4)
- `sync()` wraps the parallel new+modified file sync in begin/flush (in a
  `finally`) so a sweep makes `ceil(total_items / 128)` model calls instead of
  one per file.

### `tests/services/test_embedding_service.py`
- `TestContentHashDedup` (4) + `TestQueryCache` (1) using a `_CountingProvider`.
  Embedding/search/reindex suites green (76 passed, 1 skipped). The 17
  `tests/sync/` failures are pre-existing (reproduce without these changes).