# Stage 3: Tiered Retrieval Budgets

**Status:** In progress
**Source:** Stage 3 Task 3.1 in [Implementation Roadmap](implementation-roadmap.md)

## Implemented

MemoPad supports optional per-tier maximum approximate text-token budgets:

| Configuration | Tier |
|---|---|
| `recall_tier_budget_l3` | L3 persona/core |
| `recall_tier_budget_l2` | L2 scenario |
| `recall_tier_budget_l1` | L1 fact |
| `recall_tier_budget_l0` | L0 source/raw |

- `None` means that tier has no separate cap.
- `0` omits items in that tier from the response.
- A positive value limits the tier's approximate text-token contribution.
- Setting at least one tier cap activates L3 → L2 → L1 → L0 budget order.
  Existing within-tier ranking remains stable.
- `recall_max_tokens` remains an independent total ceiling when positive.
  Setting it to `0` disables only the total cap; configured tier caps still
  apply.
- Per-tier limits are caps, not reserved quotas. Unused capacity is not
  transferred to another tier, and the total ceiling may stop output before
  any tier cap is reached.
- A context item's primary tier accounts for its primary text, observations,
  and related rows. A relation primary is assigned to its highest-level
  endpoint. A validated Skill is treated as L2 when Skills are enabled.
- Exact-URI L0 access continues through the existing retrieval policy, subject
  to any configured L0 and total token caps.
- With all tier caps unset, the legacy shared-budget order and behavior remain
  unchanged.

The caps intentionally have no numeric defaults beyond `None`. There is not
yet a representative MemoPad relevance dataset from which to derive sensible
allocations; imposing percentages from unrelated benchmarks would misstate
their evidentiary basis.

## Validation

The focused Stage 3 suite covers all four tier boundaries (under, exact, and
over), empty input, higher-tier-first ordering, the hard total ceiling,
zero-cap omission, an uncapped tier, linked observation/relation accounting,
strongest-endpoint relation assignment, direct L0 access under its cap,
build-context wiring, confidence exclusion, and drill-down provenance. The
combined end-to-end regression also verifies these policies together and
confirms budget clipping does not break entity identity or L3 → L1 → L0 source
resolution. **48 focused retrieval, provenance, and level-ranking tests passed**;
Ruff, formatting, Pyright, and `uv lock --check` passed.

The broader context/provenance selection produced **85 passed, 5 failed, and
4 errors**. The five failures reproduce the existing SQLite `IN ?` syntax error
in related-result degree queries; the four hub-scoring errors are blocked by a
missing `context_service` test fixture. Neither failure category is caused by
the Stage 3 budget path, and both remain separate repository test blockers.

## Remaining Stage 3 work

- Run the full focused retrieval/provenance/configuration tests and the
  repository's SQLite suite after the unrelated SQL-query and test-fixture
  blockers are addressed.
- Build/evaluate on representative retrieval examples before recommending
  tier-cap defaults; measure actual tokenizer behavior alongside the
  deterministic approximate estimator.
- Derive cap recommendations from representative retrieval data; no numeric
  defaults are justified until that evaluation exists.
- Address the known SQLite related-results query failures separately from
  Stage 3 budget behavior.
