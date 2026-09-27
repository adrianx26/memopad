#!/usr/bin/env python3
"""Fail when Python files start with a UTF-8 byte-order mark."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
EXCLUDED_DIRECTORIES = {".git", ".venv", "venv", "__pycache__", "build", "dist"}


def main() -> int:
    """Check repository Python files for a leading UTF-8 BOM."""
    files = (
        path
        for path in ROOT.rglob("*.py")
        if not EXCLUDED_DIRECTORIES.intersection(path.relative_to(ROOT).parts)
    )
    affected = []
    for path in files:
        with path.open("rb") as source:
            if source.read(3) == b"\xef\xbb\xbf":
                affected.append(path.relative_to(ROOT))

    if affected:
        for path in affected:
            print(f"UTF-8 BOM found: {path}", file=sys.stderr)
        print(f"Found {len(affected)} Python file(s) with a UTF-8 BOM.", file=sys.stderr)
        return 1

    print("No Python files with a leading UTF-8 BOM.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
