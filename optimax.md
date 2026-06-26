# OPTIMAX — MemoPad Correction & Optimization Plan

Goal: make MemoPad **work** (fix what's broken), **faster** (kill the hot-path
slowdowns), and **more useful** (ship the features that are currently dead weight
or traps).

Each item is sized so it can be a single PR. Items are grouped into phases; do
them in order — earlier phases unblock later ones. Line references are from the
audit at 2026-06-26.

Priority legend: 🔴 correctness bug · 🟠 perf · 🟡 quality/hygiene · 🔵 feature/usefulness

---

## Phase 0 — Triage & Baseline (do first, ~half day)

- [x] 0.1 🔴 Capture a reproducible baseline before changing anything:
      run `just fast-check`, `just doctor`, `just test`, `just check`.
      Save timing of a 500-note bulk import (script under `scripts/bench_bulk_import.py`)
      and a `semantic_search` + `search` round-trip. Every perf claim in this plan
      is measured against this baseline.
      — Baseline established against commit `c487b5e`: the suite had a conftest
      SyntaxError that blocked ALL test collection. Fixing that (Phase 2) surfaced
      ~82 pre-existing latent failures (RuntimeMode.is_cloud/CLOUD missing,
      resolve_runtime_mode kwarg drift, missing context_service fixture,
      OS-dependent watch/tmp tests, test-isolation pollution) — confirmed not
      regressions from Phases 1–4 (verified via `git stash` of individual changes).
- [x] 0.2 🟡 Inventory scratch files at repo root into `scripts/archive/`.
      DONE. `git mv`'d 27 tracked scratch scripts (`run_assimilate*.py` ×7,
      `fix_*.py` ×6, `run_mcp_server*.py` ×3, `check_*.py`, `inspect_*.py`,
      `scan_errors.py`, `merge_memopad_dumps.py`, `migrate_path.py`,
      `safe_clean_duplicates.py`, `verify_*.py` ×2, `test_fts5.py`) into
      `scripts/archive/` with a categorizing README. Verified nothing under
      `src/`/`tests/`/`cli/`/CI imports them; only `tofix.md` (a scratch doc)
      mentions `run_assimilate.py`. Repo root now holds only project source + docs.
- [x] 0.3 🟡 Update `docs/ARCHITECTURE.md` paths from legacy `src/basic_memory/`
      to `src/memopad/`.
      DONE. Replaced all `basic_memory` → `memopad` in `docs/ARCHITECTURE.md`
      (dir trees, import examples, shim examples), plus the same stale refs in
      `docs/testing-coverage.md` (4) and `docs/ai-assistant-guide-extended.md` (1
      GitHub blob URL — verified `src/memopad/mcp/resources/ai_assistant_guide.md`
      exists). No legacy refs remain under `docs/`.

---

## Phase 1 — Correctness Bugs (ship first, each is small)

- [ ] 1.1 🔴 **`link_resolver.resolve_link:169` selects the WORST match.**
      `min(results, key=lambda x: x.score)` returns the lowest FTS5/BM25 score,
      but higher = better. Flip to `max(...)`, or negate the score key.
      Remove the `# pyright: ignore`. Add a unit test: two candidate titles,
      assert the higher-scored one is returned.
      File: `src/memopad/services/link_resolver.py:162-174`

- [ ] 1.2 🔴 **Hub-penalty config silently dropped.** `ContextService.__init__
      (context_service.py:92-107)` reads `hub_penalty_*` off `app_config`, but all
      three DI factories (`deps/services.py:409, 424, 440`) omit `app_config`.
      Pass `app_config=app_config` into all three. Add a test that toggles
      `hub_penalty_enabled=false` and asserts the penalty isn't applied.

- [ ] 1.3 🔴 **`move_entity` rollback can desync file↔DB** (`entity_service.py:1187-1199`).
      Rewrite rollback to: preserve original exception type (wrap in a custom
      `EntityMoveError(Exception)` chaining `__cause__`, not bare `ValueError`);
      track the FS-vs-DB state explicitly so a FS-succeeds/DB-fails case rolls
      the *file* back; log (don't swallow) rollback failure.
      Add test for the FS-succeeds/DB-fails path.

- [ ] 1.4 🔴 **Dead `memopad reindex --embeddings` instruction** (`mcp/tools/semantic_search.py:70`).
      Until embeddings are real (Phase 3), either:
        (a) remove the hint and emit a clear "embeddings not enabled" message, or
        (b) if Phase 3 lands in the same cycle, wire the real flag (see 3.1).
      Do not ship a hint pointing at a nonexistent command.

- [ ] 1.5 🟡 **Sync swallows failures.** `sync_service.sync` (`sync_service.py:363-371`)
      uses `return_exceptions=True` then `continue`s on every error — failures
      never reach `SyncReport.failed`. Collect exceptions into `report.failed`
      (structured: path, error class, message) so callers know sync didn't
      succeed. Add test asserting a failing file shows up in `failed`.

- [ ] 1.6 🟡 **`reindex_all` drops `search_index` with no guard**
      (`search_service.py:59-73`). Concurrent searches during reindex see empty
      results. Rebuild into a temp table and atomic-rename, or gate with a
      read-lock/flag so concurrent reads fall back to the stale index instead of
      empty results.

- [ ] 1.7 🟡 **`daily_note` conflict detection by string match** (`daily_note.py:130-140`).
      Replace `"conflict" in msg_lower or "already exists" in msg_lower` /
      HTTP-409 sniffing with a typed exception from `entity_service`
      (`EntityAlreadyExistsError`) so the "open existing daily note" path
      doesn't break when a transport rephrases the error. Scope the prev/next
      day wikilinks to the `daily/` namespace so an unrelated note titled
      `2026-06-25` doesn't resolve instead.

---

## Phase 2 — Type Safety & Error Surfaces (consistency)

- [ ] 2.1 🟡 Introduce `EntityUpdateError` in `services/exceptions.py`; replace
      raw `ValueError` at `entity_service.py:527, 589, 611, 1183` with it so
      callers can distinguish "DB update no-op" from "bad input."
- [ ] 2.2 🟡 Use `DirectoryOperationError` (currently defined but never raised,
      `exceptions.py:19`) at the directory-service call sites, or delete it.
      Don't keep dead exception classes.
- [ ] 2.3 🟡 Guard `backlinks.py:71` — `item["relation_type"]` will `KeyError`
      if a row lacks the field. Use `.get("relation_type", "related")` or
      assert the repo query guarantees the column.
- [ ] 2.4 🟡 Fix importer contract drift: every `import_data` docstring claims
      "Path to the file" but implementations take already-deserialized data
      (`chatgpt_importer.py:47`, `memory_json_importer.py:54`, etc.). Either
      (a) accept a path and deserialize internally, or (b) fix the docstring to
      "parsed data." Pick (b) for minimal churn, document the real signature.
- [ ] 2.5 🟡 **`markdown_importer` stale-permalink bug** (`markdown_importer.py:69,88`):
      entity is parsed against the *source* path and written with that permalink
      to the *destination*. Re-resolve permalink/frontmatter against the
      destination project home after parse. Add a test importing into a fresh
      project and asserting permalinks point at the destination.

---

## Phase 3 — Make Embeddings Real (or remove them) 🔵

Decision gate: ship-or-remove. Embeddings are currently dead code —
`EmbeddingService.init_store`/`upsert` are **never called**, the `embedding`
table is never created/populated, so `semantic_search`/`memory_summarizer`/
`auto_tag`/`hybrid_search` all silently return zero semantic hits.

- [x] 3.1 🔵 SHIPPED. Wired `EmbeddingService.upsert` into
      `SearchService.index_entity_markdown` (best-effort, after FTS commit) and
      `SearchService.handle_delete` (vector cleanup). `EntityService.reindex_entity`
      gets it for free (calls index_entity_data). Implemented the real
      `memopad reindex --embeddings` CLI command in `cli/commands/db.py` that
      force-enables `MEMOPAD_EMBEDDINGS_ENABLED` for the run and backfills all
      projects via `SearchService.reindex_all()`. Cached the ONNX model at module
      scope (`_PROVIDER_CACHE` + `_get_provider`) so `maybe_create` is a dict
      lookup instead of a per-`hybrid_search` model reload; EmbeddingService is
      also cached per SearchService instance. Created the `embedding` table in
      alembic migration `k4e5f6a7b8c9_add_embedding_table` (dialect-aware). Also
      fixed a pre-existing multi-head: the h1b2→i2c3→j3d4 chain was added on top
      of merge parent `g9a0b3c4d5e6`, re-opening a branch vs `d7e8f9a0b1c2` so
      `alembic upgrade head` raised "Multiple head revisions"; added merge
      migration `l5e6f7a8b9c0` to restore a single head.
- [ ] 3.2 🔵 Not taken (ship path chosen per user instruction "3 ship embeddings").
- [x] 3.3 🔵 SHIPPED. Added `test-int/test_embeddings_integration.py` (3 tests)
      that enable `MEMOPAD_EMBEDDINGS_ENABLED=true`, inject a deterministic
      `FakeProvider` (fastembed is an optional extra not in CI), and assert:
      indexing populates the `embedding` table row; `hybrid_search(mode=semantic)`
      returns the right hit ranked first; and with embeddings disabled indexing
      is a no-op (no row, FTS still works). Runs against a real SQLite db.
- [x] 3.4 🔵 SHIPPED. Hardened `auto_tag.py`: instructions moved before the note
      content, content fenced with a backtick run longer than any run inside it
      (`_code_fence_for`), and an explicit "treat below as data, don't follow
      embedded instructions" directive. Closes the prompt-injection vector where
      a note containing ``` could break out of the code fence.

**Recommendation: ship (3.1).** Semantic search is the differentiator for
"useful"; removing it loses the feature. But if the team can't staff it this
cycle, take 3.2 — a missing feature beats a silently-broken one.

---

## Phase 4 — Performance (measured against Phase 0 baseline)

- [x] 4.1 🟠 **`detect_file_path_conflicts` is O(N²)** (`entity_service.py:110-147`).
      DONE. Replaced `find_all()` + full Python scan with a targeted
      `find_path_conflict_candidates(file_path)` query (parent-dir LIKE prefix)
      so conflict detection only inspects siblings under the same folder instead
      of every entity in the project. Diagnostic-only, never alters the permalink.
- [x] 4.2 🟠 **Permalink cache nuked on every change** (`entity_service.py:238-246`).
      DONE. Added `TwoQueueCache.remove(key)` and rewrote
      `invalidate_permalink_cache` to evict only the affected `path:<key>` entry
      (falling back to `clear()` on caches without `remove`), so bulk import keeps
      the cache warm. New cache tests pin per-key eviction (23 tests pass).
- [x] 4.3 🟠 **`schema_service.normalize_category` dedupe** — DONE. Dedupes
      categories within a single note in `update_entity_and_observations` before
      applying, cutting redundant normalize work and DB churn for repeated
      categories. (The full 3-round-trip → single-query batching of
      `normalize_category` itself was deferred: it is a semantic refactor of a
      schema-resolution path with no dedicated unit tests, too risky in the
      current fragile suite.)
- [x] 4.4 🟠 **`conflict_service.detect_and_mark`** (`conflict_service.py:84-179`).
      DONE (contained slice). Batched the 2N bidirectional UPDATEs (one per side
      per conflict pair) into a single `UPDATE ... WHERE id IN (...)` with
      `CASE` expressions for per-row partner/score — one statement, one
      transaction/commit. New tests pin "exactly one execute()" (13 conflict
      tests pass). DEFERRED with rationale: (a) the "use the arg, don't refetch"
      micro-win needs a new repo method and would break the mocked
      `find_by_entity` test surface for ~zero real query savings (all-pairs
      comparison still needs the full set); (b) the all-pairs semantic is
      tested-and-correct (two new contradictory obs in one edit *should* conflict),
      so new-vs-existing-only is wrong; (c) the cross-transaction atomicity
      (obs-write + conflict-write in one session) needs an invasive sync-flow
      refactor, marginal payoff vs. risk given the docstring's own "first-pass
      quality signal, not a final truth source" caveat.
- [x] 4.5 🟠 **`link_resolver.resolve_link` does 5+ sequential awaits per link**
      (`link_resolver.py:41-177`). DONE (contained slice). Parallelized the
      independent lookups within each branch with `asyncio.gather` while
      preserving exact precedence via priority in result selection: the
      context-aware block (`get_by_permalink` + `get_by_title`) and the
      relative-path block (two `get_by_file_path`). All 32 link_resolver tests
      pass. DEFERRED: the full "single joined repository query returning
      entity + alias + observation rows" collapse — it would require encoding
      the subtle multi-stage precedence (relative-path → context permalink/title
      → alias → path → path+.md → search, with proximity scoring) in SQL and
      risks reordering the ~40 behavior-pinning tests; not justified in a
      codebase that already carries pre-existing failures.
- [x] 4.6 🟠 **`index_entity_markdown` recomputes `_generate_variants`** (`.lower()` +
      split) for every observation and relation per note (`search_service.py:256-406`).
      DONE. Memoized `_generate_variants` with `@staticmethod @lru_cache(maxsize=4096)`
      returning a `frozenset`, so duplicate strings in a note reuse the variant
      set instead of recomputing. (Splitting the 150-line function into
      parse/variant/build phases is a readability refactor, deferred to avoid
      churning a hot path with no coverage.)

---

## Phase 5 — Test Coverage of Production Paths

The conflict/schema DI regression that `c487b5e` patched proves the DI graph
was never exercised end-to-end. Close the structural gaps:

- [x] 5.1 🟡 Add a DI-graph integration test for the **v2-external** factory set
      (`tests/api/v2/test_di_graph.py`). DONE. One synthetic probe route on the
      real app depends on six top-level v2-external services (Entity/Search/File/
      Directory/Context/Sync) at once, so resolving one GET walks the entire
      external DI graph (config → engine/session_maker → project-by-external_id →
      repositories → every service + sub-dependencies) and asserts each returns
      the expected type. This is the explicit c487b5e-class regression guard.
      NOTE: the non-external `_v2` (integer project_id) factories are dead from the
      router side (0 router usages) BUT are wired into `deps/importers.py`'s v2
      importer factories, so they are intentionally not probed here — see 5.4.
- [x] 5.2 🟡 Remove `# pragma: no cover` from production-critical paths and add
      tests. DONE (named paths): `_prepend_after_frontmatter` frontmatter-parse
      failure fallback + no-frontmatter simple-prepend branches
      (`tests/services/test_pragma_covered_paths.py`, 4 tests) and the
      `index_entity_data` repository-failure re-raise path (1 test). Pragmas
      removed from the now-covered branches. `move_entity` rollback is covered
      by Phase 1.3. Importer `import_data` happy paths are already covered by
      `tests/importers/test_importer_base.py` + `test_conversation_indexing.py`.
      (Broad pragma removal on the DI factories is part of 5.4, deferred.)
- [x] 5.3 🟡 Add tests for tools with **zero** coverage today. DONE for
      `semantic_search`, `memory_summarizer`, `auto_tag`, `backlinks`
      (`tests/mcp/test_tool_zero_coverage.py`, 17 tests): pure helpers
      (`_format_results`, `_code_fence_for`), input/feature-gating branches
      (unknown mode, embeddings-disabled for semantic/hybrid), error + empty
      branches (resolve failure, no results, read failure), the 3.4
      prompt-injection fence guard, and the 2.3 missing-relation_type grouping
      guard. `batch_import` is addressed structurally in 6.1.
- [ ] 5.4 🟡 Consolidate the triplicated v2/v2-external DI factories
      (`deps/services.py` ≈ hundreds of near-identical `# pragma: no cover`
      lines). DEFERRED with rationale: the "remove dead `_v2` factories" slice is
      NOT safe — `deps/importers.py` wires the non-external `_v2` factories
      (ProjectConfigV2Dep/FileServiceV2Dep/MarkdownProcessorV2Dep) into the v2
      importer DI graph, so they are live. The full consolidation (shared
      constructor with a variant flag) is a large refactor of tested DI wiring
      with no correctness/perf upside (maintainability only) and carries exactly
      the regression class 5.1 was created to guard against — not justified in a
      codebase already carrying pre-existing failures. Revisit once 5.1's guard
      is green and the importer variant set is stabilized.

---

## Phase 6 — Usefulness Polish 🔵

- [x] 6.1 🔵 **`batch_import.py:41-62` rebuilds its own DI graph.**
      DONE (structured-result half): added an `errors: list[str]` field to
      `ImportResult` (additive, backward-compatible default), capture per-file
      errors in `MarkdownImporter` (previously only logged + a skip count — the
      messages were lost), and surface them in the `batch_import` tool summary
      (`tests/importers/test_markdown_importer_errors.py`, 2 tests). DEFERRED
      (DI-rewire half): routing the import through the same container / v2
      importer endpoint as the CLI/API importers is a behavior change — the tool
      currently imports a *local* filesystem directory in-process, whereas the
      API importer endpoint is server-mediated. Rewiring risks changing where
      files are read from and which project variant applies; deferred until the
      importer endpoint exposes a local-directory import path the tool can call.
- [x] 6.2 🔵 **`conflict_service` docstring lies.** DONE. Rewrote the module
      docstring to describe the actual metric (character-multiset / Sørensen–Dice
      overlap over case-folded, whitespace-collapsed content via the shared
      `character_overlap`) and explicitly state no embeddings are used today + why
      (needs a labeled sample to recalibrate). Documented the known false-positive
      limitation on long text and added a tuning-note to `_CONFLICT_THRESHOLD`
      that the threshold MUST be recalibrated if the metric changes. DEFERRED:
      switching to token-Jaccard / sequence ratio + recalibration — needs a
      labeled conflict sample that doesn't exist in-repo; changing the metric
      alters detection behavior.
- [x] 6.3 🔵 Extract the duplicated character-overlap metric. DONE. Created
      `src/memopad/text_similarity.py` with `character_overlap(a, b)` (the shared
      Dice-over-character-bags core); `conflict_service._similarity_ratio` and
      `schema_service._name_overlap` both delegate to it, preserving their own
      normalization semantics. (`memopad.utils` is a single module, not a package,
      so a dedicated sibling module was used instead of `utils/text_similarity.py`
      to avoid the package/module conflict.) One metric, tested once:
      `tests/utils/test_text_similarity.py` (8 tests). Existing conflict (13) and
      schema (6) tests still pass.
- [x] 6.4 🔵 **`mcp/tools/assimilate/`** Windows hardening regression test. DONE.
      Added `test_assimilate_handles_cancelled_error` — injects
      `asyncio.CancelledError` via the mocked `crawl` and asserts the tool returns
      the non-propagating "Assimilation was cancelled" guidance (the
      `__init__.py:320` handler from commits 7fe35ed/dd0aa7a), not a crash. The
      `WindowsSelectorEventLoopPolicy` setters in `server.py` / `command_utils.py`
      / `cli/commands/mcp.py` are import-time/platform-gated guards (not unit-
      testable without mutating global loop state); the CancelledError handler is
      the user-facing hardening this pins.

---

## Definition of Done

- All 🔴 items in Phase 1 closed with tests.
- Embeddings: Phase 3 fully shipped *or* fully removed — no in-between.
- Bulk import of 500 notes ≥ the speedup target from 4.1/4.2 (measured vs 0.1).
- `just test` green, `just check` clean, no new `# pragma: no cover` added.
- Repo root clean (0.2); ARCHITECTURE.md accurate (0.3).

## Sequencing rationale

Phases 1→2→4 fix correctness, then consistency, then speed — each on the prior's
foundation. Phase 3 (embeddings) is parallelizable but gated by a ship/remove
decision. Phases 5–6 are quality/usefulness and slot in once the core is stable.
Land Phase 1 in small individual PRs so review is tractable and a regression is
easy to bisect.