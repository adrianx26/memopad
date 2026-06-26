# Archived scratch scripts

One-off / superseded developer scripts that accumulated at the repo root during
early development. They are **not** part of the memopad package, are **not** imported
by anything under `src/`, `tests/`, or `cli/`, and are not referenced by CI.

Kept here (rather than deleted) for history and archaeology via `git log --follow`.
Do not add new scripts here — if you need a developer helper, put it in `scripts/`
with a clear name and a top-of-file comment explaining its purpose.

## Categories

- `run_assimilate*.py` — 7 experimental harnesses for the `assimilate` flow.
  `run_assimilate.py` is the canonical one; the rest are superseded variants.
- `fix_*.py` — one-shot syntax/encoding repair scripts, run once and obsolete.
- `run_mcp_server*.py` — 3 launch variants; the CLI (`memopad mcp`) is the
  supported entrypoint now.
- `inspect_*.py`, `scan_errors.py`, `merge_memopad_dumps.py`,
  `safe_clean_duplicates.py`, `check_*.py` — ad-hoc DB inspection / cleanup.
- `verify_cache_optimizations.py`, `verify_optimizations.py` — superseded by
  the proper test suites under `tests/cache/` and `tests/services/`.
- `test_fts5.py`, `migrate_path.py` — exploratory probes.