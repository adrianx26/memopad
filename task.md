# Execution Checklist

## 1. Batch Import
- `[x]` Create `MarkdownImporter` in `src/memopad/importers/markdown_importer.py`
- `[x]` Create MCP tool `batch_import_directory` in `src/memopad/mcp/tools/batch_import.py`
- `[x]` Register tool in `src/memopad/mcp/tools/__init__.py`

## 2. Auto-tagging / Categorization
- `[x]` Create MCP tool `auto_tag_note` in `src/memopad/mcp/tools/auto_tag.py` to fetch note and instruct host LLM to tag it
- `[x]` Register tool in `src/memopad/mcp/tools/__init__.py`

## 3. Relation Extraction
- `[x]` Create MCP tool `extract_relations` in `src/memopad/mcp/tools/relation_extractor.py` to fetch note and all entity titles, instructing host LLM to add wikilinks
- `[x]` Register tool in `src/memopad/mcp/tools/__init__.py`

## 4. Memory Summarization
- `[x]` Create MCP tool `get_relevant_context` in `src/memopad/mcp/tools/memory_summarizer.py`
- `[x]` Register tool in `src/memopad/mcp/tools/__init__.py`

## 5. Verification
- `[x]` Verify new MCP tools are registered and functional
- `[x]` Ensure LLM-centric logic runs without errors (Ruff / Pyright)
