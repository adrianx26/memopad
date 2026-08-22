<!-- mcp-name: io.github.basicmachines-co/memopad -->
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![PyPI version](https://badge.fury.io/py/memopad.svg)](https://badge.fury.io/py/memopad)
[![Version](https://img.shields.io/badge/version-0.21.3-blue.svg)](#memopad)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://github.com/adrianx26/memopad/workflows/Tests/badge.svg)](https://github.com/adrianx26/memopad/actions)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
![](https://badge.mcpx.dev?type=server 'MCP Server')
![](https://badge.mcpx.dev?type=dev 'MCP Dev')


# MemoPad

MemoPad lets you build persistent knowledge through natural conversations with Large Language Models (LLMs) like
Claude, while keeping everything in simple Markdown files on your computer. It uses the Model Context Protocol (MCP) to
enable any compatible LLM to read and write to your local knowledge base.

- Website: https://memopad.xxx
- Documentation: https://docs.memopad.xxx

## Pick up your conversation right where you left off

- AI assistants can load context from local files in a new conversation
- Notes are saved locally as Markdown files in real time
- No project knowledge or special prompting required


## Quick Start

```bash
# Install with uv (recommended)
uv tool install memopad

# Verify the install works (must print "MemoPad version: …", not a traceback)
memopad --version

# Configure Claude Desktop (edit ~/Library/Application Support/Claude/claude_desktop_config.json)
# Add this to your config:
{
  "mcpServers": {
    "memopad": {
      "command": "uvx",
      "args": [
        "memopad",
        "mcp"
      ]
    }
  }
}
# Now in Claude Desktop, you can:
# - Write notes with "Create a note about coffee brewing methods"
# - Read notes with "What do I know about pour over coffee?"
# - Search with "Find information about Ethiopian beans"

```

You can view shared context via files in `~/memopad` (default directory location).

## Why MemoPad?

Most LLM interactions are ephemeral - you ask a question, get an answer, and everything is forgotten. Each conversation
starts fresh, without the context or knowledge from previous ones. Current workarounds have limitations:

- Chat histories capture conversations but aren't structured knowledge
- RAG systems can query documents but don't let LLMs write back
- Vector databases require complex setups and often live in the cloud
- Knowledge graphs typically need specialized tools to maintain

MemoPad addresses these problems with a simple approach: structured Markdown files that both humans and LLMs can
read
and write to. The key advantages:

- **Local-first:** All knowledge stays in files you control
- **Bi-directional:** Both you and the LLM read and write to the same files
- **Structured yet simple:** Uses familiar Markdown with semantic patterns
- **Traversable knowledge graph:** LLMs can follow links between topics
- **Standard formats:** Works with existing editors like Obsidian
- **Lightweight infrastructure:** Just local files indexed in a local SQLite database

With MemoPad, you can:

- Have conversations that build on previous knowledge
- Create structured notes during natural conversations
- Have conversations with LLMs that remember what you've discussed before
- Navigate your knowledge graph semantically
- Keep everything local and under your control
- Use familiar tools like Obsidian to view and edit notes
- Build a personal knowledge base that grows over time
- Sync your knowledge to the cloud with bidirectional synchronization
- Authenticate and manage cloud projects with subscription validation
- Mount cloud storage for direct file access

## How It Works in Practice

Let's say you're exploring coffee brewing methods and want to capture your knowledge. Here's how it works:

1. Start by chatting normally:

```
I've been experimenting with different coffee brewing methods. Key things I've learned:

- Pour over gives more clarity in flavor than French press
- Water temperature is critical - around 205°F seems best
- Freshly ground beans make a huge difference
```

... continue conversation.

2. Ask the LLM to help structure this knowledge:

```
"Let's write a note about coffee brewing methods."
```

LLM creates a new Markdown file on your system (which you can see instantly in Obsidian or your editor):

```markdown
---
title: Coffee Brewing Methods
permalink: coffee-brewing-methods
tags:
- coffee
- brewing
---

# Coffee Brewing Methods

## Observations

- [method] Pour over provides more clarity and highlights subtle flavors
- [technique] Water temperature at 205°F (96°C) extracts optimal compounds
- [principle] Freshly ground beans preserve aromatics and flavor

## Relations

- relates_to [[Coffee Bean Origins]]
- requires [[Proper Grinding Technique]]
- affects [[Flavor Extraction]]
```

The note embeds semantic content and links to other topics via simple Markdown formatting.

3. You see this file on your computer in real time in the current project directory (default `~/$HOME/memopad`).

- Realtime sync can be enabled via running `memopad sync --watch`

4. In a chat with the LLM, you can reference a topic:

```
Look at `coffee-brewing-methods` for context about pour over coffee
```

The LLM can now build rich context from the knowledge graph. For example:

```
Following relation 'relates_to [[Coffee Bean Origins]]':
- Found information about Ethiopian Yirgacheffe
- Notes on Colombian beans' nutty profile
- Altitude effects on bean characteristics

Following relation 'requires [[Proper Grinding Technique]]':
- Burr vs. blade grinder comparisons
- Grind size recommendations for different methods
- Impact of consistent particle size on extraction
```

Each related document can lead to more context, building a rich semantic understanding of your knowledge base.

This creates a two-way flow where:

- Humans write and edit Markdown files
- LLMs read and write through the MCP protocol
- Sync keeps everything consistent
- All knowledge stays in local files.

## Technical Implementation

Under the hood, MemoPad:

1. Stores everything in Markdown files
2. Uses a SQLite database for searching and indexing
3. Extracts semantic meaning from simple Markdown patterns
    - Files become `Entity` objects
    - Each `Entity` can have `Observations`, or facts associated with it
    - `Relations` connect entities together to form the knowledge graph
4. Maintains the local knowledge graph derived from the files
5. Provides bidirectional synchronization between files and the knowledge graph
6. Implements the Model Context Protocol (MCP) for AI integration
7. Exposes tools that let AI assistants traverse and manipulate the knowledge graph
8. Uses memory:// URLs to reference entities across tools and conversations

The file format is just Markdown with some simple markup:

Each Markdown file has:

### Frontmatter

```markdown
title: <Entity title>
type: <The type of Entity> (e.g. note)
permalink: <a uri slug>

- <optional metadata> (such as tags) 
```

### Observations

Observations are facts about a topic.
They can be added by creating a Markdown list with a special format that can reference a `category`, `tags` using a
"#" character, and an optional `context`.

Observation Markdown format:

```markdown
- [category] content #tag (optional context)
```

Examples of observations:

```markdown
- [method] Pour over extracts more floral notes than French press
- [tip] Grind size should be medium-fine for pour over #brewing
- [preference] Ethiopian beans have bright, fruity flavors (especially from Yirgacheffe)
- [fact] Lighter roasts generally contain more caffeine than dark roasts
- [experiment] Tried 1:15 coffee-to-water ratio with good results
- [resource] James Hoffman's V60 technique on YouTube is excellent
- [question] Does water temperature affect extraction of different compounds differently?
- [note] My favorite local shop uses a 30-second bloom time
```

### Relations

Relations are links to other topics. They define how entities connect in the knowledge graph.

Markdown format:

```markdown
- relation_type [[WikiLink]] (optional context)
```

Examples of relations:

```markdown
- pairs_well_with [[Chocolate Desserts]]
- grown_in [[Ethiopia]]
- contrasts_with [[Tea Brewing Methods]]
- requires [[Burr Grinder]]
- improves_with [[Fresh Beans]]
- relates_to [[Morning Routine]]
- inspired_by [[Japanese Coffee Culture]]
- documented_in [[Coffee Journal]]
```

## Using with VS Code

Add the following JSON block to your User Settings (JSON) file in VS Code. You can do this by pressing `Ctrl + Shift + P` and typing `Preferences: Open User Settings (JSON)`.

```json
{
  "mcp": {
    "servers": {
      "memopad": {
        "command": "uvx",
        "args": ["memopad", "mcp"]
      }
    }
  }
}
```

Optionally, you can add it to a file called `.vscode/mcp.json` in your workspace. This will allow you to share the configuration with others.

```json
{
  "servers": {
    "memopad": {
      "command": "uvx",
      "args": ["memopad", "mcp"]
    }
  }
}
```

You can use MemoPad with VS Code to easily retrieve and store information while coding.

## Using with Claude Desktop

MemoPad is built using the MCP (Model Context Protocol) and works with the Claude desktop app (https://claude.ai/):

1. Configure Claude Desktop to use MemoPad:

Edit your MCP configuration file (usually located at `~/Library/Application Support/Claude/claude_desktop_config.json`
for OS X):

```json
{
  "mcpServers": {
    "memopad": {
      "command": "uvx",
      "args": [
        "memopad",
        "mcp"
      ]
    }
  }
}
```

If you want to use a specific project (see [Multiple Projects](#multiple-projects) below), update your Claude Desktop
config:

```json
{
  "mcpServers": {
    "memopad": {
      "command": "uvx",
      "args": [
        "memopad",
        "mcp",
        "--project",
        "your-project-name"
      ]
    }
  }
}
```

2. Sync your knowledge:

```bash
# One-time sync of local knowledge updates
memopad sync

# Run realtime sync process (recommended)
memopad sync --watch
```

3. Cloud features (optional, requires subscription):

```bash
# Authenticate with cloud
memopad cloud login

# Bidirectional sync with cloud
memopad cloud sync

# Verify cloud integrity
memopad cloud check

# Mount cloud storage
memopad cloud mount
```

**Routing Flags** (for users with cloud subscriptions):

When cloud mode is enabled, CLI commands communicate with the cloud API by default. Use routing flags to override this:

```bash
# Force local routing (useful for local MCP server while cloud mode is enabled)
memopad status --local
memopad project list --local

# Force cloud routing (when cloud mode is disabled but you want cloud access)
memopad status --cloud
memopad project info my-project --cloud
```

The local MCP server (`memopad mcp`) automatically uses local routing, so you can use both local Claude Desktop and cloud-based clients simultaneously.

4. In Claude Desktop, the LLM can now use these tools:

**Content Management:**
```
write_note(title, content, folder, tags) - Create or update notes
read_note(identifier, page, page_size) - Read notes by title or permalink
read_content(path) - Read raw file content (text, images, binaries)
view_note(identifier) - View notes as formatted artifacts
edit_note(identifier, operation, content) - Edit notes incrementally
move_note(identifier, destination_path) - Move notes with database consistency
delete_note(identifier) - Delete notes from knowledge base
```

**Knowledge Graph Navigation:**
```
build_context(url, depth, timeframe) - Navigate knowledge graph via memory:// URLs
recent_activity(type, depth, timeframe) - Find recently updated information
list_directory(dir_name, depth) - Browse directory contents with filtering
```

**Search & Discovery:**
```
search(query, page, page_size) - Search across your knowledge base
search_notes(query, page, page_size, search_type, types, entity_types, after_date, metadata_filters, tags, status, project) - Search with filters
search_by_metadata(filters, limit, offset, project) - Structured frontmatter search
semantic_search(query, mode, limit, project) - Embedding / hybrid search (opt-in, see below)
backlinks(identifier, project) - Find all notes linking TO an entity
```

**Project Management:**
```
list_memory_projects() - List all available projects
create_memory_project(project_name, project_path) - Create new projects
get_current_project() - Show current project stats
sync_status() - Check synchronization status
```

**Daily journal:**
```
daily_note(date_str, project, template) - Create or open daily/YYYY-MM-DD.md
                                          with prev/next wikilinks. Accepts
                                          ISO dates or "today"/"yesterday"/"tomorrow".
```

**Storage maintenance:**
```
optimize_storage(project, dry_run=True) - Find duplicate notes; with dry_run=False,
                                          replace each duplicate's body with a
                                          wikilink redirect to the canonical copy.
```

**Visualization:**
```
canvas(nodes, edges, title, folder) - Generate knowledge visualizations
```

**Tb-borrowed tools** (all gated by feature flags, default off — see
`tb-borrow-implementation-plan.md` / `tb-borrow-progress.md`):
```
# G5 provenance (levels_enabled)
drill_down(identifier, target_level, max_depth) - Trace a distilled memory back to its L0 sources

# G1 skill assets (skills_enabled)
create_skill / get_skill / list_skills / validate_skill - Versioned skills (trigger/steps/validation)

# G6 short-term session context (shortterm_enabled)
add_session_ref / add_session_step / get_session_context / drill_down_session / finalize_session
                                          - Per-session 3-layer context (refs -> steps -> Mermaid canvas)

# G2 CodeGraph (codegraph_enabled)
index_code / find_symbol / impact_path / code_context - Index & query source code in the knowledge graph
```

**Distillation (L0→L3, `levels_enabled`):** MemoPad distils structured L0
observations into L1 atomic facts → L2 scenarios → an L3 persona, natively and
automatically on every write. Drive it on demand from the CLI (pure in-app code,
no external model/API/key):

```bash
memopad distill                       # incremental L1 pass (default)
memopad distill --bulk                 # cold-start / backfill: process ALL L0 entities
memopad distill --level L1,L2,L3       # run multiple levels
memopad distill --dry-run              # read-only: print current L1/L2/L3 counts

memopad distill discover-categories    # show observation categories present vs distillable
memopad distill add-categories -c policy,guideline   # widen the distillable set
memopad distill add-categories --auto               # auto-add all unknown categories
```

Observation categories are free-form (MemoPad has no fixed canonical set) and
**comma-separated compound categories match if any component is distillable**
(e.g. `config_docs, algorithms` matches when `algorithms` is distillable). The
shipped default covers the common L0 categories; edit the `distillation` skill
asset to override per-project. A stuck watermark (L1 permanently empty though
L0 entities exist) **self-heals**: an incremental pass with an empty watermark
scan and empty L1 falls back to a full scan automatically — no manual `--bulk`
required. The same surface is available over MCP (`distill_memory`,
`discover_categories`, `add_categories`) and the v2 API (`POST /distill?bulk=`,
`GET /discover-categories`, `POST /add-categories`).

> The new tools are MCP-only (no `memopad tool` CLI wrappers), consistent with other
> feature-flagged tools like `canvas`. Enable each via its config flag to use.

5. Example prompts to try:

```
"Create a note about our project architecture decisions"
"Find information about JWT authentication in my notes"
"Create a canvas visualization of my project components"
"Read my notes on the authentication system"
"What have I been working on in the past week?"
```

## Futher info

See the [Documentation](https://docs.memopad.xxx) for more info, including:

- [Complete User Guide](https://docs.memopad.xxx/user-guide/)
- [CLI tools](https://docs.memopad.xxx/guides/cli-reference/)
- [Cloud CLI and Sync](https://docs.memopad.xxx/guides/cloud-cli/)
- [Managing multiple Projects](https://docs.memopad.xxx/guides/cli-reference/#project)
- [Importing data from OpenAI/Claude Projects](https://docs.memopad.xxx/guides/cli-reference/#import)

## Logging

MemoPad uses [Loguru](https://github.com/Delgan/loguru) for logging. The logging behavior varies by entry point:

| Entry Point | Default Behavior | Use Case |
|-------------|------------------|----------|
| CLI commands | File only | Prevents log output from interfering with command output |
| MCP server | File only | Stdout would corrupt the JSON-RPC protocol |
| API server | File (local) or stdout (cloud) | Docker/cloud deployments use stdout |

**Log file location:** `~/.memopad/memopad.log` (10MB rotation, 10 days retention)

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMOPAD_LOG_LEVEL` | `INFO` | Log level: DEBUG, INFO, WARNING, ERROR |
| `MEMOPAD_CLOUD_MODE` | `false` | When `true`, API logs to stdout with structured context |
| `MEMOPAD_FORCE_LOCAL` | `false` | When `true`, forces local API routing (ignores cloud mode) |
| `MEMOPAD_ENV` | `dev` | Set to `test` for test mode (stderr only) |

### Examples

```bash
# Enable debug logging
MEMOPAD_LOG_LEVEL=DEBUG memopad sync

# View logs
tail -f ~/.memopad/memopad.log

# Cloud/Docker mode (stdout logging with structured context)
MEMOPAD_CLOUD_MODE=true uvicorn basic_memory.api.app:app
```

## Development

### Running Tests

MemoPad supports dual database backends (SQLite and Postgres). By default, tests run against SQLite. Set `MEMOPAD_TEST_POSTGRES=1` to run against Postgres (uses testcontainers - Docker required).

**Quick Start:**
```bash
# Run all tests against SQLite (default, fast)
just test-sqlite

# Run all tests against Postgres (uses testcontainers)
just test-postgres

# Run both SQLite and Postgres tests
just test
```

**Available Test Commands:**

- `just test` - Run all tests against both SQLite and Postgres
- `just test-sqlite` - Run all tests against SQLite (fast, no Docker needed)
- `just test-postgres` - Run all tests against Postgres (uses testcontainers)
- `just test-unit-sqlite` - Run unit tests against SQLite
- `just test-unit-postgres` - Run unit tests against Postgres
- `just test-int-sqlite` - Run integration tests against SQLite
- `just test-int-postgres` - Run integration tests against Postgres
- `just test-windows` - Run Windows-specific tests (auto-skips on other platforms)
- `just test-benchmark` - Run performance benchmark tests
- `just testmon` - Run tests impacted by recent changes (pytest-testmon)
- `just test-smoke` - Run fast MCP end-to-end smoke test
- `just fast-check` - Run fix/format/typecheck + impacted tests + smoke test
- `just doctor` - Run local file <-> DB consistency checks with temp config

**Postgres Testing:**

Postgres tests use [testcontainers](https://testcontainers-python.readthedocs.io/) which automatically spins up a Postgres instance in Docker. No manual database setup required - just have Docker running.

**Testmon Note:** When no files have changed, `just testmon` may collect 0 tests. That's expected and means no impacted tests were detected.

**Test Markers:**

Tests use pytest markers for selective execution:
- `windows` - Windows-specific database optimizations
- `benchmark` - Performance tests (excluded from default runs)
- `smoke` - Fast MCP end-to-end smoke tests

**Other Development Commands:**
```bash
just install          # Install with dev dependencies
just lint             # Run linting checks
just typecheck        # Run type checking
just format           # Format code with ruff
just fast-check       # Fast local loop (fix/format/typecheck + testmon + smoke)
just doctor           # Local consistency check (temp config)
just check            # Run all quality checks
just migration "msg"  # Create database migration
```

**Local Consistency Check:**
```bash
# Roundtrip test against a throwaway temp project
memopad doctor

# Drift check on a real project (read-only by default)
memopad doctor --project my-research

# Drift check + auto-reconcile via force_full sync
memopad doctor --project my-research --fix
```

The `--project` mode reports new files on disk, modified/deleted entries, and
unresolved `[[wikilinks]]`. With `--fix`, file ↔ DB drift is reconciled
automatically. Unresolved wikilinks are reported only — fixing them is left
to the user since fuzzy-rewriting markdown is risky.

## Optional: hybrid semantic search

MemoPad supports semantic and hybrid (BM25 + embeddings) search alongside
keyword FTS5. It's **opt-in** so existing installs see no behavior change.

```bash
# Install the optional dependency (CPU-only ONNX runtime, ~30MB model)
pip install 'memopad[embeddings]'

# Enable for the current session
export MEMOPAD_EMBEDDINGS_ENABLED=true
```

The `semantic_search` MCP tool then accepts `mode="semantic" | "hybrid" | "fts"`.
Hybrid mode fuses keyword and embedding rankings via Reciprocal Rank Fusion
(RRF) — robust across precise term lookups and conceptual queries without
weight tuning.

> **CPU usage:** `fastembed`/onnxruntime defaults its intra-op thread pool to *all*
> cores, which can starve the event loop on CPU-only boxes (the historical MCP
> keepalive/reconnect storm). MemoPad caps this to 1 thread by default; override
> with `MEMOPAD_EMBED_THREADS=<n>` (e.g. `2`). `OMP_NUM_THREADS` has no effect —
> onnxruntime uses its own `SessionOptions` pool.

See [plans/PLAN.md](plans/PLAN.md) §2.4 for the full integration roadmap.

See the [justfile](justfile) for the complete list of development commands.

## License

AGPL-3.0

Contributions are welcome. See the [Contributing](CONTRIBUTING.md) guide for info about setting up the project locally
and submitting PRs.

## Star History
