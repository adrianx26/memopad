"""Note builder factory for creating structured notes from crawl results."""

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .types import CrawlResult
from .config import DEFAULT_CONFIG, TYPE_COLORS


@dataclass(frozen=True)
class NoteBuilderConfig:
    """Configuration for building a specific note type."""

    content_type: str
    title: str
    description: str
    max_chars: int = DEFAULT_CONFIG.default_note_chars


def truncate_content(content: str | None, max_len: int = DEFAULT_CONFIG.max_note_content) -> str | None:
    """Truncate content to max_len with a marker if it exceeds the limit."""
    if content and len(content) > max_len:
        return content[:max_len] + "\n\n[... content truncated to fit size limit ...]"
    return content


def build_note(data: CrawlResult, config: NoteBuilderConfig) -> str | None:
    """Build a note from pages matching the content type."""
    sections = []
    for page in data["pages"]:
        if config.content_type in page.get("content_types", []):
            sections.append(f"## From: {page['url']}\n")
            sections.append(page["text"][: config.max_chars])
            sections.append("")

    if not sections:
        return None

    header = f"# {config.title}\n\n- [category] {config.description}\n\n"
    return truncate_content(header + "\n".join(sections))


# Note builder registry
NOTE_BUILDERS = [
    NoteBuilderConfig(
        content_type="agent_profile",
        title="Agent Profiles & System Prompts",
        description="Extracted agent profiles, system prompts, and AI instructions",
    ),
    NoteBuilderConfig(
        content_type="skills_rules",
        title="Skills, Rules & Workflows",
        description="Extracted skills definitions, rules files, and workflow patterns",
    ),
    NoteBuilderConfig(
        content_type="concepts",
        title="Concepts & Ideas",
        description="Extracted architectural concepts, design patterns, and ideas",
        max_chars=DEFAULT_CONFIG.concepts_chars,
    ),
    NoteBuilderConfig(
        content_type="soul_file",
        title="Soul Files & Identity",
        description="Extracted soul files, identity definitions, personality, values, and purpose statements",
    ),
    NoteBuilderConfig(
        content_type="tools_functions",
        title="Tools & Functions",
        description="Extracted tool definitions, function registrations, API endpoints, and handlers",
    ),
    NoteBuilderConfig(
        content_type="algorithms",
        title="Algorithms & Implementations",
        description="Extracted algorithm implementations, data structures, and computational logic",
    ),
    NoteBuilderConfig(
        content_type="decision_structure",
        title="Decision Structures",
        description="Extracted decision trees, state machines, routing logic, and control flow patterns",
    ),
]


def build_overview_note(start_url: str, data: CrawlResult) -> str:
    """Build the main overview note content."""
    lines = [
        f"# Assimilated: {start_url}\n",
        f"- [source] Source URL: {start_url}",
        f"- [stats] Pages processed: {len(data['pages'])}",
        f"- [stats] GitHub links found: {len(data['all_github_links'])}",
        f"- [stats] External links found: {len(data['all_external_links'])}",
        f"- [stats] Errors: {len(data['errors'])}",
        "",
        "## Pages/Files Processed\n",
    ]
    for page in data["pages"]:
        types_str = ", ".join(page["content_types"]) if page["content_types"] else "general"
        lines.append(f"- [{types_str}] {page['url']}")

    if data["errors"]:
        lines.append("\n## Errors\n")
        for err in data["errors"]:
            lines.append(f"- {err}")

    # Add a summary of the first page content (usually the main README)
    if data["pages"]:
        readme = next(
            (p for p in data["pages"] if "readme" in p["url"].lower()),
            data["pages"][0],
        )
        first_text = readme["text"][: DEFAULT_CONFIG.overview_chars]
        lines.append(f"\n## Main Content Summary ({readme['url']})\n")
        lines.append(first_text)

    return truncate_content("\n".join(lines)) or ""


def build_functional_diagram_note(data: CrawlResult) -> str | None:
    """Build a Mermaid flowchart diagram of the assimilated content structure."""
    if not data["pages"]:
        return None

    nodes = []
    edges = []
    styles = []

    # Build a node for each page
    for i, page in enumerate(data["pages"]):
        url = page["url"]
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        label = path.split("/")[-1] if path else parsed.netloc
        if not label:
            label = url[:40]
        # Sanitize label for Mermaid
        label = re.sub(r'["\'\[\]{}()<>|&;]', "", label)
        if len(label) > 40:
            label = label[:37] + "..."

        node_id = f"N{i}"
        types_str = ", ".join(page.get("content_types", [])) or "general"
        nodes.append(f'    {node_id}["{label}\\n({types_str})"]')

        # Style by primary content type
        ctypes = page.get("content_types", [])
        if ctypes:
            primary_type = ctypes[0]
            if primary_type in TYPE_COLORS:
                color = TYPE_COLORS[primary_type]
                styles.append(f"    style {node_id} fill:{color},color:#fff")

    # Build edges: connect files in the same directory hierarchy
    dir_groups: dict[str, list[int]] = {}
    for i, page in enumerate(data["pages"]):
        parsed = urlparse(page["url"])
        parts = parsed.path.strip("/").split("/")
        parent_dir = "/".join(parts[:-1]) if len(parts) > 1 else "root"
        dir_groups.setdefault(parent_dir, []).append(i)

    # Connect first node in each group to other nodes in same group
    for indices in dir_groups.values():
        if len(indices) > 1:
            root_idx = indices[0]
            for child_idx in indices[1:]:
                edges.append(f"    N{root_idx} --> N{child_idx}")

    # Also connect root-level items to first sub-items
    root_indices = dir_groups.get("root", [])
    other_roots = [
        idx_list[0] for key, idx_list in dir_groups.items() if key != "root" and idx_list
    ]
    if root_indices and other_roots:
        for sub_root in other_roots[:10]:  # Limit connections to avoid clutter
            edges.append(f"    N{root_indices[0]} --> N{sub_root}")

    # Build the Mermaid block
    mermaid_lines = ["graph TD"]
    mermaid_lines.extend(nodes[:50])  # Cap at 50 nodes for readability
    mermaid_lines.extend(edges[:80])  # Cap edges
    mermaid_lines.extend(styles[:50])

    mermaid_block = "\n".join(mermaid_lines)

    # Build legend
    legend_lines = ["\n## Legend\n"]
    for ctype, color in TYPE_COLORS.items():
        legend_lines.append(f"- **{ctype}**: `{color}`")

    # Count unique content categories
    all_types = set()
    for page in data["pages"]:
        all_types.update(page.get("content_types", []))

    lines = [
        "# Functional Diagram\n",
        "- [category] Auto-generated schematic of assimilated content structure\n",
        f"- [stats] Total components: {len(data['pages'])}",
        f"- [stats] Content categories: {len(all_types)}\n",
        "```mermaid",
        mermaid_block,
        "```",
    ]
    lines.extend(legend_lines)

    return truncate_content("\n".join(lines))


def build_github_links_note(data: CrawlResult) -> str | None:
    """Build the GitHub links index note."""
    if not data["all_github_links"]:
        return None

    lines = [
        "# GitHub Links Index\n",
        "- [category] All GitHub links discovered during assimilation\n",
    ]
    for link in data["all_github_links"]:
        lines.append(f"- {link}")

    return truncate_content("\n".join(lines))


def build_all_notes(start_url: str, data: CrawlResult) -> list[tuple[str, str]]:
    """Build all notes from crawl data."""
    notes: list[tuple[str, str]] = []

    # Overview note
    overview = build_overview_note(start_url, data)
    notes.append(("Overview", overview))

    # Type-specific notes
    for config in NOTE_BUILDERS:
        note = build_note(data, config)
        if note:
            notes.append((config.title, note))

    # Functional diagram
    diagram = build_functional_diagram_note(data)
    if diagram:
        notes.append(("Functional Diagram", diagram))

    # GitHub links index
    github_note = build_github_links_note(data)
    if github_note:
        notes.append(("GitHub Links Index", github_note))

    return notes
