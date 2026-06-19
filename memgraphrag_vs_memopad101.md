# MemGraphRAG × MemoPad — Deep Comparison & Adoption Proposals

MemGraphRAG is a KDD 2026 paper from Xiamen / Jilin Universities.  
It introduces a **three-layer memory + three-agent** framework that attacks three well-known failure modes of naive GraphRAG.  
This document maps each idea onto MemoPad's codebase and proposes concrete adoptions.

---

## 1. The Three Problems MemGraphRAG Solves

| # | Problem | MemGraphRAG's fix | MemoPad today |
|---|---------|-------------------|---------------|
| **P1** | **Noise** — LLMs extract irrelevant triplets (e.g. "patient prefers tea" from a medical doc) | Ontology Layer validates every extracted relation against a stable schema; low-frequency schemas are suppressed | MemoPad **writes whatever the LLM writes** into Observations. No noise gate. No schema validation layer. |
| **P2** | **Contradictions** — same fact stated differently by two sources; both survive in the graph | Conflict Detector Agent + Conflict Handler Agent resolve contradictions using the Passage Layer as evidence | MemoPad **accumulates all observations**. If the same entity is updated twice with conflicting info, both facts live side-by-side. No detection, no resolution. |
| **P3** | **Fragmentation** — "Newton" and "Isaac Newton" become disconnected nodes | Ontology Layer enforces a global entity taxonomy; entity aliases are normalized at extraction time | MemoPad uses **permalink normalization** (slugification) which is purely syntactic. "Isaac Newton" and "Newton" resolve to different slugs → separate entities. No semantic deduplication. |

---

## 2. MemGraphRAG Architecture vs MemoPad Architecture

### MemGraphRAG — Three-Layer Memory

```
Ontology Layer  ← stable schemas: country→capital, person→job  (+ extraction frequency)
Factual Layer   ← instantiated triplets: (Isaac Newton, born, 1643)
Passage Layer   ← raw source passages for grounding / conflict evidence
```

### MemoPad — What Maps to What

| MemGraphRAG Layer | MemoPad equivalent | Gap |
|---|----|---|
| **Ontology Layer** | Nothing – the LLM decides observation `[category]` labels ad-hoc | No schema registry; categories are free-form strings |
| **Factual Layer** | `Observation` rows (`- [category] content`) in SQLite/Postgres | Exists, but no conflict tracking. Duplicate/contradicting facts merge silently. |
| **Passage Layer** | The raw markdown file on disk + `content_snippet` in `SearchIndexRow` | File is the ground truth but it is **never consulted during conflict resolution** because no conflict resolution exists |

### MemGraphRAG — Three Agents

| Agent | Role | MemoPad equivalent |
|---|---|---|
| **Extraction Agent** | Populates all three layers from unstructured docs | `assimilate` MCP tool + `entity_service.py` |
| **Conflict Detector** | Scans Factual Layer for contradictions | **Does not exist** |
| **Conflict Handler** | Reads Passage Layer, decides which fact is correct | **Does not exist** |

### MemGraphRAG — Retrieval Pipeline

| Step | What it does | MemoPad equivalent |
|---|---|---|
| Multi-layer retrieval | Fetches candidate schemas, facts, passages | `search_service.search()` — FTS / semantic hybrid, but single-layer |
| Structure-aware node init | Suppresses hub nodes (e.g. generic "person"), boosts rare passage nodes | No hub suppression. All nodes treated equally. |
| Personalized PageRank | Flows semantic energy from seed nodes to globally important paths | `context_service.find_related()` uses a recursive CTE (BFS), **not** PageRank |

---

## 3. Gap Analysis — What MemoPad Is Missing

### Gap 1 — No Observation Schema / Ontology Registry  *(P1 + P3)*

**Impact:** The LLM is free to invent any `[category]` label. Over time a project accumulates dozens of near-identical categories (`[note]`, `[notes]`, `[Note]`, `[observation]`). This is P3 fragmentation at the schema level. It also enables P1 noise — irrelevant categories are never filtered.

**What's needed:** A per-project `ObservationSchema` registry listing canonical categories and their aliases. When a new observation arrives, it is matched (fuzzy or embedding-based) against the registry. Unknown low-frequency categories are flagged.

---

### Gap 2 — No Conflict Detection  *(P2)*

**Impact:** When `assimilate` re-processes an updated document (or a second document covers the same entity), contradicting observations accumulate silently.  
Example: One note says `- [status] active`, another says `- [status] deprecated`. Both live in the DB. Retrieval returns both; the LLM sees contradictory context.

**Current code path:** `entity_service.py` `upsert_entity()` → replaces observations wholesale when a file is re-synced. This is **one file's** conflict path, not **cross-entity** conflict detection.

**What's needed:** A conflict scanner that, on observation write, checks for existing observations on the same entity in the same category with semantically different content. Could be a background task triggered by `index_entity_data()`.

---

### Gap 3 — No Conflict Resolution / Provenance  *(P2)*

**Impact:** Even if a conflict is detected, there is no record of which source is more authoritative or more recent.

**What's needed:** An `observation_source` or `provenance` field on `Observation` linking back to the originating file/chunk. When two observations conflict, the handler can compare source timestamps or source quality signals.

---

### Gap 4 — No Entity Deduplication / Alias Resolution  *(P3)*

**Impact:** `search_service._generate_variants()` creates text variants for FTS but does not resolve entity aliases. "Newton" and "Isaac Newton" → two separate `Entity` rows, no link between them.

**Current partial mitigation:** WikiLink `[[Target]]` matching in `relation_plugin` (link resolver) can connect by title, but only if the LLM writes the exact title.

**What's needed:** An alias table (`EntityAlias`) or a merge operation that consolidates duplicates. MemGraphRAG uses its Ontology Layer frequency counts to decide when two entities are the same.

---

### Gap 5 — BFS Context Traversal vs PageRank  *(Retrieval quality)*

**Impact:** `context_service.find_related()` uses a recursive CTE that does BFS up to `max_depth` hops. This gives equal weight to highly-connected hub nodes (e.g. "Project", "Meeting") and to rare, information-dense nodes. MemGraphRAG's Personalized PageRank explicitly down-weights hub nodes and up-weights rare passages.

**What's needed:** A scoring pass after BFS that penalizes nodes with high in/out-degree (generic hubs) and boosts nodes with few connections and high information content.

---

## 4. Prioritized Adoption Proposals

### 🟥 Priority 1 — Conflict Detection on Observation Write *(Gap 2)*

**Why first:** This is the most impactful problem. Silent contradictions corrupt LLM context and are invisible to users.

**Proposal:**  
- Add a `conflict_score: float | None` and `conflicting_observation_id: int | None` FK to `Observation`.
- In `entity_service.py`, after observations are written, run a lightweight check: for each observation, fetch existing observations in the same `category` on the same entity. If content diverges significantly (Levenshtein distance > threshold, or embedding cosine distance < threshold), mark both as conflicting.
- Expose conflicts in `read_note` / `build_context` output so the LLM sees a conflict flag and can resolve it in the next interaction.

**Effort:** Medium. Requires one migration, ~100 LOC in `entity_service.py`, one helper in `search_service.py` or a new `conflict_service.py`.

---

### 🟧 Priority 2 — Observation Schema Registry *(Gap 1 + P3 at schema level)*

**Why second:** Prevents noise accumulation before it happens; cheaper than cleaning it up later.

**Proposal:**  
- Add a `ObservationSchema` table: `(id, project_id, name, aliases: JSON, frequency: int)`.
- On observation write, look up `category` against the registry. If found, normalize to canonical name (e.g. map `"notes"` → `"note"`). If not found, create a new entry with `frequency=1`.
- Add an MCP tool `list_observation_schemas()` so the LLM can see what schemas exist before writing.
- Optionally: expose a "noise threshold" — categories with `frequency < N` are surfaced as candidates for pruning.

**Effort:** Medium-low. One migration, small change to `entity_service.upsert_observations()`, one new MCP tool.

---

### 🟨 Priority 3 — Entity Alias / Deduplication Support *(Gap 4)*

**Why third:** High-value but harder to implement correctly without breaking existing links.

**Proposal:**  
- Add an `EntityAlias` table: `(id, entity_id, alias, source)`.
- Populate aliases from frontmatter (`aliases:` key — Obsidian convention) and from WikiLink resolutions that produced fuzzy matches.
- In `link_resolver.py`, when a `[[Target]]` fails exact match, fall back to alias search.
- Optionally add a `memopad tool merge-entities` CLI command for manual deduplication.

**Effort:** Medium. Migration + changes to `link_resolver.py` + `entity_repository.py`.

---

### 🟩 Priority 4 — Hub-Aware Context Scoring *(Gap 5)*

**Why last:** Quality-of-life improvement; current BFS is functional but not optimal.

**Proposal:**  
- After `find_related()` returns rows, compute a simple **inverse degree score**: `score = 1 / sqrt(in_degree + out_degree)`.
- Multiply by a base relevance score (depth penalty already applied).
- Re-rank results before returning to the MCP tool.
- No schema changes needed; this is a pure query/post-processing change in `context_service.py`.

**Effort:** Low. ~30 LOC change in `context_service.py` + one aggregate query for degree.

---

## 5. What MemGraphRAG Does That MemoPad Doesn't Need

| MemGraphRAG feature | Reason MemoPad can skip it (for now) |
|---|---|
| Full LLM-based Extraction Agent | MemoPad delegates extraction to the LLM via MCP tools; it doesn't need to be autonomous |
| Conflict Handler with LLM arbitration | A simpler rule-based conflict flag surfaced to the user is sufficient; LLM arbitration adds cost |
| Personalized PageRank full implementation | Full PPR is a graph computation library concern; hub-aware scoring achieves 80% of the benefit |
| Ontology extraction from corpora | MemoPad's manually curated `[category]` system is already better for personal knowledge than auto-extracted ontologies |

---

## 6. Summary Table

| Problem | MemGraphRAG's solution | MemoPad today | Proposed fix |
|---|---|---|---|
| Noise (irrelevant facts) | Ontology Layer schema validation | None | ObservationSchema registry (P2) |
| Contradictions | Conflict Detector + Handler Agents | Silent accumulation | Conflict detection on write (P1) |
| Fragmentation (entity aliases) | Ontology entity taxonomy | Permalink normalization only | EntityAlias table + fuzzy resolution (P3) |
| Hub node pollution in retrieval | Hub suppression + PageRank | Equal BFS weights | Hub-aware inverse-degree scoring (P4) |
| Source grounding | Passage Layer (original text) | Markdown files on disk (not linked to observations) | Provenance FK on Observation (part of P1) |

---

> [!NOTE]
> MemoPad's **local-first, file-as-source-of-truth** design is a strength MemGraphRAG doesn't share. MemGraphRAG requires heavy indexing with an LLM at ingest time. MemoPad's proposals above are all lightweight (rule-based or lightweight embedding) and compatible with the existing sync architecture.

> [!IMPORTANT]
> The highest-leverage single change is **conflict detection on observation write** (Priority 1). Silent contradictions are the most common cause of degraded LLM context quality in long-running MemoPad projects.
