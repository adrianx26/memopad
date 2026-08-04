"""Drop legacy (non-dim-scoped) ``embedding_vec_*`` vec0 tables from the app DB.

The canonical ``EmbeddingService._vec_table`` (see
``src/memopad/services/embedding_service.py``) names vec0 tables
``embedding_vec_<item_type>_p<project>_d<dim>`` — the ``_d<dim>`` suffix was
added so a model swap writes to a fresh table instead of rolling back the
canonical BLOB store on a wrong-dim insert. Tables created before that fix
match ``embedding_vec_<item_type>_p<project>`` (no ``_d<dim>``) and are stale:
the canonical code never reads or writes them, and on a model swap they can
shadow correct dim-scoped writes. ``memopad doctor --health`` flags them.

This script drops those legacy main virtual tables (sqlite-vec cascades their
``_info``/``_chunks``/``_rowids``/``_vector_chunksNN`` shadow tables) and leaves
the dim-scoped tables untouched. It refuses to run if any legacy table has
rows (a populated legacy table should be inspected, not silently dropped).

Usage:
    python scripts/cleanup_legacy_vec0.py            # drop empty legacy tables
    python scripts/cleanup_legacy_vec0.py --dry-run   # list only, drop nothing
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys

import sqlite_vec

from memopad.config import APP_DATABASE_NAME, DATA_DIR_NAME

_LEGACY_MAIN = re.compile(r"^embedding_vec_[a-z]+_p\d+(_d\d+)?$")
_DIM_SCOPED = re.compile(r"^embedding_vec_[a-z]+_p\d+_d\d+$")

DRY_RUN = "--dry-run" in sys.argv


def main() -> None:
    db_path = os.path.expanduser(f"~/{DATA_DIR_NAME}/{APP_DATABASE_NAME}")
    if not os.path.exists(db_path):
        print(f"DB not found: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)

    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    main_tables = [n for n in names if _LEGACY_MAIN.match(n)]
    legacy = [n for n in main_tables if not _DIM_SCOPED.match(n)]

    if not legacy:
        print("No legacy (non-dim-scoped) vec0 tables found. Nothing to do.")
        conn.close()
        return

    print(f"Found {len(legacy)} legacy vec0 main table(s):")
    populated = []
    for n in sorted(legacy):
        cnt = conn.execute(f"SELECT count(*) FROM '{n}'").fetchone()[0]
        print(f"  {n}: {cnt} rows")
        if cnt:
            populated.append((n, cnt))

    if populated:
        print("\nREFUSING to drop: some legacy tables are populated. Inspect "
              "manually before cleaning up:")
        for n, cnt in populated:
            print(f"  {n}: {cnt}")
        conn.close()
        sys.exit(1)

    if DRY_RUN:
        print("\n[DRY-RUN] Would drop the above empty legacy tables (and their "
              "sqlite-vec shadow tables). Re-run without --dry-run to apply.")
        conn.close()
        return

    print("\nDropping empty legacy tables...")
    for n in sorted(legacy):
        conn.execute(f"DROP TABLE IF EXISTS '{n}'")
        print(f"  dropped {n}")
    conn.commit()
    conn.close()
    print("Done. Dim-scoped vec0 tables are untouched.")


if __name__ == "__main__":
    main()