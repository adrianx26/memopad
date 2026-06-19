"""Storage optimization service for Memopad.

Detects and (optionally) merges duplicate notes within a project. Two notes are
considered duplicates when their *normalized* content is identical — frontmatter
is stripped, leading/trailing whitespace is removed, and line endings are
canonicalized to LF before hashing. This catches "same note saved twice with
different metadata" without flagging notes whose content happens to overlap.

The fix action is non-destructive by design:
  1. The first occurrence (sorted by mtime, oldest wins) is kept as canonical.
  2. Each duplicate's body is replaced with a single-line wikilink redirect
     pointing at the canonical permalink, preserving its frontmatter.

This means no data is ever truly deleted — the redirect leaves a breadcrumb
that sync will resolve into a relation. If the user wants the duplicate file
gone entirely they can `delete_note` it, but that's an explicit choice.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger


# --- Configuration ---

# Names that are never candidates for dedupe — they may have legitimate
# duplicates across directories (e.g. README.md per project subdir).
DEDUPE_SKIP_NAMES = frozenset({"readme.md", "index.md", ".gitignore"})

# Frontmatter delimiter — used to strip YAML before hashing.
FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)


# --- Data shapes ---


@dataclass
class StorageUsage:
    """Aggregate disk usage for a project."""

    total_files: int
    total_size: int
    avg_file_size: float
    largest_file_size: int
    largest_filename: str


@dataclass
class DuplicateGroup:
    """A set of files with identical normalized content."""

    content_hash: str
    canonical: str  # relative path of the keep-this-one file
    duplicates: list[str] = field(default_factory=list)  # other relative paths
    bytes_per_file: int = 0

    @property
    def reclaimable_bytes(self) -> int:
        """Bytes we'd save if every duplicate became a redirect stub."""
        # Stub size is small (~100 bytes); pretend it's zero for reporting clarity.
        return self.bytes_per_file * len(self.duplicates)


@dataclass
class OptimizationResult:
    """Outcome of an optimize() call."""

    processed_count: int
    duplicate_groups: list[DuplicateGroup]
    files_rewritten: int
    bytes_reclaimed: int
    skipped_files: list[str]
    errors: list[str]
    dry_run: bool

    @property
    def duplicate_count(self) -> int:
        return sum(len(g.duplicates) for g in self.duplicate_groups)


# --- Helpers ---


def _normalize_for_hash(text: str) -> str:
    """Strip YAML frontmatter and whitespace noise so cosmetic edits don't break dedup."""
    body = FRONTMATTER_RE.sub("", text, count=1)
    # Canonicalize line endings and trim trailing whitespace per line.
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    body = "\n".join(line.rstrip() for line in body.split("\n")).strip()
    return body


def _redirect_stub(canonical_relpath: str) -> str:
    """Build the body that replaces a duplicate's content."""
    # Use an explicit `redirects_to` relation so the link is semantically clear
    # to anyone (or any tool) reading the file later.
    permalink = canonical_relpath.replace(os.sep, "/").removesuffix(".md")
    return (
        "# (duplicate — redirected)\n\n"
        f"- redirects_to [[{permalink}]] (auto-merged by optimize_storage)\n"
    )


# --- Service ---


class StorageOptimizer:
    """Detects and merges duplicate notes within a project's filesystem."""

    def __init__(self, project_config):
        self.project_config = project_config
        # Project model exposes `path`; ProjectConfig exposes `home`. The two
        # attribute names are an existing wart in the codebase — this service
        # is the only place that needs to bridge them.
        project_path = (
            getattr(project_config, "path", None)
            if hasattr(project_config, "path")
            else getattr(project_config, "home", None)
        )
        if not project_path:
            raise ValueError(
                "Project configuration must expose either 'path' (Project model) "
                "or 'home' (ProjectConfig)"
            )
        self.project_path = Path(project_path)

    async def get_storage_usage(self) -> StorageUsage:
        """Walk the project root and tally per-file sizes."""
        logger.debug(f"Calculating storage usage for {self.project_config.name}")

        total_files = 0
        total_size = 0
        largest_file = 0
        largest_filename = ""

        for dirpath, _, filenames in os.walk(self.project_path):
            for filename in filenames:
                file_path = Path(dirpath) / filename
                try:
                    file_size = file_path.stat().st_size
                except OSError as e:
                    logger.warning(f"Error accessing {file_path}: {e}")
                    continue

                total_files += 1
                total_size += file_size
                if file_size > largest_file:
                    largest_file = file_size
                    largest_filename = str(file_path.relative_to(self.project_path))

        avg_size = total_size / total_files if total_files else 0
        return StorageUsage(
            total_files=total_files,
            total_size=total_size,
            avg_file_size=avg_size,
            largest_file_size=largest_file,
            largest_filename=largest_filename,
        )

    async def find_duplicates(self) -> list[DuplicateGroup]:
        """Return groups of markdown files with identical normalized content."""
        # hash → list of (relpath, mtime, size)
        buckets: dict[str, list[tuple[str, float, int]]] = {}

        for dirpath, _, filenames in os.walk(self.project_path):
            for filename in filenames:
                if not filename.endswith(".md"):
                    continue
                if filename.lower() in DEDUPE_SKIP_NAMES:
                    continue
                file_path = Path(dirpath) / filename
                relpath = str(file_path.relative_to(self.project_path))
                try:
                    raw = file_path.read_text(encoding="utf-8", errors="replace")
                    normalized = _normalize_for_hash(raw)
                    if not normalized:
                        # Skip empty notes — every blank file would otherwise
                        # collide with every other blank file.
                        continue
                    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
                    stat = file_path.stat()
                    buckets.setdefault(digest, []).append((relpath, stat.st_mtime, stat.st_size))
                except OSError as e:
                    logger.warning(f"Could not read {relpath}: {e}")

        groups: list[DuplicateGroup] = []
        for digest, items in buckets.items():
            if len(items) < 2:
                continue
            # Oldest mtime wins as canonical — that's typically the original
            # creation, with later copies being unintentional duplicates.
            items.sort(key=lambda t: t[1])
            canonical, _, size = items[0]
            duplicates = [relpath for relpath, _, _ in items[1:]]
            groups.append(
                DuplicateGroup(
                    content_hash=digest,
                    canonical=canonical,
                    duplicates=duplicates,
                    bytes_per_file=size,
                )
            )
        return groups

    async def optimize(self, dry_run: bool = True) -> OptimizationResult:
        """Find duplicates and (when dry_run=False) replace them with redirect stubs.

        Args:
            dry_run: If True (default), only reports what *would* change.
                     Set to False to actually rewrite duplicate files.

        Returns:
            OptimizationResult with the groups found and counts of changes.
        """
        logger.debug(
            f"Storage optimization for {self.project_config.name} (dry_run={dry_run})"
        )

        skipped: list[str] = []
        errors: list[str] = []
        rewritten = 0
        reclaimed = 0
        processed = 0

        # Tally the full count for the report — find_duplicates already did the
        # walk but we want a separate processed-files number for the user.
        for _, _, filenames in os.walk(self.project_path):
            processed += sum(1 for f in filenames if f.endswith(".md"))

        groups = await self.find_duplicates()

        if not dry_run:
            for group in groups:
                stub = _redirect_stub(group.canonical)
                for dup_relpath in group.duplicates:
                    dup_path = self.project_path / dup_relpath
                    try:
                        original_size = dup_path.stat().st_size
                        # Preserve frontmatter — only the body is replaced.
                        # This keeps tags/permalink/metadata intact so the DB
                        # record stays addressable until sync next runs.
                        raw = dup_path.read_text(encoding="utf-8", errors="replace")
                        fm_match = FRONTMATTER_RE.match(raw)
                        new_content = (fm_match.group(0) if fm_match else "") + stub
                        dup_path.write_text(new_content, encoding="utf-8")
                        rewritten += 1
                        reclaimed += max(0, original_size - len(new_content.encode("utf-8")))
                    except OSError as e:
                        msg = f"Failed to rewrite {dup_relpath}: {e}"
                        logger.error(msg)
                        errors.append(msg)

        return OptimizationResult(
            processed_count=processed,
            duplicate_groups=groups,
            files_rewritten=rewritten,
            bytes_reclaimed=reclaimed,
            skipped_files=skipped,
            errors=errors,
            dry_run=dry_run,
        )


def format_report(usage: StorageUsage, result: OptimizationResult, project_name: str) -> str:
    """Render usage + optimization result as a markdown report."""
    lines = [
        f"# Storage Optimization Report for {project_name}",
        "",
        f"_Mode: {'DRY RUN — no files modified' if result.dry_run else 'APPLIED — files rewritten'}_",
        "",
        "## Storage Usage",
        f"- Total files: {usage.total_files}",
        f"- Total size: {usage.total_size / (1024 * 1024):.2f} MB",
        f"- Average file size: {usage.avg_file_size / 1024:.2f} KB",
        f"- Largest file: {usage.largest_filename or '(none)'} "
        f"({usage.largest_file_size / 1024:.2f} KB)",
        "",
        "## Duplicates",
        f"- Duplicate groups: {len(result.duplicate_groups)}",
        f"- Duplicate files: {result.duplicate_count}",
    ]

    if result.duplicate_groups:
        potential = sum(g.reclaimable_bytes for g in result.duplicate_groups)
        lines.append(f"- Potential bytes to reclaim: {potential / 1024:.1f} KB")

    if result.dry_run and result.duplicate_groups:
        lines.append("")
        lines.append("_Re-run with `dry_run=false` to merge these duplicates into redirect stubs._")

    if not result.dry_run:
        lines.append("")
        lines.append(f"- Files rewritten: {result.files_rewritten}")
        lines.append(f"- Bytes reclaimed: {result.bytes_reclaimed / 1024:.1f} KB")

    if result.duplicate_groups:
        lines.append("")
        lines.append("## Duplicate groups")
        for i, group in enumerate(result.duplicate_groups, start=1):
            lines.append(f"### Group {i} (canonical: `{group.canonical}`)")
            for dup in group.duplicates:
                lines.append(f"- `{dup}`")
            lines.append("")

    if result.errors:
        lines.append("## Errors")
        for err in result.errors:
            lines.append(f"- {err}")

    return "\n".join(lines)
