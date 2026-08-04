"""One-shot non-destructive KB population: sync markdown into the (empty,
already-migrated) active DB at ~/memopad/memory.db.

This is the additive alternative to `memopad reset --reindex` (which drops the
DB first). Because the active DB is empty and schema-migrated, we can sync the
project markdown straight into it — nothing is wiped. The legacy KB stays in
~/.memopad (and ~/.memopad.backup-20260804) as a backup.

Usage:
    python scripts/migrate_kb_sync.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from memopad import db
from memopad.config import ConfigManager
from memopad.repository import ProjectRepository
from memopad.services.initialization import reconcile_projects_with_config
from memopad.sync.sync_service import get_sync_service


async def main() -> None:
    app_config = ConfigManager().config
    await reconcile_projects_with_config(app_config)

    _, session_maker = await db.get_or_create_db(
        db_path=app_config.database_path,
        db_type=db.DatabaseType.FILESYSTEM,
    )
    project_repository = ProjectRepository(session_maker)
    projects = await project_repository.get_active_projects()

    print(f"Syncing {len(projects)} project(s) from markdown into active DB...")
    for project in projects:
        print(f"  - {project.name}: {project.path}")
        sync_service = await get_sync_service(project)
        report = await sync_service.sync(Path(project.path), project_name=project.name)
        total = getattr(report, "total", None)
        print(f"    sync total={total}")


if __name__ == "__main__":
    # Match the CLI's run_with_cleanup: use the Selector loop on Windows
    # (the Proactor loop + aiosqlite on Python 3.13 raises a spurious
    # "IndexError: pop from an empty deque" that aborts the run).
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        asyncio.run(main())
    finally:
        asyncio.run(db.shutdown_db())
    print("Sync complete.")