"""Daily note tool for Memopad MCP server.

Creates or opens a journal entry for a given date under `daily/<YYYY-MM-DD>.md`.
Auto-links to the previous and next day's notes so the daily timeline forms an
explicit chain inside the knowledge graph.
"""

from datetime import date, datetime, timedelta
from typing import Optional

from fastmcp import Context
from loguru import logger

from memopad.mcp.async_client import get_client
from memopad.mcp.project_context import add_project_metadata, get_active_project
from memopad.mcp.server import mcp
from memopad.schemas.base import Entity
from memopad.services.exceptions import EntityAlreadyExistsError


# --- Configuration ---

DAILY_DIRECTORY = "daily"
DAILY_TAG = "daily"


def _parse_date(value: str | None) -> date:
    """Parse a date string. Accepts ISO (YYYY-MM-DD), 'today', 'yesterday', 'tomorrow'.

    Empty / None returns today.
    """
    if not value or value.lower() == "today":
        return date.today()
    if value.lower() == "yesterday":
        return date.today() - timedelta(days=1)
    if value.lower() == "tomorrow":
        return date.today() + timedelta(days=1)
    return datetime.strptime(value, "%Y-%m-%d").date()


def _build_default_template(d: date) -> str:
    """Build the default daily note body.

    Wikilinks to the previous and next day form the timeline chain — the
    target notes don't need to exist yet; sync resolves them when they do. The
    links are scoped under the ``daily/`` namespace (``[[daily/YYYY-MM-DD]]``) so
    an unrelated note titled ``YYYY-MM-DD`` elsewhere in the vault doesn't
    accidentally resolve in place of the daily note.
    """
    prev_day = (d - timedelta(days=1)).isoformat()
    next_day = (d + timedelta(days=1)).isoformat()
    pretty = d.strftime("%A, %B %d, %Y")
    return (
        f"# {pretty}\n\n"
        f"- [category] Daily journal entry for {d.isoformat()}\n"
        f"- previous_day [[{DAILY_DIRECTORY}/{prev_day}]]\n"
        f"- next_day [[{DAILY_DIRECTORY}/{next_day}]]\n\n"
        "## Notes\n\n"
        "## Decisions\n\n"
        "## Tomorrow\n"
    )


@mcp.tool(
    description=(
        "Create or open a daily journal note. The note lives under daily/YYYY-MM-DD.md "
        "and auto-links to the previous and next day to form a timeline chain."
    ),
)
async def daily_note(
    date_str: Optional[str] = None,
    project: Optional[str] = None,
    template: Optional[str] = None,
    context: Context | None = None,
) -> str:
    """Create or open today's (or any day's) journal note.

    Args:
        date_str: ISO date (YYYY-MM-DD) or one of "today" / "yesterday" / "tomorrow".
                  Defaults to today.
        project: Project name. Optional — server resolves the default.
        template: Optional override for the note body. The default template includes
                  prev/next day wikilinks and Notes/Decisions/Tomorrow sections.
        context: Optional FastMCP context.

    Returns:
        Markdown summary including the note's permalink and whether it was just
        created or already existed.

    Examples:
        # Open today's note
        daily_note()

        # Open a specific day
        daily_note(date_str="2026-04-15")

        # Open yesterday's note in a specific project
        daily_note(date_str="yesterday", project="journal")
    """
    try:
        target_date = _parse_date(date_str)
    except ValueError:
        return (
            f"# Error\n\nInvalid date '{date_str}'. "
            "Expected YYYY-MM-DD or one of: today, yesterday, tomorrow."
        )

    title = target_date.isoformat()
    body = template if template is not None else _build_default_template(target_date)

    async with get_client() as client:
        active_project = await get_active_project(client, project, context)
        logger.info(f"daily_note: project={active_project.name} date={title}")

        from memopad.mcp.clients import KnowledgeClient

        knowledge_client = KnowledgeClient(client, active_project.external_id)

        entity = Entity(
            title=title,
            directory=DAILY_DIRECTORY,
            entity_type="journal",
            content_type="text/markdown",
            content=body,
            entity_metadata={"tags": [DAILY_TAG, target_date.strftime("%Y-%m")]},
        )

        # Trigger: optimistic create. Conflict means the note already exists.
        # Why: avoids a separate read-then-write round trip on the happy path.
        # Outcome: on a 409 (typed EntityAlreadyExistsError from the client), we
        # surface the existing note's permalink without overwriting it.
        action = "Created"
        try:
            result = await knowledge_client.create_entity(entity.model_dump(), fast=False)
        except EntityAlreadyExistsError:
            if not entity.permalink:
                raise
            entity_id = await knowledge_client.resolve_entity(entity.permalink)
            result = await knowledge_client.get_entity(entity_id)
            action = "Opened existing"

        summary = [
            f"# {action} daily note: {title}",
            f"project: {active_project.name}",
            f"file_path: {result.file_path}",
            f"permalink: {result.permalink}",
            f"day: {target_date.strftime('%A, %B %d, %Y')}",
            f"previous: [[{DAILY_DIRECTORY}/{(target_date - timedelta(days=1)).isoformat()}]]",
            f"next: [[{DAILY_DIRECTORY}/{(target_date + timedelta(days=1)).isoformat()}]]",
        ]
        return add_project_metadata("\n".join(summary), active_project.name)
