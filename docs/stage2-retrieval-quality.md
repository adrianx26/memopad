# Stage 2: Distilled Context and Retrieval Budgets

**Status:** Implementation complete; optional local-model rollout remains gated on evaluation
**Source:** `memopad-rework.md` and the supplied Priority 2 checklist

## Changes

- Kept the existing level-weighted ranking and extended it to observations and
  relation primaries by using the associated entity's level.
- With levels enabled, ordinary retrieval admits confident L1-L3 entities
  (confidence at or above `recall_min_confidence`, default `0.50`) and validated
  skills. It no longer mixes unrequested raw L0 entities or their links into
  context. An exact memory URI still returns the specifically requested L0
  entity and its observations; related raw notes remain available through the
  existing drill-down flow. Disabling levels retains legacy retrieval behavior.
- Added `recall_max_tokens` (default `4096`) as a shared, approximate text
  budget across ranked primary items, observations, and related results. The
  budget uses MemoPad's existing four-characters-per-token estimator and clips
  the final item with a visible truncation marker when the remaining budget
  allows; `0` explicitly disables it. Fixed metadata and identifiers are outside
  this text estimate.
- Updated `memopad doctor` to show the total budget and confidence threshold.
- Added an optional injected local reranking seam to `ContextService`. It
  requires an explicit natural-language query, scores at most 20 already
  retrieved primary candidates in one batch, and only reorders within matching
  result-type, memory-level, and validated-skill groups. The existing recall
  token budget is applied afterward. No application composition root, API, or
  MCP tool injects or exposes this provider, so existing retrieval stays
  deterministic; MemoPad-specific ranking evaluation is still required before
  enabling it.

Stage 2 implementation work is complete. The optional local decision seams
remain intentionally unwired and unavailable through public API/MCP settings.
Passing their model-quality rollout gate requires de-identified representative
MemoPad relation/ranking examples, frozen deterministic baselines, and a
selected local model whose local availability and exact weight license are
verified. Synthetic unit tests verify contracts and safety, not model accuracy.

## Validation

The combined Stage 1 and Stage 2 focused suite passed: **182 tests**. The
current local-provider, relation-suggestion, and context-reranking focused suite
passed: **32 tests**. Ruff and Pyright passed for the new reranking code and
tests. Two
assimilation integration tests hit SQLite fixture-teardown races when included
in that combined run; each passed when run in isolation. Pyright reported no
errors for the changed implementation files, and Ruff passed on the changed
files after excluding existing unused-import/unused-variable findings.

The broader `tests/services/test_context_service.py` remains blocked by an
existing SQLite `IN ?` syntax error in `_fetch_entity_degrees`; the focused
ranking, budget, skills, trigger-match, and v2 memory-router tests pass.
