# Data-Directory Migration + Doctor/Vec0 Cleanup + Tree Consolidation

Date: 2026-08-04
Branch: `claude/incremental-reindex` (commit `744ab98`, merged to local `main`;
push to `origin` pending user action — see end of file).

This pass resolved the long-standing **data-directory stranding**: the user's
real knowledge base lived at the legacy hidden `~/.memopad/` while the CLI and
MCP server read from the active `~/memopad/` (empty). It also lands a doctor
health-check fix and a legacy-vec0 cleanup, and repoints the editable install
to the canonical source tree.

## 1. Data-dir migration (the real KB is now in the active path)

**Architecture fact (verified):** memopad is **markdown-first**. Markdown files
on disk are the source of truth; the SQLite DB is a derived index rebuilt by
sync/reindex. Confirmed against the real corpus: entity `Overview` (permalink
`github.com/openclaw/openclaw/overview`) has exactly 2005 `- [category]`
observation lines in its markdown, matching its 2005 DB observations 1:1. The
`assimilate` tool writes every fact into the markdown.

**Starting state:**
- `~/.memopad/` (legacy): 40.5 MB `memory.db`, alembic `n8b9c0d1e2f3` (two
  migrations behind), **132 entities / 4171 observations / 41 relations**;
  `main/` (112 md) + `memopad-kb/` (6 md) project dirs.
- `~/memopad/` (active, CLI target): 0.6 MB `memory.db`, alembic
  `p9d1e2f3a4b5` (fully migrated schema), **0 entities**. The `main` markdown
  had already been moved here by the user (flattened from `~/.memopad/main/`
  into `~/memopad/`); `memopad-kb/` had not.

**Steps taken (non-destructive — no DB wipe, full backup first):**
1. Backed up the entire legacy dir to `~/.memopad.backup-20260804/` (274 MB).
2. Copied `~/.memopad/memopad-kb/` → `~/memopad-kb/` (preserved the 2nd project).
3. Added `memopad-kb` → `C:\Users\shobymik\memopad-kb` to `~/memopad/config.json`
   (the new config previously held only `main`).
4. Synced the already-moved markdown **additively** into the empty migrated DB
   via `scripts/migrate_kb_sync.py` (modeled on `db.py:_reindex_projects`, but
   without the DB drop that `reset --reindex` does). The script uses the
   Windows **Selector** event-loop policy (matching `run_with_cleanup`) to
   avoid the Python 3.13 + aiosqlite `IndexError: pop from an empty deque` that
   aborts runs under the default Proactor loop.
5. Repointed the `memopad-kb` project record to `~/memopad-kb` via
   `memopad project move` (file_paths are stored relative to the project root,
   so the repoint is path-independent — re-sync reported 0 drift).
6. Backfilled the search index + embeddings via `memopad reindex --embeddings`.

**Result (active `~/memopad/memory.db`, alembic `p9d1e2f3a4b5`):**
- **129 entities / 4171 observations / 41 relations** — observations and
  relations match the legacy DB **exactly**; entities are 3 fewer (the moved
  markdown set differs slightly from the legacy; markdown is the source of
  truth, so this is correct, not loss).
- **4341 embeddings** (129 entity + 4171 observation + 41 relation vectors), all
  in dim-scoped `embedding_vec_*_p{1,2}_d384` tables; `reindex_state`
  fingerprinted (129 rows); `embedding.content_hash` populated (4341 rows).
- `main` → `~/memopad`, `memopad-kb` → `~/memopad-kb`; no file↔DB drift.
- `memopad doctor --health` → **0 issues, exit 0**.
- Legacy `~/.memopad/` and `~/.memopad.backup-20260804/` kept intact as backup.

## 2. Doctor health-check fix (`src/memopad/cli/commands/doctor.py`)

`run_health_checks` check 1 inspected `reindex_state.content_hash`, but that
column does not exist on `reindex_state` — `content_hash` lives on the
**`embedding`** table (migration `p9d1e2f3a4b5`); `reindex_state`'s own
per-entity fingerprint column is **`fingerprint`** (migration `o9c0d1e2f3a4`).
The conflation made `doctor --health` report a permanent false "content_hash
missing" on every DB (this was 1 of the 16 issues doctor previously surfaced).

Split check 1 into:
- **1a.** `reindex_state` table exists with a `fingerprint` column → incremental
  reindex enabled.
- **1b.** `embedding` table has a `content_hash` column → embedding dedup
  enabled. A not-yet-created `embedding` table (embeddings never run) is
  treated as OK, not an error.

Check 2 (vec0 dim-scoping) is unchanged.

## 3. Legacy vec0 cleanup (`scripts/cleanup_legacy_vec0.py`)

The active DB had **15 legacy (non-dim-scoped) vec0 tables**
(`embedding_vec_<type>_p{1..5}`, no `_d<dim>` suffix), all **empty** (0 rows),
including orphan tables for `p3/p4/p5` projects that no longer exist. They were
created by the stale parent-tree code at some earlier point. The canonical
`EmbeddingService._vec_table` only ever reads/writes the dim-scoped
`_d{dim}` tables, so the legacy tables are dead weight that `doctor --health`
flags and that can shadow correct writes on a model swap.

`scripts/cleanup_legacy_vec0.py` drops empty legacy vec0 main tables
(sqlite-vec cascades the `_info`/`_chunks`/`_rowids`/`_vector_chunksNN`
shadows) and **refuses to drop any populated legacy table** (those need manual
inspection). Run once after migration; `--dry-run` lists first.

## 4. Two-tree consolidation (install repoint)

`pip show memopad` had the editable install pointing at the **frozen parent**
`C:\ANTI\memopad` (Jun 27, stale code: `search_service.py` 800 lines vs the
canonical 1158), so `import memopad` loaded old code and every `memopad` CLI
invocation ran stale binaries. Reinstalled editable from the canonical nested
tree `C:\ANTI\memopad\memopad` (`pip install -e "…\memopad[embeddings]"`):

- `import memopad` now resolves to `C:\ANTI\memopad\memopad\src\memopad\`.
- Installed version `0.0.1.dev42+b301086` (branch HEAD at the time).
- Installed the `embeddings` extra (fastembed + sqlite-vec) so semantic search
  works (per the no-functionality-loss / embeddings-required policy).
- The parent tree is **left on disk** as a fallback (not deleted).

## 5. Files

- `src/memopad/cli/commands/doctor.py` — check-1 split (1a fingerprint, 1b
  content_hash); docstring + `--health` help updated.
- `scripts/migrate_kb_sync.py` (new) — non-destructive markdown→DB sync.
- `scripts/cleanup_legacy_vec0.py` (new) — empty legacy vec0 table dropper.
- `tests/conftest.py`, `tests/cli/conftest.py` — pending test-infra fixes
  (alias_repository wiring; `clear_config_cache()` replacing a no-op
  `_CONFIG_CACHE = None`).
- `README.md` — corrected stale `~/.memopad/memopad.log` references to the
  current data dir (`~/memopad/`).
- Doc-drift sweep (all stale `~/.memopad` / `/app/.memopad` / `~\.memopad\`
  data-dir paths → `memopad`, no leading dot): `docs/Docker.md`,
  `docs/ai-assistant-guide-extended.md`, `docs/cloud-cli.md` (bisync-state,
  auth/token, config.json, .bmignore paths), `llms-install.md`,
  `test_mcp_server.md`, `plans/install-memopad-vscode.md`,
  `plans/install-memopad-to-antigravity.md` (incl. the Docker
  `%USERPROFILE%\memopad:/root/memopad` volume mount).
- **Intentionally NOT changed:** `CHANGELOG.md` (historical basic_memory→
  memopad rename entries; `.memopad` was the correct dir name at that release),
  `files/{README,QUICKSTART,INSTALL,MEMOPAD_ANALYSIS}.md` (a *separate*, older
  notes.json-based "Memopad MCP Server" product — its `.memopad/notes.json`
  paths belong to that product, not the current knowledge base), and the
  legacy-path references inside this very changelog.

## 6. Verification

- `memopad doctor --health` → 0 issues, exit 0 (reindex_state.fingerprint,
  embedding.content_hash, all vec0 dim-scoped).
- DB row counts: 129 entities / 4171 obs / 41 rels / 4341 embeddings.
- `memopad` CLI loads canonical code (`memopad.cli.main` from the nested tree).
- No file↔DB drift on either project after repoint + re-sync.

## Push (pending user action)

The local `main` and `claude/incremental-reindex` both carry commit `744ab98`.
Push to origin was not performed (outward-facing to the default branch; user
did not explicitly authorize). To publish:

```
cd C:\ANTI\memopad\memopad
git push origin main
git push origin claude/incremental-reindex
```

(If the user prefers the PR workflow instead, open a PR from
`claude/incremental-reindex` to `main` and merge there.)