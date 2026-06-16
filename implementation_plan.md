# MemoPad Feature Enhancements Plan

This document outlines the proposed implementation plan for the four requested features: Batch Import, Auto-tagging, Relation Extraction, and Memory Summarization.

## User Review Required

Since MemoPad acts primarily as an **MCP Server** (meaning it provides tools to a host LLM like Claude or ChatGPT, rather than calling LLMs itself), some of these features require design decisions on *where* the intelligence should live.

> [!IMPORTANT]
> Please review the "Open Questions" below carefully. Depending on your answers, we will either implement these as backend algorithms, local ML models, or MCP Prompts/Tools that leverage the host LLM.

## Open Questions

1. **Auto-tagging / Categorization Intelligence:**
   - Since MemoPad doesn't directly call outbound LLMs (it serves them), how should auto-tagging work?
     - *Option A (Host LLM):* Create an MCP Prompt (`/auto-tag`) that instructs the host LLM to read notes and apply tags using `edit_note`.
     - *Option B (Local ML):* Add a local lightweight NLP library (like `keybert` or simple TF-IDF keyword extraction) to auto-generate tags when notes are saved.
     - *Option C (External API):* Configure MemoPad to make its own LLM API calls (e.g. via an OpenAI/Anthropic API key stored in config) to categorize notes in the background.

2. **Relation Extraction:**
   - Should relation extraction be fully automatic (silently modifying your note to add `[[Wikilinks]]` when it detects entity titles), or should we provide an MCP tool `suggest_relations(permalink)` that the host LLM can call to see suggestions and decide whether to apply them?

3. **Batch Import Format:**
   - Will the batch import primarily target folders of local Markdown files, or do you have a specific format in mind (like Obsidian vaults, Roam Research exports, etc.)?

## Proposed Changes

### 1. Batch Import

We will add a new `MarkdownImporter` to bulk-import local directories into MemoPad's knowledge graph.

#### [NEW] `src/memopad/importers/markdown_importer.py`
- Extend the `Importer` base class.
- Recursively scan a target directory for `.md` files.
- Read files, preserve existing frontmatter, and use `EntityService` to bulk-resolve permalinks and index them.

#### [NEW] `src/memopad/mcp/tools/batch_import.py`
- Expose a new MCP tool `batch_import_directory(directory_path)` so the LLM can trigger imports of existing local folders.

#### [MODIFY] `src/memopad/mcp/tools/__init__.py`
- Register `batch_import_directory`.

---

### 2. Auto-tagging / Categorization

*(Assuming Option B: Local ML/Keyword Extraction, or an MCP Tool approach)*

#### [NEW] `src/memopad/services/categorization_service.py`
- A service that analyzes text to extract high-frequency keywords or topics.
- We can leverage existing TF-IDF/BM25 logic from the search index, or use a lightweight NLP approach to extract 3-5 tags per note.

#### [NEW] `src/memopad/mcp/tools/auto_tag.py`
- Expose `suggest_tags(permalink)` tool. The LLM can use this to get tag recommendations for a note and apply them via `edit_note`.

---

### 3. Relation Extraction (Wikilink Detection)

#### [NEW] `src/memopad/services/relation_extraction_service.py`
- A service that takes note content and scans it against the known `entity` table (using Aho-Corasick or simple regex on entity titles).
- Identifies exact or fuzzy matches of existing note titles and suggests replacing them with `[[Title]]`.

#### [NEW] `src/memopad/mcp/tools/relation_extractor.py`
- Expose `suggest_relations(permalink)`.
- Returns a list of strings found in the note that match existing entities, along with their line numbers. The LLM can use this to enrich the knowledge graph contextually.

---

### 4. Memory Summarization

#### [NEW] `src/memopad/mcp/tools/memory_summarizer.py`
- Implement an MCP tool `get_relevant_context(query: str, limit: int = 3)`.
- Under the hood, this will call our newly implemented `semantic_search(query, limit=3)` to retrieve the top 3 most semantically relevant notes.
- Instead of returning just search results, this tool will fetch the **full content** of these 3 notes and return them in a structured block.
- The host LLM can then easily synthesize and summarize this context for the user.

#### [NEW] `src/memopad/mcp/prompts/summarize_memory.py`
- Implement an MCP Prompt that the user can trigger from their client (e.g. `Use MemoPad's summarize_memory prompt`). This prompt will instruct the LLM: "Given the user's query, use `get_relevant_context` to fetch the top 3 notes, then provide a synthesized summary."

## Verification Plan

### Automated Tests
- Unit tests for `MarkdownImporter` ensuring bulk file operations succeed and relations are preserved.
- Unit tests for relation string matching logic.
- Tests for `get_relevant_context` to ensure it successfully chains semantic search and content fetching.

### Manual Verification
- Point `batch_import_directory` at a sample vault and verify notes appear in `memopad project list`.
- Ask the LLM to run `get_relevant_context("some topic")` and verify it produces a summary based on the actual top 3 notes.
