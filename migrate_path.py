"""
Memopad — Data Directory Migration Script
==========================================
Migrates user data from the legacy hidden directory  (~/.memopad)
to the new visible directory  (~/memopad).

What it does:
  1. Detects whether the legacy .memopad directory exists
  2. Copies all contents to the new memopad directory (db, config.json, logs, etc.)
  3. Rewrites project paths inside config.json if they pointed inside ~/.memopad
  4. Prints a summary of what was done

Usage:
    python migrate_path.py [--dry-run]

Options:
    --dry-run   Show what would be done without making any changes.
"""

import json
import shutil
import sys
from pathlib import Path

# ──────────────────────────────────────────────────────────────
OLD_DIR_NAME = ".memopad"
NEW_DIR_NAME = "memopad"
CONFIG_FILE  = "config.json"
# ──────────────────────────────────────────────────────────────

DRY_RUN = "--dry-run" in sys.argv


def banner(msg: str) -> None:
    print(f"\n{'='*60}\n  {msg}\n{'='*60}")


def info(msg: str) -> None:
    prefix = "[DRY-RUN] " if DRY_RUN else ""
    print(f"  {prefix}{msg}")


def migrate() -> None:
    home = Path.home()
    old_dir = home / OLD_DIR_NAME   # C:\Users\shobymik\.memopad
    new_dir = home / NEW_DIR_NAME   # C:\Users\shobymik\memopad

    banner("Memopad Path Migration  v0.21")
    print(f"  Source : {old_dir}")
    print(f"  Target : {new_dir}")

    # ── 1. Check source exists ──────────────────────────────────
    if not old_dir.exists():
        print(f"\n✅  Nothing to migrate — {old_dir} does not exist.")
        print(   "    If this is a fresh install, memopad will use the new path automatically.")
        return

    # ── 2. Warn if target already exists ────────────────────────
    if new_dir.exists():
        print(f"\n⚠️   Target directory already exists: {new_dir}")
        print(   "    Existing files will NOT be overwritten (copy_function=shutil.copy2).")
        print(   "    New files from the old directory will be added.")

    # ── 3. Copy everything ──────────────────────────────────────
    banner("Step 1 — Copying files")
    if not DRY_RUN:
        new_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    skipped = 0
    for src_path in old_dir.rglob("*"):
        if src_path.is_dir():
            continue
        rel = src_path.relative_to(old_dir)
        dst_path = new_dir / rel
        if dst_path.exists():
            info(f"SKIP  (exists) {rel}")
            skipped += 1
        else:
            info(f"COPY  {rel}")
            if not DRY_RUN:
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dst_path)
            copied += 1

    print(f"\n  Copied: {copied}  |  Skipped: {skipped}")

    # ── 4. Rewrite config.json project paths ────────────────────
    banner("Step 2 — Updating config.json project paths")
    config_path = new_dir / CONFIG_FILE
    if not config_path.exists():
        info(f"No config.json found at {config_path}, skipping path rewrite.")
    else:
        with config_path.open("r", encoding="utf-8") as f:
            config = json.load(f)

        updated = False
        old_prefix = str(old_dir).replace("\\", "/")
        new_prefix = str(new_dir).replace("\\", "/")

        # Rewrite any project paths that pointed inside ~/.memopad
        projects = config.get("projects", {})
        for name, path in list(projects.items()):
            normalized = path.replace("\\", "/")
            if normalized.startswith(old_prefix):
                new_path = normalized.replace(old_prefix, new_prefix, 1)
                info(f"PROJECT '{name}':  {path}  →  {new_path}")
                projects[name] = new_path
                updated = True
            else:
                info(f"PROJECT '{name}': no change  ({path})")

        # Also rewrite the top-level database_url if it was a local path inside .memopad
        db_url = config.get("database_url") or ""
        if db_url and old_prefix in db_url.replace("\\", "/"):
            new_db_url = db_url.replace("\\", "/").replace(old_prefix, new_prefix, 1)
            info(f"database_url:  {db_url}  →  {new_db_url}")
            config["database_url"] = new_db_url
            updated = True

        if updated and not DRY_RUN:
            config["projects"] = projects
            with config_path.open("w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
            print("  ✅  config.json updated.")
        elif not updated:
            print("  ℹ️   No project paths required updating.")
        else:
            print("  [DRY-RUN] Would update config.json.")

    # ── 5. Summary ──────────────────────────────────────────────
    banner("Migration Complete")
    if DRY_RUN:
        print("  This was a DRY-RUN. No files were changed.\n")
        print("  Re-run without --dry-run to apply changes:")
        print("    python migrate_path.py")
    else:
        print(f"  ✅  Data migrated to: {new_dir}")
        print()
        print("  NEXT STEPS:")
        print("  1. Verify the new directory looks correct:")
        print(f"     dir {new_dir}")
        print()
        print("  2. Restart the memopad MCP server (or Claude Desktop).")
        print()
        print("  3. The old .memopad directory is left untouched.")
        print("     Once you've confirmed everything works, you can delete it:")
        print(f"     Remove-Item -Recurse -Force \"{old_dir}\"  (PowerShell)")
        print()


if __name__ == "__main__":
    migrate()
