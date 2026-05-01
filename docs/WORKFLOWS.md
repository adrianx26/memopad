# MemoPad Workflows

Visual reference for how data and control flow through MemoPad. Diagrams use
Mermaid — GitHub renders them natively, and they round-trip cleanly into
Obsidian / Notion / VS Code's Markdown preview.

For the engineering plan and current implementation status see
[plans/PLAN.md](../plans/PLAN.md). For deeper architecture see
[ARCHITECTURE.md](ARCHITECTURE.md).

---

## 1. High-level architecture

How an LLM call lands in the right repository method.

```mermaid
flowchart LR
    LLM["LLM client<br/>(Claude Desktop, VS Code, …)"]
    MCP["MCP server<br/>(fastmcp)"]
    Tools["MCP tools<br/>write_note, read_note,<br/>search_notes, daily_note,<br/>backlinks, semantic_search,<br/>assimilate, optimize_storage,<br/>doctor"]
    Clients["Typed clients<br/>KnowledgeClient, SearchClient,<br/>MemoryClient, ProjectClient,<br/>DirectoryClient, ResourceClient"]
    API["FastAPI v2 routers<br/>/v2/projects/{id}/…"]
    Services["Services<br/>EntityService, SearchService,<br/>SyncService, ContextService,<br/>StorageOptimizer, EmbeddingService"]
    Repos["Repositories<br/>Entity, Relation, Observation,<br/>Search, Project"]
    DB[("SQLite or Postgres<br/>+ FTS5 / tsvector index<br/>+ optional embedding table")]
    Files[("Markdown files<br/>(source of truth)")]

    LLM -- JSON-RPC over stdio --> MCP
    MCP --> Tools
    Tools -- httpx ASGI --> Clients
    Clients --> API
    API --> Services
    Services --> Repos
    Repos --> DB
    Services <-. read/write .-> Files
```

**Key invariant:** files are the source of truth. The DB is a derived index
that sync rebuilds from disk.

---

## 2. Write flow — `write_note`

Optimistic create with conflict-aware update.

```mermaid
sequenceDiagram
    participant LLM
    participant Tool as write_note
    participant Client as KnowledgeClient
    participant API as Knowledge router
    participant Svc as EntityService
    participant File as FileService
    participant DB as Repository

    LLM->>Tool: write_note(title, content, directory, …)
    Tool->>Client: POST /entities (Entity payload)
    Client->>API: HTTP POST
    API->>Svc: create_entity(data, fast=…)

    alt fast=true
        Svc->>File: write markdown file
        Svc->>DB: insert entity row (lazy index)
        Note over Svc: schedule reindex_entity background task
    else fast=false (default for write_note)
        Svc->>File: write markdown file
        Svc->>DB: insert entity + observations + relations
        Svc->>DB: update FTS5 search index
    end

    alt 409 conflict (entity exists)
        Tool->>Client: resolve_entity(permalink) → external_id
        Tool->>Client: PUT /entities/{id} (update)
    end

    API-->>Client: EntityResponse
    Client-->>Tool: parsed result
    Tool-->>LLM: markdown summary
```

---

## 3. Read & search flows

`search_notes` (FTS5/BM25), `semantic_search` (hybrid), and `read_note`
(direct fetch) share the typed-client layer.

```mermaid
flowchart TD
    Q[/"User query<br/>or wikilink"/]

    subgraph Tools
        SN[search_notes]
        SS[semantic_search]
        RN[read_note]
        BC[build_context]
        BL[backlinks]
    end

    Q --> SN
    Q --> SS
    Q --> RN
    Q --> BC
    Q --> BL

    SN -->|"text + filters"| SR["SearchClient<br/>POST /search"]
    SS -->|"mode=fts"| SR
    SS -->|"mode=semantic / hybrid"| ES["EmbeddingService<br/>(optional, gated by env)"]
    RN -->|"identifier"| KR["KnowledgeClient<br/>POST /resolve → GET /entities/{id}"]
    BC -->|"memory:// URL"| MR["MemoryClient<br/>POST /context"]
    BL -->|"identifier"| KB["KnowledgeClient<br/>GET /entities/{id}/backlinks"]

    SR --> FTS5[("FTS5 / tsvector<br/>BM25 ranking")]
    ES --> VEC[("embedding table<br/>cosine similarity")]
    ES -. fuses with FTS5 via .-> RRF["Reciprocal Rank Fusion<br/>(score = Σ 1/(k+rank))"]
    KR --> ENT[("entity rows")]
    MR --> REL[("relation graph<br/>(depth-bounded BFS)")]
    KB --> RELI[("incoming relations<br/>+ unresolved [[wikilinks]]")]

    FTS5 --> Out
    VEC --> RRF
    RRF --> Out
    ENT --> Out
    REL --> Out
    RELI --> Out

    Out[/"Markdown response<br/>back to LLM"/]
```

**Note (status):** `semantic_search` ships with the embedding service wired
up; the hybrid retrieval call back to the API still needs the
`/v2/projects/{id}/search/semantic` endpoint and a sync-time embedding hook.
See [plans/PLAN.md §2.4](../plans/PLAN.md).

---

## 4. Sync flow — files ↔ DB

The sync coordinator watches the project root and reconciles each change.

```mermaid
flowchart TD
    Start([User edits file<br/>or runs `memopad sync`])
    FS[/"Filesystem watcher<br/>(watchfiles)"/]
    Coord[SyncCoordinator]
    Scan["Scan: compute<br/>checksums per file"]
    Diff{Compare to<br/>DB checksums}

    NEW[New: file on disk,<br/>not in DB]
    MOD[Modified: checksum changed]
    DEL[Deleted: in DB,<br/>file gone]
    MOV[Moved: same checksum,<br/>different path]

    ParseNew["EntityParser<br/>+ MarkdownProcessor"]
    UpdateRow[Update entity row]
    DeleteRow[Soft-delete entity]
    UpdatePath[Update file_path]

    Resolve["Resolve unresolved relations<br/>(any [[wikilinks]] this file<br/>fulfills now exist)"]
    Reindex[Update FTS5 index<br/>+ embedding store if enabled]

    Start --> FS --> Coord --> Scan --> Diff
    Diff --> NEW --> ParseNew --> UpdateRow --> Resolve --> Reindex
    Diff --> MOD --> ParseNew --> UpdateRow
    Diff --> DEL --> DeleteRow --> Resolve
    Diff --> MOV --> UpdatePath --> Resolve
```

`memopad doctor --project NAME` reads the same diff from
`POST /v2/projects/{id}/status`. With `--fix`, it triggers a `force_full=true`
sync to apply the reconciliation.

---

## 5. Assimilate flow — URL → structured notes

```mermaid
flowchart TD
    URL[/"User-supplied URL"/]
    Strat{Detect strategy}

    GH["GitHub repo<br/>(github.com/owner/repo)"]
    DL["Direct file<br/>(.pdf, .docx, .xlsx, image)"]
    GEN["Generic web page"]

    Clone["git clone --depth 1<br/>→ tempdir"]
    Walk["Walk REPO_FILE_PATTERNS<br/>(deduped via set)"]
    Read["Read each file<br/>(errors → result.errors)"]

    HEAD["HEAD request<br/>→ Content-Type"]
    Fetch["GET → bytes"]
    Extract["FileProcessor<br/>(pypdf, python-docx,<br/>openpyxl, PIL)"]

    Crawl["BFS crawl with deque<br/>(depth-bounded)"]
    Pages["Extract text<br/>(html_to_text or<br/>plain-text passthrough)"]
    Links["Extract links<br/>internal / github / external"]

    Detect[detect_content_type]
    Build[NOTE_BUILDERS registry<br/>build_note per type]
    Notes[("Per-type notes:<br/>Overview, Agent Profiles,<br/>Skills, Concepts, Tools,<br/>Algorithms, Decision Structures,<br/>Functional Diagram,<br/>GitHub Links Index")]

    Store["Store via KnowledgeClient<br/>(409 conflict → update)"]

    URL --> Strat
    Strat --> GH --> Clone --> Walk --> Read --> Detect
    Strat --> DL --> HEAD --> Fetch --> Extract --> Detect
    Strat --> GEN --> Crawl --> Pages --> Links --> Detect
    Detect --> Build --> Notes --> Store
```

Errors (network, decode, file-read) are now captured with their reason and
surfaced in the final summary, not silently swallowed.

---

## 6. Knowledge graph traversal

Where wikilinks come from and how they get resolved.

```mermaid
flowchart LR
    subgraph Markdown
        A["[Note A]<br/>- depends_on [[Note B]]<br/>- relates_to [[Future Note]]"]
    end

    subgraph Parser
        EP[EntityParser]
    end

    subgraph DB
        ENT["Entity row (A)"]
        REL_RES["Relation: A → B<br/>(to_id = B.id, resolved)"]
        REL_UNR["Relation: A → 'future-note'<br/>(to_id = NULL, unresolved)"]
    end

    A --> EP --> ENT
    EP --> REL_RES
    EP --> REL_UNR

    subgraph Resolution
        SyncResolver["SyncService.resolve_unresolved_relations()<br/>called after every sync"]
    end

    REL_UNR -- "when [[Future Note]]<br/>finally exists" --> SyncResolver --> REL_RES2["Relation upgraded<br/>(to_id set)"]

    subgraph Queries
        BC2["build_context(memory://A)<br/>BFS over outgoing relations"]
        BL2["backlinks(B)<br/>incoming relations<br/>+ unresolved with to_name match"]
    end

    REL_RES --> BC2
    REL_RES --> BL2
    REL_UNR --> BL2
```

Backlinks include unresolved wikilinks that match the target's permalink or
title — so a brand-new note immediately shows the inbound references that
were waiting on it.

---

## 7. Storage optimization (dedupe)

```mermaid
flowchart TD
    Run[/"optimize_storage(dry_run=True)<br/>or memopad CLI"/]
    Walk["Walk project root"]
    Skip{"filename in<br/>{readme.md, index.md, .gitignore}<br/>or empty?"}
    Norm["Strip frontmatter<br/>+ canonicalize whitespace<br/>+ SHA256"]
    Bucket["bucket: hash → list[(path, mtime, size)]"]
    Group{"len > 1?"}

    Canonical["Pick oldest mtime<br/>= canonical"]
    Report[Markdown report]

    DryRun{dry_run?}
    Rewrite["For each duplicate:<br/>1. read frontmatter<br/>2. body = redirects_to wikilink<br/>3. write back"]

    Run --> Walk --> Skip
    Skip -- skip --> Walk
    Skip -- include --> Norm --> Bucket --> Group
    Group -- no --> Walk
    Group -- yes --> Canonical --> Report
    Report --> DryRun
    DryRun -- "true (default)" --> Done[/"Report only<br/>— no files modified"/]
    DryRun -- false --> Rewrite --> Done2[/"Files rewritten<br/>(frontmatter preserved)"/]
```

Canonical-by-oldest-mtime means the original creation wins; later accidental
copies become redirect stubs.

---

## 8. Graph analytics (`cluster_notes`, `hub_notes`, `find_path`)

Three read-only operations over the relation graph, inspired by graphify but
running on MemoPad's existing wikilink-derived relations. NetworkX-backed,
no LLM calls, no embedding model required.

```mermaid
flowchart TD
    Tool["MCP tool<br/>cluster_notes / hub_notes / find_path"]
    Client["GraphAnalyticsClient<br/>GET /graph/{clusters,hubs,path}"]
    API["graph_analytics_router"]
    Svc["GraphAnalyticsService<br/>(session_maker, project_id)"]
    Load["_load_graph()<br/>SELECT entities + resolved relations"]
    NX["NetworkX MultiGraph"]

    Tool --> Client --> API --> Svc --> Load --> NX

    NX --> Louvain["nx.community.louvain_communities<br/>(seed=42, deterministic)"]
    NX --> Degree["in_degree / out_degree<br/>(directional via raw SQL)"]
    NX --> SP["nx.shortest_path<br/>(undirected, preserves<br/>relation_type per hop)"]

    Louvain -- "filter < min_size,<br/>label by max-degree member" --> CRes[/"Cluster list<br/>(label, members, internal_edges)"/]
    Degree -- "sort desc by total" --> HRes[/"Top-N hubs<br/>(in_degree, out_degree)"/]
    SP -- "bound by max_length" --> PRes[/"PathStep chain<br/>(or found=False)"/]

    CRes --> Out[/"Markdown to LLM"/]
    HRes --> Out
    PRes --> Out
```

**Why undirected for clustering:** `depends_on` is not symmetric with
`enables`, but for "what topics are connected" questions users expect
either direction to count as a link.

**Why directional for hubs:** in/out split tells you whether a note is a
*reference* (high in_degree, things link to it) vs. an *index* (high
out_degree, it links out to many things).

**Determinism:** Louvain has a stable seed so the same vault produces the
same clusters across runs. Tied degrees in `find_hubs` break by title
ascending, also stable.

---

## 9. Doctor (`--fix` mode)

```mermaid
sequenceDiagram
    participant U as User
    participant CLI as memopad doctor
    participant API
    participant Svc as SyncService

    U->>CLI: memopad doctor --project NAME --fix
    CLI->>API: POST /v2/projects/{id}/status
    API-->>CLI: SyncReportResponse(new, modified, deleted, moves)
    CLI->>U: prints drift counts

    alt drift detected and --fix set
        CLI->>API: POST /v2/projects/{id}/sync?force_full=true
        API->>Svc: full reconcile
        Svc-->>API: SyncReport(total)
        API-->>CLI: total reconciled
    end

    CLI->>API: GET /v2/projects/{id}/sync/unresolved
    API-->>CLI: unresolved relations
    CLI->>U: warns about broken [[wikilinks]] (not auto-fixed)

    alt issues remain
        CLI->>U: exit 1 with summary
    else clean
        CLI->>U: "Project is clean."
    end
```

Unresolved wikilinks are reported only — auto-rewriting user content via
fuzzy matching is too risky.

---

## Updating these diagrams

Mermaid blocks are plain text. When you change a flow:

1. Edit the relevant block in this file.
2. Preview in any Mermaid-aware renderer (GitHub, Obsidian, VS Code).
3. Note the corresponding section in [plans/PLAN.md](../plans/PLAN.md) if the
   change reflects a new ✅/🚧/💭 status.
