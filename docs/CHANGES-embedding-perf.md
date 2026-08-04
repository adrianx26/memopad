# CHANGES — Embedding service CPU/perf fixes

This document describes the embedding-service performance work that reduces
the CPU cost of semantic search and indexing. Each entry lists the file(s),
what changed, and why. All work is **uncommitted** on `main`.

The motivating symptom: `embedding_service.py` consumed large amounts of CPU
during indexing and search. Root causes and the five fixes are below.

---

## Problem statement

`EmbeddingService` ran ONNX inference synchronously on the asyncio event loop
and re-embedded unchanged content on every re-index, so:

- every `upsert_batch` and every `similar()` **blocked the whole event loop**
  for the duration of inference, and onnxruntime's default thread count
  (one per logical CPU) **pegged every core** for a single call — the server
  froze during search/index;
- `reindex_all`/`index_entity` **re-embedded unchanged items** — repeated
  re-indexes burned the same CPU as the first;
- when `sqlite-vec` failed to load (common on the async SQLite path), every
  search **re-loaded and re-scored the whole project's vectors** in numpy, with
  no caching;
- the per-file sync path issued **one model call per file** instead of batching
  across files;
- repeat identical search queries paid full inference each time.

## Fix 1 — Offload `embed()` off the event loop + cap ONNX threads

### `src/memopad/services/embedding_service.py`

- `FastEmbedProvider.__init__` now accepts `num_threads` and passes it to
  `fastembed.TextEmbedding(threads=...)`, which bounds onnxruntime's
  `intra_op_num_threads`. Default capped to `min(4, cpu_count)` so a single
  embed no longer saturates every core; override with
  `MEMOPAD_EMBEDDING_THREADS` (`0` restores the legacy "all cores" behavior).
- New `_resolve_num_threads()` reads the env var.
- New `EmbeddingService._embed(texts)` runs `provider.embed` via
  `asyncio.to_thread`, so inference no longer blocks the event loop.
- `upsert_batch` and `similar` now `await self._embed(...)` instead of calling
  `provider.embed(...)` synchronously.

**Effect:** the FastAPI/MCP server stays responsive while a batch embeds; CPU
spikes during indexing/search are bounded.

## Fix 2 — Content-hash dedup for embeddings

### `src/memopad/services/embedding_service.py`

- New `content_hash` column on the `embedding` table (SHA-256 of the embedded
  text). `_init_blob_store` creates it; `_ensure_content_hash_column` +
  `_has_content_hash_column` backfill it on databases that skip the migration.
- New `_content_hash(text)` helper and `_fetch_hashes(keys)` lookup.
- `upsert_batch` now computes each item's hash, compares it (and the stored
  `model`) against what's already in the table, and **only embeds new/changed
  items**. Items whose text and model are unchanged are skipped entirely — no
  model call, no BLOB write, no vec0 rewrite. `content_hash` is written with
  the vector.

### `src/memopad/alembic/versions/p9d1e2f3a4b5_add_embedding_content_hash.py` (new)

- Migration adding the nullable `content_hash` column (dialect-aware,
  idempotent via `_has_column`). `down_revision = "o9c0d1e2f3a4"`; it is the
  new single head. Pre-existing rows have `NULL` and are re-embedded once on
  the next `upsert_batch`, then carry their hash forward.

**Effect:** a repeated re-index of unchanged content does zero model work; a
changed entity only re-embeds the items whose text actually changed (e.g. the
title vector, not its unchanged facts). Composes with the entity-level
incremental reindex (`reindex_state`), which already skips whole unchanged
entities — this adds item-level granularity inside the changed set.

## Fix 3a — Verify/diagnose sqlite-vec loading

### `src/memopad/db.py`

- `_load_sqlite_vec` was taking its sync branch for aiosqlite connections
  (`hasattr(conn, "enable_load_extension")` is true for aiosqlite — the method
  exists as a coroutine), so `sqlite_vec.load` returned an unawaited coroutine
  and vec0 **never loaded on the async SQLite path** — silently forcing the
  O(N) BLOB+numpy scorer. Fixed by switching on
  `inspect.iscoroutinefunction(...)`: aiosqlite now drives the underlying
  sqlite3 connection via `run_async` on its worker thread (the only correct
  path).
- The load outcome is now logged **once** (module-level `_SQLITE_VEC_STATUS_LOGGED`):
  `info` when vec0 loads, `warning` with an install hint
  (`pip install sqlite-vec`) when it doesn't — previously it was a silent
  `debug` line, so users never knew search was on the slow fallback.

**Effect:** vec0 ANN indexes actually load on SQLite where the extension is
installed → sublinear KNN instead of a per-query full matmul. Where it can't
load, the operator is told why instead of being silently on the slow path.

## Fix 4 — Batch across entities on the sync path

### `src/memopad/services/search_service.py`

- `SearchService` gains an opt-in embedding buffer:
  `begin_embedding_batch()` / `flush_embedding_buffer()` / `_flush_embedding_buffer()`,
  plus `_embedding_batch_mode` and `_embedding_buffer` state in `__init__`.
- `_upsert_entity_embeddings` appends items to the buffer (auto-flushing at
  `BACKFILL_BATCH_DEFAULT`) when batch mode is active; outside batch mode it
  embeds immediately as before (single-file updates, API writes).

### `src/memopad/sync/sync_service.py`

- `sync()` wraps the parallel new+modified file sync in `begin_embedding_batch()`
  / `flush_embedding_buffer()` (in a `finally`, so a task failure can't strand
  the buffer). Items from many files accumulate and flush in 128-item chunks.

**Effect:** a first-time sync of N files makes `ceil(total_items / 128)` model
calls instead of N. asyncio is single-threaded, so the buffer is safe under
the concurrent `asyncio.gather` of `sync_file` tasks.

## Fix 5 — LRU cache for query embeddings

### `src/memopad/services/embedding_service.py`

- Module-level bounded LRU `_QUERY_EMBED_CACHE` (max 256 entries, keyed by
  `(model_name, query)`) with `_cached_query_vec` / `_store_query_vec`.
- `similar()` checks the cache before embedding the query; on a miss it embeds
  (off-loop via `_embed`) and stores the result.
- `reset_provider_cache()` now also clears the query cache (tests/reloads).

**Effect:** repeated identical searches (common in UIs/MCP) skip the model
call entirely. Keyed by model so a model swap can't serve a stale vector.

---

## Tests

### `tests/services/test_embedding_service.py`

- `TestContentHashDedup` (4 tests): unchanged items skip embed; changed text
  re-embeds; model change re-embeds same text; a partial batch embeds only the
  changed item.
- `TestQueryCache` (1 test): a repeat query reuses the cached embedding; a new
  query embeds.
- All use a `_CountingProvider` (subclasses the existing deterministic
  `_FakeProvider`, counts `embed` calls) so no fastembed is required.

Full embedding + search + reindex suites remain green (76 passed, 1 skipped —
the skip is the vec0 path that requires the sqlite-vec extension).

## Pre-existing failures (not caused by this work)

The 17 failures in `tests/sync/` (permalink-on-move, watch-service, tmp-files)
reproduce identically with and without these changes — they are part of the
documented pre-existing baseline, not regressions from this work.

## Env var reference

| Variable | Default | Meaning |
|---|---|---|
| `MEMOPAD_EMBEDDINGS_ENABLED` | unset (off) | Gate the whole embeddings feature. |
| `MEMOPAD_EMBEDDING_THREADS` | `min(4, cpu_count)` | Cap on ONNX intra-op threads. `0` = let onnxruntime use all cores (legacy). |