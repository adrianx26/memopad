# MemGraphRAG vs MemoPad — Three-Column Comparison

> **Column guide**
> - **MemGraphRAG** — the KDD 2026 research system
> - **MemoPad (today)** — current codebase as-is
> - **MemoPad (after proposals)** — after implementing the four prioritized proposals from the analysis

---

## 🏗️ Memory / Storage Architecture

| Dimension | MemGraphRAG | MemoPad (today) | MemoPad (after proposals) |
|-----------|-------------|-----------------|---------------------------|
| **Memory layers** | 3 explicit layers: Ontology, Factual, Passage | 2 implicit layers: markdown files (passage) + SQLite/Postgres index (factual) | 3 aligned layers: `ObservationSchema` table (ontology) + `Observation` rows (factual) + markdown files (passage) |
| **Ontology / Schema layer** | Stores stable relation schemas with extraction frequency counts | ❌ None — `[category]` labels are free-form strings chosen by the LLM | ✅ `ObservationSchema` table per project; canonical names + aliases + frequency; auto-normalizes on write |
| **Factual layer** | Entity–relation triplets in a structured graph DB | `Observation` rows (`- [category] content`) in SQLite/Postgres | Same, enriched with `conflict_score` + `conflicting_observation_id` FK + `provenance` FK |
| **Passage / evidence layer** | Raw source text chunks stored explicitly for grounding | Markdown files on disk (source of truth) but not linked to individual observations | Markdown files remain source of truth; `provenance` FK on `Observation` points to originating file section |
| **Entity model** | Named entities with type taxonomy from Ontology Layer | `Entity` with `title`, `permalink`, `entity_type` — no alias system | `Entity` + `EntityAlias` table (aliases from frontmatter + fuzzy WikiLink fallback) |
| **Relation model** | Typed triplets: (subject, predicate, object) | `Relation` rows with `relation_type`, `from_id`, `to_id`, `to_name` | Same — unchanged; alias resolution improves `to_id` resolution rate |
| **Source of truth** | Graph database + passage store | Markdown files on disk (files win over DB on conflict) | Markdown files on disk (unchanged — core MemoPad principle preserved) |

---

## 🤖 Agent / Processing Architecture

| Dimension | MemGraphRAG | MemoPad (today) | MemoPad (after proposals) |
|-----------|-------------|-----------------|---------------------------|
| **Extraction agent** | Dedicated LLM agent: extracts triplets and routes them into all 3 layers | LLM uses `write_note` / `assimilate` MCP tools; parser extracts observations + relations from markdown syntax | Same — no change; lightweight schema normalization added transparently in `entity_service.py` |
| **Conflict detector** | Dedicated agent monitors Factual Layer for incoming contradictions | ❌ None — contradictions accumulate silently | ✅ Rule-based conflict checker runs on observation write; flags when same entity + same category has semantically divergent content |
| **Conflict resolver** | Dedicated LLM agent reads Passage Layer, picks the correct fact | ❌ None | ✅ Conflict flags surfaced to the LLM via `read_note` / `build_context`; LLM resolves conflicts in the next interaction (human-in-the-loop, lower cost than autonomous agent) |
| **Autonomy level** | Fully autonomous multi-agent pipeline (no human required) | Human / LLM operates tools manually via MCP | Same — MemoPad intentionally keeps humans in the loop |
| **LLM dependency at ingest** | Heavy — Extraction Agent calls LLM for every document chunk | Light — markdown syntax carries structure; LLM only needed for creative writing, not parsing | Light — unchanged |
| **Cost per ingest** | High (multiple LLM calls per chunk for extraction + ontology placement) | Low (file sync + SQLite upsert) | Low — schema normalization is a DB lookup, not an LLM call |

---

## 🔍 Search & Retrieval

| Dimension | MemGraphRAG | MemoPad (today) | MemoPad (after proposals) |
|-----------|-------------|-----------------|---------------------------|
| **Search modes** | Multi-layer: schema candidates + fact candidates + passage candidates combined | FTS (SQLite FTS5 / Postgres tsvector), semantic (embedding cosine), hybrid (RRF fusion) | Same three modes + new `conflict_aware` flag that excludes or highlights conflicting observations |
| **Graph traversal** | Personalized PageRank (PPR) over heterogeneous graph — global importance flow from seed nodes | Recursive CTE (BFS) up to configurable `max_depth` hops | BFS + hub-aware inverse-degree scoring post-pass: `score × 1/√(in+out degree)` |
| **Hub node handling** | Explicit suppression — generic hub categories (e.g. "person", "particle") are down-weighted | ❌ All nodes treated equally — a node with 500 relations gets same weight as one with 2 | ✅ High-degree nodes penalized in re-ranking; information-dense leaf nodes boosted |
| **Entity resolution at query time** | Ontology Layer aliases normalize query terms before graph lookup | Exact permalink/title match; `_generate_variants()` creates text variants for FTS only | ✅ Alias table searched as fallback; fuzzy match on `EntityAlias` before returning "not found" |
| **Passage grounding** | Retrieved passages are included as evidence alongside facts | Markdown file content is returned via `read_note` / `build_context` | Same + provenance link on observations allows fetching the exact originating passage |
| **Retrieval speed** | Fast at query time (heavy compute invested at index time) | Fast — FTS5 is sub-millisecond; semantic requires embedding model | Fast — schema lookup and degree scoring add negligible overhead |

---

## ⚠️ Conflict & Quality Management

| Dimension | MemGraphRAG | MemoPad (today) | MemoPad (after proposals) |
|-----------|-------------|-----------------|---------------------------|
| **Contradiction detection** | ✅ Automatic — Conflict Detector Agent scans every new fact against the Factual Layer | ❌ Not detected — both "active" and "deprecated" observations survive side-by-side | ✅ Detected on write — `conflict_score` set when same entity + same category diverges beyond threshold |
| **Contradiction resolution** | ✅ Automatic — Conflict Handler reads source passages, picks winner | ❌ Never resolved | ✅ Semi-automatic — conflict flag exposed to LLM; LLM or user resolves; resolved observations marked `conflict_resolved=True` |
| **Noise filtering** | ✅ Ontology schema validation — low-frequency / off-schema extractions suppressed | ❌ All LLM-written observations accepted unconditionally | ✅ Unknown categories flagged with `frequency=1`; tool `list_observation_schemas()` lets LLM see canonical set before writing |
| **Duplicate entity prevention** | ✅ Ontology entity taxonomy prevents "Newton" and "Isaac Newton" from being separate nodes | ❌ Different slugs = different entities; no semantic deduplication | ✅ `EntityAlias` table + fuzzy resolution in `link_resolver.py`; `memopad tool merge-entities` CLI for manual consolidation |
| **Provenance / source tracking** | ✅ Every fact linked to originating passage chunk | ❌ Observations have no back-link to originating document section | ✅ `provenance` FK on `Observation` → `file_path` + optional char offset |

---

## 🧠 Knowledge Representation

| Dimension | MemGraphRAG | MemoPad (today) | MemoPad (after proposals) |
|-----------|-------------|-----------------|---------------------------|
| **Knowledge format** | Entity–relation triplets (subject, predicate, object) | Markdown: `- [category] content` (observations) + `- relation_type [[Target]]` (relations) | Same markdown format — backward compatible |
| **Schema enforcement** | Strong — Ontology Layer validates all extractions | None — free-form `[category]` strings | Soft — canonical schema suggested; LLM can still write new categories but they are flagged |
| **Human readability** | Low — stored in graph DB, not human-readable files | ✅ High — every entity is a plain markdown file editable in any editor | ✅ Same — no change to file format |
| **Entity types** | Taxonomy-defined (person, organization, place, event, …) | Free-form `entity_type` field (defaults to "note") | Same — `entity_type` kept free-form; `ObservationSchema` adds soft schema at observation level |
| **Multi-hop reasoning** | Enabled by PPR over heterogeneous graph | Enabled by recursive CTE traversal | Same traversal + better ranked results via hub-aware scoring |

---

## 🏠 Deployment & Integration

| Dimension | MemGraphRAG | MemoPad (today) | MemoPad (after proposals) |
|-----------|-------------|-----------------|---------------------------|
| **Deployment model** | Research system — Python scripts, no server, no API | Local-first MCP server + optional cloud sync + FastAPI REST API | Same — proposals are internal service changes, no deployment model change |
| **LLM requirement** | Requires external LLM API (GPT-4o Mini used in benchmarks) | Requires LLM via MCP client (Claude, etc.) | Same — no additional LLM calls for schema normalization or conflict detection |
| **Database** | Not specified (graph DB implied) | SQLite (default, local) or Postgres (cloud/team) | Same — new tables added via Alembic migrations; both backends supported |
| **File system integration** | ❌ No — graph DB only, no human-readable files | ✅ Yes — markdown files are source of truth, DB is a derived index | ✅ Same — proposals add DB tables but markdown files remain primary |
| **MCP protocol** | ❌ No MCP support | ✅ Full MCP server — 15+ tools, 4 prompts, resources | ✅ Same + new `list_observation_schemas()` tool and conflict information in existing tool outputs |
| **Offline / local-first** | ❌ Requires LLM API at ingest | ✅ Fully local — sync and indexing work offline | ✅ Same |
| **Open source** | ✅ GitHub: XMUDeepLIT/MemGraphRAG | ✅ GitHub: basicmachines-co/basic-memory | ✅ Same |

---

## 📊 Benchmark / Performance Profile

| Dimension | MemGraphRAG | MemoPad (today) | MemoPad (after proposals) |
|-----------|-------------|-----------------|---------------------------|
| **Benchmark target** | HotpotQA, MuSiQue — academic multi-hop QA | Personal knowledge management — not benchmarked on academic QA | Not benchmarked; qualitative improvement expected |
| **Retrieval accuracy** | Outperforms GraphRAG, HippoRAG, LightRAG, RAPTOR | Not measured against academic baselines | Expected improvement on multi-hop lookups where aliases/conflicts previously degraded results |
| **Indexing cost** | High (LLM calls per chunk) | Low (file watch + SQLite upsert, sub-second) | Low — schema lookup adds < 1ms per observation |
| **Query latency** | Fast (heavy pre-computation at index time) | Fast (FTS5 < 5ms, semantic < 50ms) | Fast — hub scoring adds one aggregate query, ~2ms overhead |
| **Scalability** | Tested on corpus-scale QA datasets | Tested on personal knowledge bases (100s–1000s of notes) | Same scale profile |

---

## ✅ What MemGraphRAG Has That MemoPad Will Gain

| Feature | Before | After |
|---------|--------|-------|
| Contradiction detection | ❌ | ✅ |
| Contradiction resolution surface | ❌ | ✅ (semi-auto) |
| Observation schema / noise gate | ❌ | ✅ |
| Entity alias resolution | ❌ | ✅ |
| Hub-aware retrieval scoring | ❌ | ✅ |
| Source provenance on observations | ❌ | ✅ |

## ✅ What MemoPad Has That MemGraphRAG Doesn't

| Feature | MemoPad | MemGraphRAG |
|---------|---------|-------------|
| Human-readable file storage | ✅ | ❌ |
| MCP protocol integration | ✅ | ❌ |
| Local-first / offline operation | ✅ | ❌ |
| Cloud sync (rclone bisync) | ✅ | ❌ |
| Obsidian canvas visualization | ✅ | ❌ |
| CLI + REST API | ✅ | ❌ |
| No LLM required at ingest | ✅ | ❌ |
| Low operational cost | ✅ | ❌ (heavy LLM use) |
