# MemoPad Implementation Roadmap

**Purpose:** Current status and remaining work after Stages 0–2 and the local
decision-model research. This is the working roadmap for the previously shared
P0–P3 implementation list; it narrows and corrects stale L0–L3 plan items.
**Scope:** This document describes implementation work. It does not enable an
inference provider or change runtime behavior.

## Current state

| Stage | Status | What is present |
|---|---|---|
| 0. Runtime and OS stability | **Complete** | BOM prevention, subprocess/event-loop safeguards, SQLite write serialization and contention coverage; details in [Stage 0](stage0-hardening.md). |
| 1. Knowledge quality | **Complete** | Structured code observations, local relation/tag suggestions, and conservative conflict detection; details in [Stage 1](stage1-knowledge-quality.md). |
| 2. Retrieval and local-decision seams | **Implementation complete; model-quality rollout gate remains** | Distilled-first retrieval, confidence threshold, shared total budget, and optional read-only local decision seams. Model accuracy is not claimed; evaluation needs representative labeled data and a selected local model. |
| 3. Tiered retrieval budgets | **In progress** | Optional per-level token caps and higher-tier-first ordering are implemented, with combined confidence/provenance coverage. Package metadata, lockfile, CI, and project guidance now agree on Python 3.12+. Evaluation-derived defaults and supported-suite verification remain; see [Stage 3](stage3-tiered-retrieval.md). |
| 4. Distillation operations | **Operational hardening implemented; maintenance policy pending** | Per-project scheduler serialization/retry, atomic fail-fast watermarks, and watcher idle dispatch are implemented. Retention thresholds, dry-run/pruning, end-to-end trigger audit, and representative-volume verification remain; see [Stage 4](stage4-distillation-operations.md). |
| 5. Local diagram extraction | **Deterministic text-diagram slice implemented; visual pipeline pending** | A local Mermaid flowchart parser emits typed, provenance-bearing review suggestions and warnings. Raster/SVG extraction, ingestion wiring, and labeled quality evaluation remain; see [Stage 5](stage5-diagram-extraction.md). |
| L0–L3 deterministic distillation | **Core engine present; validate operational completeness** | `FactExtractor` / `CodeExtractor`, L1 fact creation and deduplication, L2 clustering, L3 persona aggregation, provenance, search indexing, a scheduler/dispatcher, and manual distillation routes are present. Cadence counters are process-local, not persisted; pass watermarks are persisted. Scale and end-to-end operation still need validation before treating it as production-complete. |
| Local decision-provider foundation | **Service seams implemented; rollout pending** | A typed, provider-neutral batch contract and loopback-only TypeSafe-compatible HTTP adapter support read-only relation/context decisions by injection. No application composition or public endpoint is wired; no model is bundled/downloaded or graph write enabled. |
| Visual and diagram extraction | **Not implemented as an end-to-end pipeline** | Code-architecture assimilation and CodeGraph are separate features; they do not implement general image/diagram understanding. |
| Decay, pruning, and full productization | **Remaining** | Define safe lifecycle rules, complete strict retrieval allocation, broaden regression/scale validation, and document operational behavior. |

The design patterns and workflow boundaries extracted from Ollaya, Kev, Nimble,
Jevlike, mini-Jev, jeff, and SemIf are in
[Local Decision Workflows](local-decision-workflows.md). The main resulting
change is sequencing: define and evaluate MemoPad tasks before choosing or
enabling a model; use a local compatible service as an optional adapter, not as
a new core dependency.

## Implementation progress

### Completed foundation: typed local decision contract

Added `src/memopad/services/local_decision_provider.py` with:

- Provider-neutral Pydantic contracts for batched `choice`, `noul`, and `score`
  questions over one shared state, including option/level and unique-ID bounds.
- A `DecisionProvider` protocol and normalized typed answer/result objects.
- `LocalSystemOneProvider`, an async adapter for `/v1/systemone` on an already
  running local server. It only accepts localhost/loopback origins, disables
  environment proxies for its owned HTTP client, rejects redirects, and
  validates answer IDs, types, ranges, probabilities, and usage before returning.
- Explicit transport and malformed-response errors. The adapter does not retry,
  silently fall back, download weights, select a model, or write to MemoPad.

`tests/services/test_local_decision_provider.py` covers valid batched requests,
serialization, loopback restrictions, criteria validation, malformed answers,
HTTP errors, and client lifecycle expectations. Targeted validation: 18 tests
passed, Ruff passed, and Pyright reported zero errors.

`LocalExtractionService` has an internal `_decide_relation` helper used by
`suggest_relation_target` as a read-only service seam: the existing FTS/alias
search supplies candidates, a bounded excerpt supplies context, and the local
provider can select only an offered candidate or `none`. No relation is written,
and no public endpoint calls this path. Service regression tests cover candidate
restriction, abstention, bounded context, and invalid inputs. The combined
provider and relation-service selection passed 26 tests; Ruff passed and
Pyright reported zero errors.

`ContextService` now has an optional, injected, read-only reranking seam. A
caller must provide an explicit natural-language `query`; URI lookups and
recent-activity requests remain unchanged. It scores at most 20 already
retrieved primary candidates in one batch, then reorders only within matching
result type, memory-level, and validated-skill groups. The existing total token
budget is applied after ranking. It is not wired from application composition
roots or exposed through an API/MCP tool.
Six focused tests cover within-level ordering, level isolation, disabled
behavior, the 20-candidate bound, malformed provider output, and budget
application after ranking.

**Stage 2 implementation is complete.** Production rollout of its optional
decision workflows remains gated on frozen MemoPad evaluation fixtures and
deterministic baselines; composition-root configuration/lifecycle wiring;
verification of the chosen model's local availability and weight license; and
evaluation of both read-only service seams. Neither path is exposed to users,
and the adapter is not user-configurable through MemoPad settings until those
gates are implemented and tested. Synthetic unit tests validate contracts and
safety, not model accuracy.

## Remaining implementation

### Phase 2 — Evaluated local decision workflows

**Goal:** Add bounded typed judgments where they improve MemoPad's own
workflows, without confusing decision scoring with fact generation.

1. **Task 2.1 — Complete pluggable decision/fact-selection integration**
   - The provider protocol, typed request/response contract, and loopback-only
     HTTP adapter are implemented. Remaining work is composition-root wiring,
     configuration validation, provider lifecycle, and a Windows smoke test.
   - Keep it distinct from `FactExtractor`: the deterministic extractor creates
     fact candidates; an optional `System1FactExtractor` may filter or classify
     those candidates but cannot create unsupported fact text.
   - Model choice, model-weight license review, and local performance
     verification are deployment gates; no model weights are bundled by MemoPad.

2. **Evaluate against MemoPad-specific fixtures before integration**
   - Freeze de-identified examples for relation choice, context reranking, and
     fact retention/category selection.
   - Split by source entity or project to prevent near-duplicate leakage.
   - Lock a test partition and record a deterministic baseline before tuning
     prompts, model, or thresholds.
   - Measure ranking separately from confidence: Recall@k/MRR or nDCG for
     ranking; precision/coverage and Brier/ECE or error-at-coverage for
     probability thresholds.

3. **Add an optional local provider adapter**
   - The vendor-neutral protocol and loopback HTTP adapter are implemented.
     Next, verify Windows setup, selected model availability,
     request/response compatibility, limits, queue behavior, and exact
     model-weight license before wiring runtime settings.
   - Keep HTTP transport, server-specific fields, and provider model names
     inside the adapter. Continue using fakes for offline tests.
   - Do not add a heavy ML runtime to MemoPad's default installation.

4. **Task 2.2 — Candidate prefiltering and context reranking**
   - Keep deterministic local search as the prefilter; do not ask the model to
     scan the whole graph.
   - The injected `ContextService` seam scores at most 20 existing primary
     results and only reorders within the same type, level, and validated-skill
     group. It receives a query explicitly; existing URI and recent-activity
     calls remain deterministic.
   - The confidence policy is applied before ranking, and the existing total
     token budget is applied afterward. Composition wiring and evaluation are
     still required before exposure.
   - Replace the unqualified sub-50-ms target with an explicit benchmark on
     supported hardware. Record cold start, warm p50/p95, candidate count,
     question batch size, input length, and concurrency. Set an SLO only after
     measurement; external projects' hardware timings are not a MemoPad
     guarantee.

5. **Task 2.3 — Complete two-stage edge verification**
   - Stage one retrieves candidate entities deterministically from FTS, aliases,
     and configured embedding search.
   - An injectable, read-only service path now asks a typed `choice` decision
     over locally retrieved candidates with an explicit `none` option and a
     bounded source excerpt.
   - Remaining: evaluate on locked MemoPad fixtures, wire explicit local
     provider configuration into the API composition, expose a read-only route
     only after the evaluation gate, and document Windows/model-license checks.
   - Verify the selected ID belongs to the current project's candidate set and
     return a read-only suggestion with evidence location. No graph write in
     this task.
   - Test same-subject/scope alignment and complementary-fact negatives so a
     topical similarity score alone cannot create or validate an edge.

6. **Ship read-only workflows first**
   - **Relation suggestions:** use local FTS/alias/embedding retrieval to create
     a small same-project candidate list, then ask the provider to select or
     abstain. Return the mention, candidate ID, source location, and score.
   - **Context reranking:** the optional service seam scores only a bounded
     pool returned by existing search; retain entity level, project scope, and
     provenance. Keep it unwired pending evaluation.
   - Neither workflow writes a relation or changes the graph in suggestion mode.

7. **Evaluate optional fact-selection integration**
   - Compare the deterministic `CodeExtractor` baseline against bounded
     keep/category judgments on the frozen MemoPad set.
   - Require source-observation IDs for every accepted candidate.
   - Preserve idempotence, provenance, and deterministic behavior when the
     provider is disabled.
   - Do not add free-form fact synthesis under a decision-model interface.

8. **Gate any future model-assisted writes**
   - Start with user confirmation or draft suggestions.
   - Automatic mutation requires a workflow- and model-specific evaluated
     threshold, explicit feature flag, persisted source/model/workflow
     provenance, and a tested undo/reconciliation path.
   - Re-evaluate after changes to model revision, prompt, provider settings, or
     candidate-generation logic.

**Phase 2 exit criteria**

- Provider contract tests pass offline, including malformed output, timeout,
  unknown option, abstention, and provider error cases.
- The local integration has a Windows smoke test and does not affect startup
  when disabled or unavailable.
- Each read-only workflow beats or usefully complements its deterministic
  baseline on locked MemoPad evaluation data; uncertainty and failure are
  reported rather than hidden.
- No provider response can reference another project's candidate or mutate the
  graph in read-only mode.

### Phase 3 — Strict retrieval budget and verification

**Implemented so far:** optional maximum token caps are available through
`recall_tier_budget_l3`, `recall_tier_budget_l2`, `recall_tier_budget_l1`, and
`recall_tier_budget_l0`. `None` leaves that tier uncapped, `0` omits it, and a
positive value limits its approximate text tokens. When any tier cap is set,
items are budgeted in L3 → L2 → L1 → L0 order while preserving their
within-tier order. The shared `recall_max_tokens` is an independent hard
ceiling; unused tier caps are not borrowed or redistributed. Observations and
related rows consume the budget of their primary item. A relation uses its
highest-level endpoint, and a validated skill is L2 when Skills are enabled.
When all caps are unset, existing shared-budget behavior is unchanged.

No numeric default allocations are imposed without MemoPad retrieval-quality
measurements. Combined confidence/provenance verification is now covered;
evaluation-derived recommendations and supported-suite verification remain.

1. **Task 3.1 — Implement a tiered budget on top of the existing total budget**
   - Configurable per-tier caps are implemented. Recommend values from MemoPad
     evaluation before setting defaults. These are maximum caps, not reserved
     allocations; unused capacity is neither borrowed nor redistributed.
   - Preserve the existing total recall cap as a hard ceiling.
   - Keep ordinary retrieval distilled-first. Raw L0 should be included only
     under an explicit evidence/detail policy; exact-URI requests and
     `drill_down` must remain able to retrieve the requested ground truth.
   - Apply level confidence thresholds before spending budget. Enforce token
     accounting consistently across primary entities, observations, and related
     results; retain visible truncation behavior.

2. **Add exact behavior tests and evaluation**
   - Test each tier at empty, under-budget, exact-boundary, over-budget, and
     missing-tier conditions.
   - Test the total ceiling, reservation/borrowing rules, confidence exclusion,
     requested-L0 exception, and provenance through drill-down.
   - Compare retrieval relevance and answer quality with the current shared
     budget baseline; measure actual tokenization on representative text in
     addition to the approximate estimator.

3. **Task 3.2 — Complete exports, compatibility, and coverage**
   - Audit dependency exports and MCP/API/CLI surfaces for services added by
     this roadmap; retain existing compatibility shims where required.
   - The supported Python floor is aligned at 3.12+ across package metadata,
     lockfile, CI, and project guidance. Supporting 3.11 or earlier would
     require a separate compatibility decision and validation.
   - Add tests for changed public behavior and close relevant baseline test
     blockers separately from roadmap regressions.

**Phase 3 exit criteria**

- The total budget is never exceeded under the defined accounting contract.
- L3/L2/L1 priority and the explicit L0 evidence exception are covered by
  tests and retrieval evaluation.
- Project exports and supported Python versions match package metadata and CI.
- Targeted tests, typing, lint, and the repository's supported SQLite test
  suite pass; platform-specific Windows checks are recorded.

### Phase 4 — Distillation operations and maintenance

The deterministic L0–L3 engine exists, but operational completeness still needs
verification. Treat these as separate tasks, not as missing model features:

1. **Scheduler and incremental-state hardening**
   - Implemented per-project trigger serialization, rollback of cadence state
     on callback failure/cancellation, surfaced dispatcher failures, atomic
     watermark replacement, strict state parsing, and watcher idle polling.
   - Restart behavior is explicit: cadence counters/warmup cursors are
     process-local and reset; persisted pass watermarks remain. A failed pass
     does not advance its watermark and idempotent partial work may be repeated.
   - Still verify creation, import, sync, watch, and manual paths end to end,
     including cancellation and large backlogs; cadence state is not a durable
     ingestion queue.

2. **Decay, reconfirmation, and pruning**
   - Define decay inputs, thresholds, exclusions, and retention rules before
     implementation. No arbitrary policy or automatic pruning is enabled.
   - Provide a dry-run report with entity IDs and source provenance before
     enabling mutation.
   - Never prune L0 source files automatically. Derived records must remain
     reversible through provenance, and pruning must be idempotent and
     recoverable.
   - Validate on a copy of representative project data before enabling any
     automatic maintenance.

3. **Operational verification**
   - Run representative-volume tests (including large entity/observation
     counts), end-to-end file↔DB checks, and SQLite/Postgres validation where
     supported.
   - Document measured latency, queue depth, pass size, memory use, and known
     failure modes. Do not adopt a universal sub-50-ms target from external
     model reports.

### Phase 5 — Optional visual and diagram pipeline

Keep this separate from decision-provider integration because vision extraction
and diagram parsing are different capabilities.

1. **Implemented first local slice:** a pluggable extractor contract and a
   deterministic Mermaid flowchart parser emit validated nodes/edges, source
   references, line numbers, confidence/uncertainty, and warnings. Parsing is
   local and dependency-free; results are review-only and never write to the
   graph.
2. Raster images, OCR, SVG geometry, image/page coordinates, and an MCP/API
   suggestion surface remain unimplemented. Define supported formats,
   byte/pixel limits, local runtime/license, and page-reference semantics
   before selecting an extractor.
3. Create a manually labeled evaluation set and measure node/relation
   precision/recall before exposing image extraction or any mutation path.
   Keep raw assets as L0 ground truth and require human approval before graph
   writes.

This phase is not required to complete Stages 0–3 and should not block local
decision workflows or deterministic text distillation.

## Stage 4–5 focused validation

The combined scheduler, distillation-service, watcher-idle, and diagram
extractor selection passed **71 tests**. Ruff lint passed for the changed
Stage 4/5 implementation and test files; Pyright reported zero errors for the
diagram extractor, scheduler, and watcher implementation. The suite emitted
508 existing aiosqlite datetime-adapter deprecation warnings. Full repository
tests and Stage 4 representative-volume/end-to-end trigger validation remain
open.

## Implementation order

1. Close any reproducible test/type failures that block evaluation; keep
   pre-existing defects clearly separated from regressions.
2. Phase 2 provider/model quality evaluation and composition wiring remain a
   rollout gate; they need a selected local model and representative labeled
   MemoPad data.
3. Finish Phase 3 tests, provenance/compatibility audits, and evaluation-based
   cap recommendations.
4. Phase 4: verify scheduler operations across all ingestion paths; define a
   retention policy before adding dry-run decay/pruning.
5. Phase 5: evaluate diagram suggestions, then scope local image extraction
   only if the product requirement remains.

The sequence intentionally puts measurement ahead of model selection and
automatic writes. No external benchmark establishes that an option is accurate
for MemoPad's own knowledge graph.

## Related documentation

- [Stage 0: Runtime and Windows Stability](stage0-hardening.md)
- [Stage 1: Knowledge Extraction and Conflict Quality](stage1-knowledge-quality.md)
- [Stage 2: Distilled Context and Retrieval Budgets](stage2-retrieval-quality.md)
- [Local Decision Workflows](local-decision-workflows.md)
- [L0–L3 implementation plan](../memopad-levels-implementation-plan.md)
- [Tb-borrow implementation status](../tb-borrow-progress.md)
