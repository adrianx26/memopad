# Future Plan

Forward-looking work that's been analyzed but not implemented. This file
captures **why** something isn't in the codebase yet — the design notes
shouldn't have to be reconstructed from scratch later.

For the current state of the codebase see [plans/PLAN.md](plans/PLAN.md).
For overall flow diagrams see [docs/WORKFLOWS.md](docs/WORKFLOWS.md).

The items below originated from the [graphify](https://github.com/safishamsi/graphify)
analysis. Tier 1 (cluster_notes / hub_notes / find_path) shipped in commit
`f496f7f`. This document covers Tier 2 (deferred — costly but high-value
when the user need arises) and Tier 3 (skipped — duplicative or out of
scope).

---

## Tier 2 — Defer until the user need arises

### 2.1 Tree-sitter code extraction in `assimilate`

#### Status: 💭 Proposed — not started

#### What it would do

Today, `assimilate` clones a GitHub repo and reads code files as raw text:
the `assimilate` tool grabs README, markdown, and source files via
`REPO_FILE_PATTERNS`, runs them through the content-type detector, and
stores them as monolithic notes. The result for a repo like `react/react`
is ~50 notes that are mostly raw code dumps.

Tree-sitter is graphify's secret sauce on the code side. With it we'd
extract structured entities directly from the AST:

| Source structure | Becomes |
|---|---|
| Each function / method | An `Entity` (entity_type=`function`) |
| Each class / struct / trait | An `Entity` (entity_type=`class`) |
| `import`, `from … import`, `use`, `#include` | A `Relation` of type `imports` |
| Function call site | A `Relation` of type `calls` |
| Inheritance / interface implementation | A `Relation` of type `extends` / `implements` |
| Module / package / namespace | A directory-style `Entity` hierarchy |

A 50-note repo dump becomes thousands of cross-linked function-level
entities. Search becomes "find all callers of `useEffect`" instead of "grep
the markdown blob containing the React README."

#### Why this fits MemoPad

- The graph layer we just added (`backlinks`, `cluster_notes`, `hub_notes`,
  `find_path`) suddenly has 1000× more meaningful structure to operate on.
  `cluster_notes` over a real codebase finds true subsystems.
- `find_path("useState", "react-dom/render")` answers "how does this
  function reach rendering" without any LLM intervention.
- Hybrid semantic search becomes much more powerful once entities are at
  function granularity.

#### Real costs

- **Native dependencies.** `tree-sitter` itself is C with Python bindings,
  and each language needs its own grammar package
  (`tree-sitter-python`, `tree-sitter-typescript`, `tree-sitter-rust`, …).
  Ten languages = ~10 MB total + native compilation on `pip install`.
  That's a meaningful uplift from MemoPad's current "pure Python" install
  story.
- **New code volume.** Either a new module under
  `src/memopad/mcp/tools/assimilate/code_extractor.py` or a separate
  package under `src/memopad/services/code_extraction/`. Probably 800–1500
  LOC including per-language quirks.
- **Ingestion latency grows.** Parsing instead of just reading. For a
  large repo this could go from seconds to minutes.
- **Output volume grows ~50×**, which stresses:
  - The DB (we'd want to revisit indexes, particularly on `relation`)
  - Sync (more files = more checksums to diff each watch tick)
  - Embeddings (if enabled — function-level embeddings is a lot of vectors)
  - The relation table (thousands of `calls` edges per repo)

#### Required design decisions before starting

1. **Granularity policy.** Function-level only? Class-level? File-level
   fallback for languages we don't support? Need a `--granularity` flag.
2. **Entity stability across commits.** If a function gets renamed, do we
   detect the rename via tree-sitter + similarity, or treat it as
   delete+add? graphify uses SHA-based caching but doesn't solve renames.
3. **Where do calls live.** A `calls` relation per call site explodes the
   graph. Probably want to dedupe per (caller, callee) pair so the edge
   count stays manageable.
4. **Opt-in vs. opt-out.** Strongly recommend opt-in via
   `pip install 'memopad[code-extraction]'` extra and a flag
   `assimilate(extract_code=False)` that defaults to off. Don't surprise
   existing users with a heavier install.
5. **Hybrid with the existing path.** Should code extraction run *in
   addition to* the current text-based assimilation, or replace it for
   detected source files? The README still wants to be a markdown note.

#### Effort estimate

**3–5 days of focused work**, broken down:

- Day 1: tree-sitter setup, Python + TypeScript grammars working end-to-end,
  emit functions+classes as entities for one file.
- Day 2: import + extends + implements relations across a multi-file
  project; entity stability via permalink scheme like
  `code/<repo>/<filepath>:<symbol>`.
- Day 3: call-graph extraction (deduped per caller/callee), DB stress
  testing on a real repo.
- Day 4: integration into `assimilate` flow, optional `[code-extraction]`
  install extra in `pyproject.toml`, `extract_code` parameter, fall back
  to current text-based assimilation when disabled.
- Day 5: tests (unit per language; integration on `react/react`-sized
  repo), docs, plan/workflow updates.

#### When to actually do this

When at least one of the following is true:

- A user explicitly asks for it, or you find yourself repeatedly
  assimilating the same codebase and frustrated by the blob-of-text result.
- You're reaching for the graph analytics tools (`cluster_notes`, etc.)
  on assimilated data and getting weak signal — function-level granularity
  would unlock real structure.
- You have time for a focused week and the appetite to grow the install
  surface.

Until then, the `assimilate` tool's current text-based approach works and
incremental re-runs are cheap (commit `3e7e441`), so users aren't paying
ongoing pain.

#### Files that would change

- `src/memopad/mcp/tools/assimilate/code_extractor.py` (new, primary work)
- `src/memopad/mcp/tools/assimilate/__init__.py` (add `extract_code` param,
  branch into code extractor when enabled)
- `src/memopad/mcp/tools/assimilate/config.py` (per-language config)
- `pyproject.toml` (new `[project.optional-dependencies] code-extraction`)
- `src/memopad/schemas/base.py` (potentially new `entity_type`s)
- `tests/mcp/tools/test_code_extraction.py` (new)
- `docs/WORKFLOWS.md` (extend assimilate diagram)
- `plans/PLAN.md` (move from "Proposed" to "Implemented" once shipped)

---

## Tier 3 — Skip (duplicative or out of scope)

These were considered and intentionally rejected during the graphify
analysis. Recorded here so the rationale doesn't get re-litigated next time
someone discovers graphify and asks "shouldn't we steal X?"

### 3.1 Confidence tagging on relations (`EXTRACTED` / `INFERRED` / `AMBIGUOUS`)

#### Why graphify has it

graphify *generates* relations using LLMs — so it needs to mark which ones
are AST-derived (high confidence) vs. inferred from comments (lower
confidence) vs. ambiguous matches.

#### Why MemoPad doesn't need it

MemoPad's relations come from user-written `[[wikilinks]]`. They are
ground truth — there's no inferred-vs-extracted distinction to record. A
confidence column would be dead weight: every row would have the same
value.

#### When this could change

If we ever add an LLM-relation-extraction pass (e.g. "scan the corpus and
suggest relations the user didn't write explicitly"), then this becomes
necessary. Schema migration would add a `confidence` column to the
`relation` table with a default of 1.0 for existing user-authored rows.

#### Status: 💭 Revisit only if AI-generated relations land

---

### 3.2 vis.js interactive HTML viewer

#### Why graphify has it

graphify's primary output is a browser-based graph view because users want
to *see* the structure of an unfamiliar codebase.

#### Why MemoPad doesn't need it

We already have multiple visualization surfaces:

- **`canvas` MCP tool** emits Obsidian canvas (`.canvas`) files with full
  layout and styling that work in Obsidian's native canvas view.
- **`memopad-architecture.html`** at the repo root is an existing
  standalone HTML diagram of the architecture.
- **Obsidian itself** is the canonical visualization layer for MemoPad —
  graph view, backlinks panel, local graph.

A third in-browser visualization would be net maintenance burden without
unlocking anything users can't already do.

#### When this could change

If we decide to ship a web UI (currently MemoPad has none), then a built-in
graph viewer would be table stakes. That's a much larger product decision
than "should we add a viewer?"

#### Status: ❌ Skip

---

### 3.3 Whisper transcription for audio/video

#### Why graphify has it

graphify ingests YouTube videos and audio files via `faster-whisper` to
expand its multimodal coverage.

#### Why MemoPad doesn't

- MemoPad is positioned as a knowledge management tool over markdown
  notes. Audio/video memos are completely orthogonal to the current use
  case.
- `faster-whisper` adds a 200 MB+ install footprint (model + CUDA support
  optional but encouraged).
- No user has asked for this. Adding 200 MB of dependencies to a
  knowledge-graph CLI without demand is a textbook bloat anti-pattern.

#### When this could change

If a user explicitly asks "I want to drop voice memos into MemoPad," this
gets reconsidered as an optional `[transcription]` extra (same opt-in
shape as `[embeddings]`).

#### Status: ❌ Skip until user demand exists

---

### 3.4 Git hooks for auto-rebuild on commit

#### Why graphify has it

`graphify hook install` adds a post-commit hook that rebuilds the graph
after each commit, keeping the visualization fresh.

#### Why MemoPad doesn't need it

`memopad sync --watch` already covers this. It uses `watchfiles` to
monitor the project root and triggers a sync the moment a file changes —
which catches *all* edits, not just commits, and works for users who don't
use git for their notes (Obsidian users, etc.).

A git hook would be:
- Strictly weaker (only fires on commits, misses live edits)
- Strictly more complex (requires a git repo, has to handle hook conflicts
  with existing user hooks)
- Fully duplicative for any user already running `--watch`

#### When this could change

If we want to support a "no daemon" workflow where the user explicitly
prefers commit-triggered updates, this could ship as `memopad hook
install`. Low priority — `--watch` is the better UX.

#### Status: ❌ Skip — duplicative

---

### 3.5 SHA256-based file cache

#### Why graphify has it

graphify stores a SHA cache to skip re-processing files that haven't
changed between runs.

#### Why MemoPad already has the equivalent

- `memopad sync` already uses per-file content checksums (see
  `src/memopad/sync/sync_service.py`).
- `assimilate` now uses content-hash skipping at the note-write boundary
  (see `_content_hash` in `src/memopad/mcp/tools/assimilate/__init__.py`,
  shipped commit `3e7e441`).

The two together cover the same ground graphify's SHA cache covers.

#### Status: ✅ Already implemented (different mechanism, same outcome)

---

### 3.6 Cross-platform skill installer

#### Why graphify has it

`/graphify` registers itself as a skill across Claude Code, Cursor, Codex,
etc.

#### Why MemoPad's situation differs

We already have install guides for VS Code and Antigravity in
[`plans/install-memopad-vscode.md`](plans/install-memopad-vscode.md) and
[`plans/install-memopad-to-antigravity.md`](plans/install-memopad-to-antigravity.md).

The install path is well-documented. What we don't have is a single
auto-installer. That's a UX nice-to-have but not architecturally
significant.

#### Status: ❌ Skip — current docs are sufficient

---

## Decision matrix

| Item | Effort | Risk | User value | Decision |
|---|---|---|---|---|
| 2.1 Tree-sitter code extraction | L (3–5 days) | Medium (native deps, install size) | High (for codebase users) | **Defer until demand** |
| 3.1 Confidence on relations | M (schema migration) | Low | Zero today (no AI extraction) | Skip |
| 3.2 vis.js viewer | M | Low | Low (duplicative) | Skip |
| 3.3 Whisper | L (heavy deps) | Medium (bloat) | Zero (no use case) | Skip |
| 3.4 Git hooks | S | Low | Low (duplicative) | Skip |
| 3.5 SHA cache | — | — | — | Already done differently |
| 3.6 Cross-platform installer | M | Low | Low | Skip — docs cover it |

---

## How to revisit

When a user asks "can we add X from graphify?", check this file first.
Either the analysis already exists (and you can argue from a position) or
you'll have a place to record a fresh analysis.

When implementing one of these, move the section into
[`plans/PLAN.md`](plans/PLAN.md) with status changed to ✅ and remove it
from this file.
