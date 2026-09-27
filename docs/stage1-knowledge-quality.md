# Stage 1: Knowledge Extraction and Conflict Quality

**Status:** Complete
**Source:** `memopad-rework.md` and the supplied Priority 1 checklist

## Changes

- Code assimilation now creates a `Code Architecture` note with structured
  `[architecture]`, `[api]`, `[configuration]`, and `[rule]` observations.
  Python is parsed with `ast` without executing it; common declarations in
  JavaScript/TypeScript, Go, Rust, Java, C#, and Ruby are extracted with bounded
  patterns. Unparseable Python and non-code pages are skipped. Output is
  deduplicated, capped at 200 observations, and subjected to the standard note
  content limit.
- The four code-observation categories are distillable by default and carry
  category-specific confidence. Assimilated code structure can therefore
  produce L1 facts while the raw code pages remain available as source.
- Relation and tag MCP tools now ask MemoPad for concrete local suggestions
  instead of returning long instructions containing the entire note. The API
  searches a bounded FTS candidate set, matches note mentions against existing
  entity titles and explicit aliases, and aggregates tags from related indexed
  notes. Suggestions are not applied automatically; the caller retains control
  over edits.
- Conflict detection no longer treats textual or vector distance as evidence
  of contradiction. It marks matching negated claims and explicit opposing
  lifecycle/status terms only, preserving bidirectional conflict persistence.

## Validation

The focused assimilation, distillation, conflict, local-extraction, API endpoint,
and typed MCP client suite passed (124 tests). Pyright passed on all Stage 1
implementation files. Ruff passed on the changed behavior; existing unused-code
findings in the router and distillation test module were left untouched.

## Related checklist items verified as already present

- `ConflictServiceDep` is exported by `memopad.deps` and included in `__all__`.
- Watch service tests already cover configured watchfiles debounce, active
  project reloads, and add/modify/delete/move change handling. The status JSON
  is write-only; there is no persisted sync-state restoration path to test.
- `Importer.import_data` and `Importer.handle_error` are abstract methods;
  their `pass` bodies are intentional abstract stubs rather than unfinished
  importer implementations.

No changes were needed for these items.
