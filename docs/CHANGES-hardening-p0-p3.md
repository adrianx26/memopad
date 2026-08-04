# CHANGES — Codebase hardening pass (P0–P3)

This document describes the prioritised fix pass over the whole codebase.
Each entry lists the file(s), what changed, and why. The audit was organised
into four tiers (P0 correctness/data-integrity → P3 test/infra polish). All
work is **uncommitted** on `claude/incremental-reindex`.

Guiding constraint: **do not break existing functionality or needed
workflows.** Additive fixes were preferred over rewrites; where a fix risked
churning behaviour that self-heals via the next sync, it was deferred (see
P2 #9 below).

A pre-existing test/pyright failure baseline was established first (see the
`pre-existing-failures-baseline` memory and the verification note at the end
of this file) so regressions could be told apart from pre-existing red.

---

## P0 — Cache invalidation + permalink ordering (data integrity)

Recurring root cause across P0: dead/missing cache-invalidation calls let the
permalink and metadata caches serve stale data after moves/deletes, and the
permalink resolver generated a new permalink before checking the DB for an
existing one.

### `src/memopad/services/entity_service.py` — `resolve_permalink`
- Added `explicit_permalink = bool(markdown and markdown.frontmatter.permalink)`
  and changed the cache lookup guard to
  `if not explicit_permalink and cache_key in self._permalink_cache:`.
  **Why:** an entity whose markdown carries an explicit `permalink:` in
  frontmatter must never be served from the permalink cache — the cache could
  hold a stale generated value from a previous, frontmatter-less write, which
  would silently override the user's explicit choice.

### `src/memopad/services/entity_service.py` — `create_entity`
- Wrapped parse → upsert → reconcile → checksum in `try:` with an
  `except Exception:` that best-effort deletes the just-written file
  (`await self.file_service.delete_file(file_path)`) and re-raises.
  **Why:** if upsert/reconcile raised after the file was written, the file
  was left on disk with no DB row (an orphan that the next sync would
  re-import, often with a different generated permalink). Cleaning it up on
  failure keeps file ↔ DB 1:1.
- Added permalink reconciliation: when the DB entity's permalink differs from
  the schema's generated `_permalink`, write the DB permalink back into the
  file's frontmatter and recompute the checksum.
  **Why:** the DB permalink is the source of truth (it went through the
  uniqueness-suffix loop); persisting it into the file makes the next sync a
  no-op instead of a re-link.

### `src/memopad/services/entity_service.py` — `move_entity`
- Track `wrote_new_permalink`; invalidate `destination_path` *before*
  resolving its permalink and `current_path` *after* the DB update succeeds.
  On rollback, if a new permalink was written, restore `old_permalink` into
  the file.
  **Why:** moving a file generates/updates a permalink, but the cache still
  held the old `path:<old>`/`path:<new>` entries. Without invalidation the
  resolver returned the stale permalink; without the rollback restore a
  failed move would leave the file with the new permalink while the DB still
  pointed at the old one.

### `src/memopad/sync/sync_service.py`
- `sync_markdown_file` / `sync_regular_file`: the four `get_file_metadata(path)`
  calls on the write path now pass `use_cache=False` (3 via replace_all + 1
  `_for_update`).
  **Why:** the metadata cache (60 s TTL) could return the pre-write mtime,
  so the checksum/modified check compared against stale metadata and skipped
  the write or mis-detected "modified".
- `handle_move`: invalidate the new path before `resolve_permalink` and the
  old path after `index_entity`.
- `handle_delete`: invalidate the deleted file's path after the entity is
  found.
  **Why:** moves/deletes left stale `path:` keys in the permalink cache;
  subsequent resolves for those paths returned ghosts.

---

## P1 — Embedding performance + correctness gaps

### `src/memopad/services/search_service.py` — flush serialisation & recovery
- Added `self._embedding_flush_lock = asyncio.Lock()`.
- Non-batch `_upsert_entity_embeddings` now runs `svc.upsert_batch(items)`
  under the lock.
- `flush_embedding_buffer`: rewrote to retry up to 2× while the buffer is
  non-empty; on failure it leaves the buffer for the next sync instead of
  `.clear()`-ing it, and logs if items remain.
- `_flush_embedding_buffer`: holds the flush lock; on exception it re-queues
  the un-flushed tail (`remaining + self._embedding_buffer`).
  **Why:** concurrent flushes could interleave ONNX inference and overflow the
  single asyncio thread, and a failed flush silently dropped the buffered
  embeddings (data loss). The lock caps concurrent inference to one; the
  re-queue guarantees no embedding is lost on a transient failure.

### `src/memopad/services/embedding_service.py` — `_vec_table`
- Now returns `f"embedding_vec_{item_type}_p{self.project_id}_d{dim}"` where
  `dim = self.provider.dim if self.provider else EMBEDDING_DIM_DEFAULT`.
  **Why:** vec0 virtual tables were created with `IF NOT EXISTS`, so after a
  model swap the old-dim table survived and a wrong-dim `INSERT` rolled back
  the canonical BLOB write — silently losing embeddings. Scoping the table
  name by dim means a new model creates its own table. Known minor
  limitation: same-dim model blends share a table (acceptable — a process
  runs one model at a time). Centralising in `_vec_table` keeps every call
  site consistent.

---

## P2 — Transaction gaps, orphan cleanup, project_id filtering

### `src/memopad/repository/observation_repository.py` — atomic replace
- New `replace_observations(entity_id, observations)`: one `db.scoped_session`,
  `delete` + `add_all` + `flush` + select-back in a single commit.
  **Why:** the old path committed the delete (separate session) then the add
  (another session); if the add raised, the entity was left with **zero**
  observations.
- `find_by_context` / `find_by_category` / `observation_categories`: wrapped
  the query in `self._add_project_filter(...)`.
  **Why:** these cross-project queries leaked observations from other
  projects into the result.

### `src/memopad/repository/relation_repository.py` — atomic replace
- New `replace_outgoing_relations(entity_id, relations) -> int`: one session,
  `delete(from_id == entity_id)` + dialect-specific `ON CONFLICT DO NOTHING`
  insert, returns the inserted count. Empty list → delete only, return 0.
  **Why:** same delete-then-add-on-separate-sessions gap as observations — a
  failure mid-replace left the entity with no outgoing relations.

### `src/memopad/services/entity_service.py` — switch to atomic replaces
- `update_entity_and_observations`: removed `delete_by_fields`; replaced
  `add_all` with `replace_observations(db_entity.id, observations)`.
- `update_entity_relations`: removed `delete_outgoing_relations_from_entity`
  + the `add_all`/`IntegrityError`-fallback loop; single
  `replace_outgoing_relations(db_entity.id, relations_to_add)` call.
  **Why:** adopt the atomic repo methods so observation/relation replacement
  can't half-commit.

### P2 #9 — `delete_entity` relation reordering: **deferred**
- The audit suggested deleting/rewriting incoming relations inside
  `delete_entity`. **Deferred:** both directions self-heal on the next sync
  (unresolved backlinks re-resolve), and reordering risks breaking existing
  tests. Honours the "don't break needed workflows" constraint.

### P2 #10 — covered above
- The `replace_observations` project-id normalisation (`_set_project_id_if_needed`)
  ensures inserted observations carry the right `project_id`.

---

## P3 — Test infra, router error handling, stale tree

### `tests/conftest.py` — `config_manager` fixture
- `config_module._CONFIG_CACHE = None` → `config_module.clear_config_cache()`.
  **Why:** the real cache is the module-level `_config_cache` instance; the
  `_CONFIG_CACHE` attribute never existed, so the assignment was a silent
  no-op that let stale configs leak across tests.
- `config_home / ".memopad"` → `config_home / DATA_DIR_NAME` (`"memopad"`),
  matching production `ConfigManager.__init__`.
  **Why:** the fixture wrote config to `.memopad` while `WatchService.handle_changes`
  constructed a fresh `ConfigManager()` reading `HOME/"memopad"` — a
  fixture/production dir mismatch that left 14 watch/tmp tests red with an
  empty config. This fix unblocks them (verified: `tests/sync/test_watch_service_reload.py`
  now 7/7 green).

### `tests/sync/test_watch_service_reload.py` — `test_run_handles_no_projects`
- `assert slept and slept[-1] == config.watch_project_reload_interval` →
  `assert config.watch_project_reload_interval in slept`.
  **Why:** membership (not last-element) so the reload-interval sleep is
  recognised regardless of which concurrent task (`_schedule_restart` timer
  vs. cycle body) calls `asyncio.sleep` first. Matches the sibling test's
  `assert 5 in slept` style.

### `src/memopad/api/v2/routers/knowledge_router.py`
- `edit_entity_by_id`, `move_directory`, `delete_directory`: added
  `except HTTPException: raise` before the generic `except Exception → 400`.
  **Why:** the broad handler collapsed intentional HTTP statuses (e.g. 404
  from a not-found check, 409 from a duplicate) into a generic 400, hiding
  the typed status from clients.
- `delete_entity_by_id`: removed the dead `search_service=Depends(lambda: None)`
  parameter and the `if search_service:` background-task block.
  **Why:** `entity_service.delete_entity` already calls
  `search_service.handle_delete(entity)` internally (it cleans the search
  index itself), so the router param was dead (`Depends(lambda: None)` is
  always `None`, and the block was `# pragma: no cover`).

### `src/memopad/api/v2/routers/importer_router.py`
- Both import endpoints: added `except HTTPException: raise` before the
  generic `except Exception → 500`.
  **Why:** preserve the intentional `HTTPException` raised on a failed
  import (`result.success is False`) instead of re-wrapping it and losing the
  original detail.

### P3 #13 — stale duplicate tree `C:\ANTI\memopad\` (parent of the repo)
- **Flagged, not deleted.** The parent directory `C:\ANTI\memopad\` holds a
  frozen (Jun 27) duplicate tree: its own `src/memopad/`, `pyproject.toml`,
  `tests/`, etc. The canonical, actively-edited tree is the nested
  `C:\ANTI\memopad\memopad\`. Confirmed `import memopad` from the canonical
  dir resolves to the **parent's** `src/memopad/` (the parent is what's
  pip-installed into the system Python), while pytest imports the canonical
  tree via `pyproject` `pythonpath = ["src", "tests"]`.
  **Action:** not hard-deleted (the parent also holds unrelated artifacts:
  `stoolap-main`, `plans/`, `docs/`, etc. that the user may want). Documented
  here so it's known. To run the CLI against the canonical tree, use
  `PYTHONPATH=src python -m memopad.cli.main …` (or install the canonical
  tree's package explicitly). Renaming the parent's `src/` aside is a safe
  future cleanup but was left to the user to avoid breaking anything that may
  still reference it.

---

## `memopad doctor` — schema health checks

### `src/memopad/cli/commands/doctor.py`
- New `run_health_checks()` (read-only, against `~/memopad/memory.db`):
  1. **`reindex_state` + `content_hash`** — the table exists and has the
     per-entity SHA-256 fingerprint column (incremental reindex). Missing ⇒
     incremental reindex is disabled / would re-embed everything.
  2. **vec0 dim-scoping** — every *main* `embedding_vec_*` virtual table
     matches `embedding_vec_<type>_p<project>_d<dim>`. sqlite-vec shadow
     tables (`_info`/`_chunks`/`_rowids`/`_vector_chunksNN`) are filtered out
     (anchored regex) so they aren't false-positived as "legacy".
- Cache invalidation is behavioural (exercised by the roundtrip), not schema,
  so it is not checked here.
- New `--health` flag on the `doctor` command: runs *only* the schema checks
  and exits non-zero if any issues are found.
- Default `memopad doctor` runs `run_health_checks()` first as a
  **best-effort** preamble (wrapped so a schema issue never aborts the
  functional roundtrip that follows).

When run against the user's real DB it surfaced 16 genuine, pre-existing
issues (15 legacy non-dim-scoped vec0 tables across 5 projects + a missing
`reindex_state.content_hash` column) — exactly the kind of regression the
doctor should catch.

---

## Verification

- All edited modules compile (`py_compile`): `entity_service`,
  `search_service`, `embedding_service`, `observation_repository`,
  `relation_repository`, `sync_service`, `knowledge_router`,
  `importer_router`, `doctor`, `conftest`, `test_watch_service_reload`.
- Targeted suites (areas touched) are green: `tests/sync/` +
  `tests/services/test_search_service.py` +
  `tests/services/test_entity_service.py` → **212 passed, 4 skipped, 0
  failed**.
- `tests/sync/test_watch_service_reload.py` → **7/7** (was red on the
  pre-existing baseline due to the conftest `.memopad` mismatch; now fixed).
- The 6 failures + 4 errors in `tests/api/` + `tests/services/`
  (`test_create_entity`, `test_entity_alias*`, `test_initialization*`,
  `test_context_service_hub_scoring*`) were confirmed **pre-existing** by
  stashing the P0–P3 changes and re-running on the clean committed HEAD —
  identical failures. They are part of the documented baseline, not
  regressions from this pass.
- `memopad doctor --health` runs and correctly reports the real DB's
  pre-existing schema issues (exit 1 on issues); `memopad doctor` (roundtrip)
  reports them as a best-effort warning and still passes (exit 0).

No commit has been made for this pass yet.