"""Service for building rich context from the knowledge graph."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import math


from loguru import logger
from sqlalchemy import text

from memopad.repository.entity_repository import EntityRepository
from memopad.repository.observation_repository import ObservationRepository
from memopad.repository.postgres_search_repository import PostgresSearchRepository
from memopad.repository.search_repository import SearchRepository, SearchIndexRow
from memopad.schemas.memory import MemoryUrl, memory_url_path
from memopad.schemas.search import SearchItemType
from memopad.services.skill_service import (
    CATEGORY_TRIGGER,
    SKILL_ENTITY_TYPE,
    group_skill_observations,
    is_validated_skill,
    match_trigger,
)
from memopad.services.local_decision_provider import (
    DecisionBatch,
    DecisionProvider,
    DecisionQuestion,
    DecisionProviderResponseError,
)
from memopad.services.shortterm_context import estimate_tokens
from memopad.utils import generate_permalink
from memopad.config import MemoPadConfig


@dataclass
class ContextResultRow:
    type: str
    id: int
    title: str
    permalink: str
    file_path: str
    depth: int
    root_id: int
    created_at: datetime
    from_id: Optional[int] = None
    to_id: Optional[int] = None
    relation_type: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None
    entity_id: Optional[int] = None
    # Conflict detection fields (MemGraphRAG-inspired)
    conflict_score: Optional[float] = None
    conflicting_obs_id: Optional[int] = None
    conflict_resolved: bool = False
    provenance_path: Optional[str] = None
    relevance_score: float = 0.0


@dataclass
class ContextResultItem:
    """A hierarchical result containing a primary item with its observations and related items."""

    primary_result: ContextResultRow | SearchIndexRow
    observations: List[ContextResultRow] = field(default_factory=list)
    related_results: List[ContextResultRow] = field(default_factory=list)


@dataclass
class ContextMetadata:
    """Metadata about a context result."""

    uri: Optional[str] = None
    types: Optional[List[SearchItemType]] = None
    depth: int = 1
    timeframe: Optional[str] = None
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    primary_count: int = 0
    related_count: int = 0
    total_observations: int = 0
    total_relations: int = 0


@dataclass
class ContextResult:
    """Complete context result with metadata."""

    results: List[ContextResultItem] = field(default_factory=list)
    metadata: ContextMetadata = field(default_factory=ContextMetadata)


class ContextService:
    """Service for building rich context from memory:// URIs.

    Handles three types of context building:
    1. Direct permalink lookup - exact match on path
    2. Pattern matching - using * wildcards
    3. Special modes via params (e.g., 'related')

    The optional hub-aware post-processing step adapts MemGraphRAG's retrieval idea:
    highly connected entities are down-weighted so specific, information-dense notes
    are less likely to be buried by generic hub nodes.
    """

    def __init__(
        self,
        search_repository: SearchRepository,
        entity_repository: EntityRepository,
        observation_repository: ObservationRepository,
        app_config: MemoPadConfig | None = None,
        decision_provider: DecisionProvider | None = None,
    ):
        self.search_repository = search_repository
        self.entity_repository = entity_repository
        self.observation_repository = observation_repository
        self.app_config = app_config
        self.decision_provider = decision_provider
        self.hub_penalty_enabled = app_config.hub_penalty_enabled if app_config else True
        self.hub_penalty_weight = app_config.hub_penalty_weight if app_config else 0.5
        self.hub_degree_threshold = app_config.hub_degree_threshold if app_config else 0

        # --- Retrieval budget (Tb G4: per-memory cap + graceful timeout) ---
        # 0 means disabled (current behavior). Opt-in via config so existing
        # deployments keep their retrieval semantics unchanged.
        self.recall_max_chars_per_memory = (
            app_config.recall_max_chars_per_memory if app_config else 0
        )
        self.recall_max_tokens = app_config.recall_max_tokens if app_config else 4096
        self.recall_tier_budgets = {
            "L0": app_config.recall_tier_budget_l0 if app_config else None,
            "L1": app_config.recall_tier_budget_l1 if app_config else None,
            "L2": app_config.recall_tier_budget_l2 if app_config else None,
            "L3": app_config.recall_tier_budget_l3 if app_config else None,
        }
        self.recall_min_confidence = app_config.recall_min_confidence if app_config else 0.50
        self.recall_timeout_s = (
            (app_config.recall_timeout_ms / 1000.0)
            if app_config and app_config.recall_timeout_ms > 0
            else 0.0
        )

        # --- Skill ranking boost (Tb G1) ---
        # When enabled, a validated skill is re-ranked as if it were a
        # high-confidence L2 scenario — moved ahead of non-skill primary results
        # so reusable expertise surfaces first. Off by default; zero effect on the
        # existing retrieval order when disabled.
        self.skills_enabled = bool(app_config and app_config.skills_enabled)

        # --- Level-weighted ranking (Tb L0-L3 distillation) ---
        # When `levels_enabled` is on, each primary entity's search score is
        # multiplied by its configured level weight (L3=1.0 > L2=0.85 > L1=0.70 >
        # L0=0.40) and the results re-sorted, so distilled higher-level memories
        # surface above raw L0 notes. A uniform weight across an all-L0 repo
        # preserves the search-engine order, so enabling is safe even when no
        # derived tiers exist yet — reordering only happens when levels differ.
        self.levels_enabled = bool(app_config and app_config.levels_enabled)
        self.level_weights = {
            "L3": app_config.level_weight_l3 if app_config else 1.0,
            "L2": app_config.level_weight_l2 if app_config else 0.85,
            "L1": app_config.level_weight_l1 if app_config else 0.70,
            "L0": app_config.level_weight_l0 if app_config else 0.40,
        }

    def _truncate_memory(self, text: Optional[str]) -> Optional[str]:
        """Cap a single memory item's content at the configured per-memory limit.

        Tb G4: prevents one large memory from exhausting the token budget.
        No-op when `recall_max_chars_per_memory` is 0 (disabled). The truncation
        marker makes it visible to the caller that the item was trimmed.
        """
        limit = self.recall_max_chars_per_memory
        if not text or limit <= 0 or len(text) <= limit:
            return text
        return text[:limit].rstrip() + " …[truncated]"

    async def _with_recall_timeout(self, coro, stage: str):
        """Run a retrieval stage with a hard timeout, degrading gracefully.

        Tb G4: on timeout, skip that stage's injection without failing the
        conversation — return whatever was gathered so far (None here; the caller
        treats None as "no results from this stage"). No-op when the timeout is 0.
        """
        if self.recall_timeout_s <= 0:
            return await coro
        try:
            return await asyncio.wait_for(coro, timeout=self.recall_timeout_s)
        except asyncio.TimeoutError:
            logger.warning(
                f"Recall stage '{stage}' timed out after {self.recall_timeout_s:.3f}s "
                f"— degrading gracefully (skipping this stage)"
            )
            return None

    async def _apply_skill_boost(
        self, context_results: List[ContextResultItem]
    ) -> List[ContextResultItem]:
        """Re-rank primary results so validated skills come first (Tb G1).

        Trigger: `skills_enabled` is on and build_context produced results.
        Why: a validated skill is reusable, high-confidence expertise — the
            analogue of a high-confidence L2 scenario — so it should surface
            ahead of ordinary notes when both match a context lookup.
        Outcome: a stable partition — validated-skill primary results move to
            the front, preserving the search-engine order within both the boosted
            and the unboosted groups (no re-shuffle beyond the cut).

        This is a post-fetch re-rank only; it never drops or adds results. The
        metadata read is a single batched `get_by_ids`. If that read fails, we
        degrade to the unboosted order rather than failing the whole context
        build — explicit degradation (like the G4 timeout), not hidden fallback:
        the failure is logged.
        """
        if not self.skills_enabled:
            return context_results
        entity_ids = [
            item.primary_result.id
            for item in context_results
            if item.primary_result.type == SearchItemType.ENTITY.value
        ]
        if not entity_ids:
            return context_results

        try:
            entities = await self.entity_repository.get_by_ids(entity_ids)
        except Exception as e:  # pragma: no cover - explicit degradation, not fallback
            logger.warning(
                f"Skill boost skipped — entity metadata read failed: {e}. "
                f"Returning unboosted order."
            )
            return context_results

        validated_ids = {e.id for e in entities if is_validated_skill(e)}
        if not validated_ids:
            return context_results

        boosted: List[ContextResultItem] = []
        rest: List[ContextResultItem] = []
        for item in context_results:
            prim = item.primary_result
            if prim.type == SearchItemType.ENTITY.value and prim.id in validated_ids:
                boosted.append(item)
            else:
                rest.append(item)
        return boosted + rest

    async def _rerank_context_candidates(
        self,
        context_results: List[ContextResultItem],
        query: Optional[str],
    ) -> List[ContextResultItem]:
        """Rerank at most 20 candidates without crossing retrieval groups."""
        if self.decision_provider is None or not query or not query.strip() or not context_results:
            return context_results

        candidate_limit = 20
        candidate_count = min(len(context_results), candidate_limit)
        candidates = context_results[:candidate_count]
        if candidate_count < 2:
            return context_results

        group_keys = await self._context_rerank_group_keys(candidates)
        scores = await self._score_context_candidates(query, candidates)
        candidate_slots: dict[tuple[str, tuple[str, ...], bool], list[int]] = {}
        for index, group_key in enumerate(group_keys):
            candidate_slots.setdefault(group_key, []).append(index)

        reranked_candidates = list(candidates)
        for indices in candidate_slots.values():
            ordered = sorted(indices, key=lambda index: (-scores[index], index))
            for slot, candidate_index in zip(indices, ordered):
                reranked_candidates[slot] = candidates[candidate_index]

        reranked = reranked_candidates + context_results[candidate_count:]
        return reranked

    async def _context_rerank_group_keys(
        self, context_results: List[ContextResultItem]
    ) -> List[tuple[str, tuple[str, ...], bool]]:
        """Resolve result type, memory level, and validated-skill status."""
        referenced: list[set[int]] = []
        all_entity_ids: set[int] = set()
        for item in context_results:
            primary = item.primary_result
            if primary.type == SearchItemType.ENTITY.value:
                ids = {primary.id}
            elif primary.type == SearchItemType.OBSERVATION.value:
                ids = {primary.entity_id} if primary.entity_id is not None else set()
            elif primary.type == SearchItemType.RELATION.value:
                ids = {
                    entity_id
                    for entity_id in (primary.from_id, primary.to_id)
                    if entity_id is not None
                }
            else:
                ids = set()
            referenced.append(ids)
            all_entity_ids.update(ids)

        entities = (
            await self.entity_repository.get_by_ids(list(all_entity_ids)) if all_entity_ids else []
        )
        metadata_by_id = {entity.id: entity for entity in entities}
        group_keys = []
        for item, entity_ids in zip(context_results, referenced):
            levels = tuple(
                sorted(
                    {
                        str((metadata_by_id[entity_id].entity_metadata or {}).get("level") or "L0")
                        for entity_id in entity_ids
                        if entity_id in metadata_by_id
                    }
                )
            )
            is_validated = any(
                is_validated_skill(metadata_by_id[entity_id])
                for entity_id in entity_ids
                if entity_id in metadata_by_id
            )
            group_keys.append((item.primary_result.type, levels or ("L0",), is_validated))
        return group_keys

    async def _score_context_candidates(
        self,
        query: str,
        candidates: List[ContextResultItem],
    ) -> list[float]:
        """Score a small candidate batch; scores only reorder matching groups."""
        state_candidates = []
        questions: list[DecisionQuestion] = []
        for index, item in enumerate(candidates):
            primary = item.primary_result
            title = primary.title or ""
            if isinstance(primary, SearchIndexRow):
                snippet = primary.content_snippet or ""
            else:
                snippet = primary.content or ""
            question_id = f"candidate_{index}"
            state_candidates.append(
                {
                    "id": question_id,
                    "title": title[:200],
                    "content": snippet[:600],
                }
            )
            questions.append(
                DecisionQuestion(
                    id=question_id,
                    type="noul",
                    instructions=f"Score the relevance of {question_id} to the query.",
                    criteria={
                        "true": "The candidate provides useful information for this query.",
                        "false": "The candidate is not useful for this query.",
                    },
                )
            )

        batch = DecisionBatch(
            state={"query": query[:1000], "candidates": state_candidates},
            questions=questions,
        )
        provider = self.decision_provider
        if provider is None:
            raise RuntimeError("A local decision provider is required for context reranking")
        response = await provider.decide(batch)
        expected_ids = {question.id for question in questions}
        if set(response.answers) != expected_ids:
            raise DecisionProviderResponseError(
                "Provider answer IDs must exactly match the context reranking questions"
            )
        scores: list[float] = []
        for question in questions:
            answer = response.answers.get(question.id)
            if (
                answer is None
                or answer.question_id != question.id
                or answer.type != "noul"
                or isinstance(answer.value, bool)
                or not isinstance(answer.value, (int, float))
                or not math.isfinite(answer.value)
                or not 0 <= answer.value <= 1
            ):
                raise DecisionProviderResponseError(
                    f"Provider returned an invalid relevance score for {question.id!r}"
                )
            scores.append(float(answer.value))
        return scores

    async def _apply_level_weights(
        self, context_results: List[ContextResultItem]
    ) -> List[ContextResultItem]:
        """Re-rank primary results by memory level weight (Tb L0-L3 distillation).

        Trigger: `levels_enabled` is on and build_context produced results.
        Why: distilled memories (L1 facts, L2 scenarios, L3 persona) are
            higher-confidence abstractions than raw L0 notes, so when both match
            a context lookup the higher level should surface first. The level
            weight (L3=1.0 > L2=0.85 > L1=0.70 > L0=0.40) scales the search score.
        Outcome: each primary's score is multiplied by its level weight and the
            list is re-sorted by (weighted_score, -weight) ascending. The primary
            key is the weighted score (bm25 convention: more negative = better),
            so a much stronger raw match still wins — the weight scales relevance,
            it does not override it. The -weight tiebreaker is what makes level
            ranking effective here: build_context's primary search has no
            full-text component, so bm25 ties at 0 and the level weight itself
            breaks the tie (higher level → larger weight → sorts first).

        Entity primaries use their own level; observation primaries use their
        owning entity's level, and relation primaries use the strongest level
        among their endpoints. Unlevelled entities use the L0 weight.

        The level is read from authoritative `entity_metadata` via a single
        batched `get_by_ids` (the search index stores only `entity_type`, not the
        full metadata). On read failure we degrade to the unweighted order
        (logged) — explicit degradation, like the G4 timeout / G1 boost, not a
        hidden correctness fallback.
        """
        if not self.levels_enabled or not context_results:
            return context_results

        entity_ids: set[int] = set()
        for item in context_results:
            primary = item.primary_result
            if primary.type == SearchItemType.ENTITY.value:
                entity_ids.add(primary.id)
            elif primary.type == SearchItemType.OBSERVATION.value and primary.entity_id:
                entity_ids.add(primary.entity_id)
            elif primary.type == SearchItemType.RELATION.value:
                entity_ids.update(
                    entity_id
                    for entity_id in (primary.from_id, primary.to_id)
                    if entity_id is not None
                )
        if not entity_ids:
            return context_results  # no entity primaries → nothing to re-rank

        try:
            entities = await self.entity_repository.get_by_ids(list(entity_ids))
        except Exception as e:  # pragma: no cover - explicit degradation, not fallback
            logger.warning(
                f"Level-weight re-rank skipped — entity metadata read failed: {e}. "
                f"Returning unweighted order."
            )
            return context_results

        level_by_id = {}
        for entity in entities:
            level_value = (entity.entity_metadata or {}).get("level")
            level_by_id[entity.id] = level_value if isinstance(level_value, str) else None
        l0_weight = self.level_weights["L0"]

        def _weight_for(prim) -> float:
            if prim.type == SearchItemType.ENTITY.value:
                entity_ids_for_row = [prim.id]
            elif prim.type == SearchItemType.OBSERVATION.value:
                entity_ids_for_row = [prim.entity_id] if prim.entity_id else []
            elif prim.type == SearchItemType.RELATION.value:
                entity_ids_for_row = [
                    entity_id for entity_id in (prim.from_id, prim.to_id) if entity_id is not None
                ]
            else:
                entity_ids_for_row = []
            weights = []
            for entity_id in entity_ids_for_row:
                level = level_by_id.get(entity_id)
                weights.append(
                    self.level_weights[level]
                    if level is not None and level in self.level_weights
                    else l0_weight
                )
            return max(weights, default=l0_weight)

        def _sort_key(item: ContextResultItem):
            prim = item.primary_result
            weight = _weight_for(prim)
            raw = getattr(prim, "score", None)
            raw = raw if raw is not None else 0.0
            weighted = raw * weight
            # Persist the weighted score on the row so callers see the re-ranked
            # value (the primary_result is a per-request SearchIndexRow copy).
            setattr(prim, "score", weighted)
            # (weighted_score ASC, -weight ASC): bm25 best-first, ties broken
            # toward the higher level (larger weight).
            return (weighted, -weight)

        context_results.sort(key=_sort_key)
        return context_results

    async def _apply_distilled_retrieval_policy(
        self,
        context_results: List[ContextResultItem],
        *,
        directly_requested_primary_ids: set[int],
    ) -> List[ContextResultItem]:
        """Keep distilled, sufficiently confident results in ordinary retrieval.

        An exact URI explicitly requested by the caller may keep its raw L0
        primary. Other L0 memories are reachable through the dedicated drill-down
        flow, not mixed into ordinary retrieval alongside compact L1-L3 memories.
        """
        if not self.levels_enabled or not context_results:
            return context_results

        def referenced_entity_ids(row: ContextResultRow | SearchIndexRow) -> set[int]:
            if row.type == SearchItemType.ENTITY.value:
                return {row.id}
            if row.type == SearchItemType.OBSERVATION.value:
                entity_id = row.entity_id
                return {entity_id} if entity_id is not None else set()
            if row.type == SearchItemType.RELATION.value:
                return {
                    entity_id for entity_id in (row.from_id, row.to_id) if entity_id is not None
                }
            return set()

        entity_ids = set()
        for item in context_results:
            entity_ids.update(referenced_entity_ids(item.primary_result))
            for row in (*item.observations, *item.related_results):
                entity_ids.update(referenced_entity_ids(row))
        if not entity_ids:
            return []

        entities = await self.entity_repository.get_by_ids(list(entity_ids))
        eligible_ids: set[int] = set()
        level_by_id: dict[int, str | None] = {}
        for entity in entities:
            metadata = entity.entity_metadata or {}
            level_value = metadata.get("level")
            level = level_value if isinstance(level_value, str) else None
            level_by_id[entity.id] = level
            if self.skills_enabled and is_validated_skill(entity):
                eligible_ids.add(entity.id)
                continue
            if level not in {"L1", "L2", "L3"}:
                continue
            try:
                confidence = float(metadata.get("confidence") or 0.0)
            except (TypeError, ValueError):
                logger.warning(
                    f"Distilled entity {entity.id} has invalid confidence metadata; "
                    "excluding it from context."
                )
                continue
            if confidence >= self.recall_min_confidence:
                eligible_ids.add(entity.id)

        directly_requested_raw_ids = {
            entity_id
            for entity_id in directly_requested_primary_ids
            if level_by_id.get(entity_id) not in {"L1", "L2", "L3"}
        }
        allowed_ids = eligible_ids | directly_requested_raw_ids

        def is_allowed(row: ContextResultRow | SearchIndexRow, *, primary: bool) -> bool:
            entity_ids_for_row = referenced_entity_ids(row)
            if row.type == SearchItemType.ENTITY.value:
                if primary and row.id in directly_requested_raw_ids:
                    return True
                return row.id in eligible_ids
            if row.type == SearchItemType.OBSERVATION.value:
                return bool(entity_ids_for_row & allowed_ids)
            if row.type == SearchItemType.RELATION.value:
                return (
                    len(entity_ids_for_row) == 2
                    and entity_ids_for_row <= allowed_ids
                    and bool(entity_ids_for_row & eligible_ids)
                )
            return False

        filtered: List[ContextResultItem] = []
        for item in context_results:
            if not is_allowed(item.primary_result, primary=True):
                continue
            item.related_results = [
                row for row in item.related_results if is_allowed(row, primary=False)
            ]
            filtered.append(item)
        return filtered

    @staticmethod
    def _budget_row_text(
        row: ContextResultRow | SearchIndexRow, remaining_tokens: int
    ) -> tuple[int, bool]:
        """Spend a shared estimated-token budget on user-visible memory text."""
        if isinstance(row, SearchIndexRow):
            fields = (
                ("title", row.title),
                ("content_snippet", row.content_snippet),
            )
        else:
            fields = (
                ("title", row.title),
                ("content", row.content),
                ("category", row.category),
                ("relation_type", row.relation_type),
            )

        for field_index, (field_name, value) in enumerate(fields):
            if not isinstance(value, str) or not value:
                continue
            if remaining_tokens <= 0:
                if field_name == "title":
                    row.title = ""
                elif field_name == "content_snippet" and isinstance(row, SearchIndexRow):
                    row.content_snippet = ""
                elif field_name == "content" and isinstance(row, ContextResultRow):
                    row.content = ""
                elif field_name == "category" and isinstance(row, ContextResultRow):
                    row.category = ""
                elif field_name == "relation_type" and isinstance(row, ContextResultRow):
                    row.relation_type = ""
                return 0, True
            cost = estimate_tokens(value)
            if cost <= remaining_tokens:
                remaining_tokens -= cost
                continue

            marker = " …[truncated]"
            marker_cost = estimate_tokens(marker)
            if remaining_tokens > marker_cost:
                clipped = value[: (remaining_tokens - marker_cost) * 4].rstrip() + marker
            else:
                clipped = value[: remaining_tokens * 4 + 3]
            ContextService._set_row_text(row, field_name, clipped)
            for remaining_field, _ in fields[field_index + 1 :]:
                ContextService._set_row_text(row, remaining_field, "")
            return max(0, remaining_tokens - estimate_tokens(clipped)), True
        return remaining_tokens, remaining_tokens <= 0

    @staticmethod
    def _set_row_text(row: ContextResultRow | SearchIndexRow, field_name: str, value: str) -> None:
        if field_name == "title":
            row.title = value
        elif field_name == "content_snippet" and isinstance(row, SearchIndexRow):
            row.content_snippet = value
        elif field_name == "content" and isinstance(row, ContextResultRow):
            row.content = value
        elif field_name == "category" and isinstance(row, ContextResultRow):
            row.category = value
        elif field_name == "relation_type" and isinstance(row, ContextResultRow):
            row.relation_type = value

    def _apply_recall_token_budget(
        self, context_results: List[ContextResultItem]
    ) -> List[ContextResultItem]:
        """Cap combined primary, observation, and related text after ranking."""
        if self.recall_max_tokens <= 0:
            return context_results

        remaining = self.recall_max_tokens
        bounded: List[ContextResultItem] = []
        for item in context_results:
            if remaining <= 0:
                break

            remaining, exhausted = self._budget_row_text(item.primary_result, remaining)
            observations = []
            related = []
            if remaining > 0 and not exhausted:
                for row in item.observations:
                    remaining, exhausted = self._budget_row_text(row, remaining)
                    observations.append(row)
                    if exhausted:
                        break
            if remaining > 0 and not exhausted:
                for row in item.related_results:
                    remaining, exhausted = self._budget_row_text(row, remaining)
                    related.append(row)
                    if exhausted:
                        break
            bounded.append(
                ContextResultItem(
                    primary_result=item.primary_result,
                    observations=observations,
                    related_results=related,
                )
            )
            if exhausted:
                break
        return bounded

    async def _apply_configured_recall_budgets(
        self, context_results: List[ContextResultItem]
    ) -> List[ContextResultItem]:
        """Apply optional per-level caps and the existing total response ceiling.

        Per-tier values are caps, not reserved quotas: underused capacity is not
        redistributed, and no tier can exceed the shared total budget. Missing
        tier caps preserve the legacy shared-budget behavior for that level.
        """
        if not any(budget is not None for budget in self.recall_tier_budgets.values()):
            return self._apply_recall_token_budget(context_results)
        if not context_results:
            return context_results

        item_levels = await self._context_item_levels(context_results)
        level_rank = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}
        prioritized = sorted(
            enumerate(zip(item_levels, context_results)),
            key=lambda pair: (-level_rank[pair[1][0]], pair[0]),
        )
        item_levels = [level for _, (level, _) in prioritized]
        context_results = [item for _, (_, item) in prioritized]
        remaining_by_level = dict(self.recall_tier_budgets)
        total_remaining = self.recall_max_tokens if self.recall_max_tokens > 0 else None
        bounded: List[ContextResultItem] = []

        for item, level in zip(context_results, item_levels):
            if total_remaining == 0:
                break
            tier_remaining = remaining_by_level[level]
            if tier_remaining == 0:
                continue

            limits = [limit for limit in (total_remaining, tier_remaining) if limit is not None]
            if not limits:
                bounded.append(item)
                continue

            allowance = min(limits)
            remaining = allowance
            remaining, exhausted = self._budget_row_text(item.primary_result, remaining)
            observations: list[ContextResultRow] = []
            related: list[ContextResultRow] = []
            if remaining > 0 and not exhausted:
                for row in item.observations:
                    remaining, exhausted = self._budget_row_text(row, remaining)
                    observations.append(row)
                    if exhausted:
                        break
            if remaining > 0 and not exhausted:
                for row in item.related_results:
                    remaining, exhausted = self._budget_row_text(row, remaining)
                    related.append(row)
                    if exhausted:
                        break

            spent = allowance - remaining
            if total_remaining is not None:
                total_remaining = max(0, total_remaining - spent)
            if tier_remaining is not None:
                remaining_by_level[level] = max(0, tier_remaining - spent)

            bounded.append(
                ContextResultItem(
                    primary_result=item.primary_result,
                    observations=observations,
                    related_results=related,
                )
            )

        return bounded

    async def _context_item_levels(self, context_results: List[ContextResultItem]) -> list[str]:
        """Resolve each primary item's tier once; relations use their strongest endpoint."""
        referenced_ids: list[set[int]] = []
        all_entity_ids: set[int] = set()
        for item in context_results:
            primary = item.primary_result
            if primary.type == SearchItemType.ENTITY.value:
                entity_ids = {primary.id}
            elif primary.type == SearchItemType.OBSERVATION.value:
                entity_ids = {primary.entity_id} if primary.entity_id is not None else set()
            elif primary.type == SearchItemType.RELATION.value:
                entity_ids = {
                    entity_id
                    for entity_id in (primary.from_id, primary.to_id)
                    if entity_id is not None
                }
            else:
                entity_ids = set()
            referenced_ids.append(entity_ids)
            all_entity_ids.update(entity_ids)

        entities = (
            await self.entity_repository.get_by_ids(list(all_entity_ids)) if all_entity_ids else []
        )
        level_rank = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}
        level_by_id = {}
        for entity in entities:
            level = (entity.entity_metadata or {}).get("level")
            if self.skills_enabled and is_validated_skill(entity):
                level_by_id[entity.id] = "L2"
            else:
                level_by_id[entity.id] = (
                    level if isinstance(level, str) and level in level_rank else "L0"
                )

        levels = []
        for entity_ids in referenced_ids:
            referenced_levels = [
                level_by_id[entity_id] for entity_id in entity_ids if entity_id in level_by_id
            ]
            levels.append(
                max(referenced_levels, key=level_rank.__getitem__) if referenced_levels else "L0"
            )
        return levels

    async def _inject_trigger_skills(
        self,
        context_results: List[ContextResultItem],
        topic: Optional[str],
    ) -> List[ContextResultItem]:
        """Inject validated skills whose ``[trigger]`` matches the context topic.

        Tb G1 trigger-matching, complementary to ``_apply_skill_boost``: the
        boost re-ranks skills that *already* surfaced in primary search; this step
        pulls in a validated skill that did *not* surface but whose trigger is
        relevant to the topic. The skill is prepended so reusable expertise leads
        the injected context.

        Trigger: `skills_enabled` is on AND a non-empty `topic` is available
            (the raw path of a non-wildcard `memory_url`; wildcards and type-only
            lookups have no topic to match against).
        Why: search may miss a skill whose title/body doesn't share tokens with
            the query, even though its trigger clearly applies. Matching the
            topic against the explicit `[trigger]` observation recovers it.
        Outcome: matched skills not already present are prepended. Existing
            results are never dropped. Failures (entity/observation reads) are
            logged and degrade to the unmodified order — explicit, not silent.
        """
        if not self.skills_enabled or not topic:
            return context_results

        # IDs already represented as primary results — don't double-inject.
        present_ids = {
            item.primary_result.id
            for item in context_results
            if getattr(item.primary_result, "type", None) == SearchItemType.ENTITY.value
        }

        try:
            skill_entities = await self.entity_repository.list_by_entity_type(
                SKILL_ENTITY_TYPE, limit=500, offset=0
            )
        except Exception as e:  # pragma: no cover - explicit degradation, not fallback
            logger.warning(f"Skill trigger-match skipped — skill list failed: {e}")
            return context_results

        candidates = [
            e for e in skill_entities if is_validated_skill(e) and e.id not in present_ids
        ]
        if not candidates:
            return context_results

        try:
            obs_by_entity = await self.observation_repository.find_by_entities(
                [e.id for e in candidates]
            )
        except Exception as e:  # pragma: no cover - explicit degradation, not fallback
            logger.warning(f"Skill trigger-match skipped — observation read failed: {e}")
            return context_results

        injected: List[ContextResultItem] = []
        for skill in candidates:
            obs = obs_by_entity.get(skill.id, []) or []
            triggers = group_skill_observations(obs).get(CATEGORY_TRIGGER, [])
            if match_trigger(triggers, topic):
                # Build a lightweight primary row for the skill (no related/obs
                # fetched — the caller can drill_down if it wants the body).
                primary = ContextResultRow(
                    type=SearchItemType.ENTITY.value,
                    id=skill.id,
                    title=skill.title,
                    permalink=skill.permalink or "",
                    file_path=skill.file_path or "",
                    depth=0,
                    root_id=skill.id,
                    created_at=skill.created_at,
                )
                injected.append(
                    ContextResultItem(primary_result=primary, observations=[], related_results=[])
                )

        if not injected:
            return context_results
        logger.info(f"Skill trigger-match injected {len(injected)} skill(s) for topic '{topic}'")
        return injected + context_results

    async def build_context(
        self,
        memory_url: Optional[MemoryUrl] = None,
        types: Optional[List[SearchItemType]] = None,
        depth: int = 1,
        since: Optional[datetime] = None,
        limit=10,
        offset=0,
        max_related: int = 10,
        include_observations: bool = True,
        query: Optional[str] = None,
    ) -> ContextResult:
        """Build rich context; an explicit query may enable injected reranking."""
        logger.debug(
            f"Building context for URI: '{memory_url}' depth: '{depth}' since: '{since}' limit: '{limit}' offset: '{offset}'  max_related: '{max_related}'"
        )

        normalized_path: Optional[str] = None
        directly_requested_primary_ids: set[int] = set()
        # Tb G1: the raw (un-normalized) memory_url path is the context
        # topic used for skill trigger-matching. Only set for non-wildcard
        # lookups — a wildcard pattern isn't a topic. Type-only lookups have no
        # topic, so trigger-matching is skipped there (see _inject_trigger_skills).
        topic: Optional[str] = None
        if memory_url:
            path = memory_url_path(memory_url)
            # Check for wildcards before normalization
            has_wildcard = "*" in path

            if has_wildcard:
                # For wildcard patterns, normalize each segment separately to preserve the *
                parts = path.split("*")
                normalized_parts = [
                    generate_permalink(part, split_extension=False) if part else ""
                    for part in parts
                ]
                normalized_path = "*".join(normalized_parts)
                logger.debug(f"Pattern search for '{normalized_path}'")
                primary = await self._with_recall_timeout(
                    self.search_repository.search(
                        permalink_match=normalized_path, limit=limit, offset=offset
                    ),
                    "primary_search",
                )
            else:
                # For exact paths, normalize the whole thing
                normalized_path = generate_permalink(path, split_extension=False)
                topic = path  # raw topic for G1 trigger-matching (un-normalized)
                logger.debug(f"Direct lookup for '{normalized_path}'")
                primary = await self._with_recall_timeout(
                    self.search_repository.search(
                        permalink=normalized_path, limit=limit, offset=offset
                    ),
                    "primary_search",
                )
                directly_requested_primary_ids = {
                    item.id for item in (primary or []) if item.type == SearchItemType.ENTITY.value
                }
        else:
            logger.debug(f"Build context for '{types}'")
            primary = await self._with_recall_timeout(
                self.search_repository.search(
                    search_item_types=types, after_date=since, limit=limit, offset=offset
                ),
                "primary_search",
            )

        # Get type_id pairs for traversal

        type_id_pairs = [(r.type, r.id) for r in primary] if primary else []
        logger.debug(f"found primary type_id_pairs: {len(type_id_pairs)}")

        # Find related content
        related = await self._with_recall_timeout(
            self.find_related(type_id_pairs, max_depth=depth, since=since, max_results=max_related),
            "find_related",
        )
        related = related or []
        logger.debug(f"Found {len(related)} related results")

        # Collect entity IDs from primary and related results
        entity_ids = []
        for result in primary or []:
            if result.type == SearchItemType.ENTITY.value:
                entity_ids.append(result.id)

        for result in related:
            if result.type == SearchItemType.ENTITY.value:
                entity_ids.append(result.id)

        # Fetch observations for all entities if requested
        observations_by_entity = {}
        if include_observations and entity_ids:
            # Use our observation repository to get observations for all entities at once
            observations_by_entity = await self._with_recall_timeout(
                self.observation_repository.find_by_entities(entity_ids),
                "find_observations",
            )
            observations_by_entity = observations_by_entity or {}
            logger.debug(f"Found observations for {len(observations_by_entity)} entities")

        # Create metadata dataclass
        metadata = ContextMetadata(
            uri=normalized_path if memory_url else None,
            types=types,
            depth=depth,
            timeframe=since.isoformat() if since else None,
            primary_count=len(primary or []),
            related_count=len(related),
            total_observations=sum(len(obs) for obs in observations_by_entity.values()),
            total_relations=sum(1 for r in related if r.type == SearchItemType.RELATION),
        )

        # Build context results list directly with ContextResultItem objects
        context_results = []

        # For each primary result
        for primary_item in primary or []:
            # Find all related items with this primary item as root
            related_to_primary = [r for r in related if r.root_id == primary_item.id]

            # Get observations for this item if it's an entity
            item_observations = []
            if primary_item.type == SearchItemType.ENTITY.value and include_observations:
                # Convert Observation models to ContextResultRows
                for obs in observations_by_entity.get(primary_item.id, []):
                    # Tb G4: cap each memory item's content so a single large
                    # observation cannot exhaust the injected token budget.
                    truncated_content = self._truncate_memory(obs.content)
                    item_observations.append(
                        ContextResultRow(
                            type="observation",
                            id=obs.id,
                            title=f"{obs.category}: {(truncated_content or obs.content)[:50]}...",
                            permalink=generate_permalink(
                                f"{primary_item.permalink}/observations/{obs.category}/{obs.content}"
                            ),
                            file_path=primary_item.file_path,
                            content=truncated_content,
                            category=obs.category,
                            entity_id=primary_item.id,
                            depth=0,
                            root_id=primary_item.id,
                            created_at=primary_item.created_at,
                            # --- Conflict fields ---
                            conflict_score=obs.conflict_score,
                            conflicting_obs_id=obs.conflicting_obs_id,
                            conflict_resolved=obs.conflict_resolved,
                            provenance_path=obs.provenance_path,
                        )
                    )

            # Create ContextResultItem directly
            context_item = ContextResultItem(
                primary_result=primary_item,
                observations=item_observations,
                related_results=related_to_primary,
            )

            context_results.append(context_item)

        # Keep distilled memories in ordinary retrieval; raw L0 context is only
        # preserved when the caller addressed that exact entity URI.
        if context_results:
            context_results = await self._apply_distilled_retrieval_policy(
                context_results,
                directly_requested_primary_ids=directly_requested_primary_ids,
            )

        # Re-rank the retained entity results by confidence-weighted memory level.
        if context_results:
            context_results = await self._apply_level_weights(context_results)

        if query is not None:
            context_results = await self._rerank_context_candidates(context_results, query)

        # Tb G1: re-rank so validated skills surface first. The method
        # self-gates on `skills_enabled`, so this is a no-op when the flag is off.
        if context_results:
            context_results = await self._apply_skill_boost(context_results)

        # Tb G1: trigger-matching — inject validated skills whose [trigger]
        # matches the topic but which didn't surface in primary search. Self-gates
        # on `skills_enabled` + a non-empty `topic`; never drops existing results.
        context_results = await self._inject_trigger_skills(context_results, topic)

        context_results = await self._apply_configured_recall_budgets(context_results)

        # Tb G1: trigger-matching may prepend validated skills to the result list
        # after `metadata` was built (primary_count was captured as len(primary)
        # above, before injection). Reconcile it to the final list length so the
        # metadata stays consistent with `len(result.results)`. No-op when the
        # flag is off or there's no topic (injection is a no-op then).
        metadata.primary_count = len(context_results)
        metadata.related_count = sum(len(item.related_results) for item in context_results)
        metadata.total_observations = sum(len(item.observations) for item in context_results)
        metadata.total_relations = sum(
            row.type == SearchItemType.RELATION.value
            for item in context_results
            for row in item.related_results
        )

        # Return the structured ContextResult
        return ContextResult(results=context_results, metadata=metadata)

    async def find_related(
        self,
        type_id_pairs: List[Tuple[str, int]],
        max_depth: int = 1,
        since: Optional[datetime] = None,
        max_results: int = 10,
    ) -> List[ContextResultRow]:
        """Find items connected through relations.

        Uses recursive CTE to find:
        - Connected entities
        - Relations that connect them

        Note on depth:
        Each traversal step requires two depth levels - one to find the relation,
        and another to follow that relation to an entity. So a max_depth of 4 allows
        traversal through two entities (relation->entity->relation->entity), while reaching
        an entity three steps away requires max_depth=6 (relation->entity->relation->entity->relation->entity).
        """
        max_depth = max_depth * 2

        if not type_id_pairs:
            return []

        # Extract entity IDs from type_id_pairs for the optimized query
        entity_ids = [i for t, i in type_id_pairs if t == "entity"]

        if not entity_ids:
            logger.debug("No entity IDs found in type_id_pairs")
            return []

        logger.debug(
            f"Finding connected items for {len(entity_ids)} entities with depth {max_depth}"
        )

        # Build the VALUES clause for entity IDs
        entity_id_values = ", ".join([str(i) for i in entity_ids])

        # Parameters for bindings - include project_id for security filtering
        params = {
            "max_depth": max_depth,
            "max_results": max_results,
            "project_id": self.search_repository.project_id,
        }

        # Build date and timeframe filters conditionally based on since parameter
        if since:
            # SQLite accepts ISO strings, but Postgres/asyncpg requires datetime objects
            if isinstance(self.search_repository, PostgresSearchRepository):  # pragma: no cover
                # asyncpg expects timezone-NAIVE datetime in UTC for DateTime(timezone=True) columns
                # even though the column stores timezone-aware values
                since_utc = (
                    since.astimezone(timezone.utc) if since.tzinfo else since
                )  # pragma: no cover
                params["since_date"] = since_utc.replace(tzinfo=None)  # pyright: ignore  # pragma: no cover
            else:
                params["since_date"] = since.isoformat()  # pyright: ignore
            date_filter = "AND e.created_at >= :since_date"
            relation_date_filter = "AND e_from.created_at >= :since_date"
            timeframe_condition = "AND eg.relation_date >= :since_date"
        else:
            date_filter = ""
            relation_date_filter = ""
            timeframe_condition = ""

        # Add project filtering for security - ensure all entities and relations belong to the same project
        project_filter = "AND e.project_id = :project_id"
        relation_project_filter = "AND e_from.project_id = :project_id"

        # Use a CTE that operates directly on entity and relation tables
        # This avoids the overhead of the search_index virtual table
        # Note: Postgres and SQLite have different CTE limitations:
        # - Postgres: doesn't allow multiple UNION ALL branches referencing the CTE
        # - SQLite: doesn't support LATERAL joins
        # So we need different queries for each database backend

        # Detect database backend
        is_postgres = isinstance(self.search_repository, PostgresSearchRepository)

        if is_postgres:  # pragma: no cover
            query = self._build_postgres_query(
                entity_id_values,
                date_filter,
                project_filter,
                relation_date_filter,
                relation_project_filter,
                timeframe_condition,
            )
        else:
            # SQLite needs VALUES clause for exclusion (not needed for Postgres)
            values = ", ".join([f"('{t}', {i})" for t, i in type_id_pairs])
            query = self._build_sqlite_query(
                entity_id_values,
                date_filter,
                project_filter,
                relation_date_filter,
                relation_project_filter,
                timeframe_condition,
                values,
            )

        result = await self.search_repository.execute_query(query, params=params)
        rows = result.all()

        context_rows = [
            ContextResultRow(
                type=row.type,
                id=row.id,
                title=row.title,
                permalink=row.permalink,
                file_path=row.file_path,
                from_id=row.from_id,
                to_id=row.to_id,
                relation_type=row.relation_type,
                content=row.content,
                category=row.category,
                entity_id=row.entity_id,
                depth=row.depth,
                root_id=row.root_id,
                created_at=row.created_at,
            )
            for row in rows
        ]
        if context_rows:
            entity_ids = [row.id for row in context_rows if row.type == "entity"]
            if entity_ids:
                degrees = await self._fetch_entity_degrees(entity_ids)
                context_rows = self._apply_hub_penalty(context_rows, degrees)
        return context_rows

    async def _fetch_entity_degrees(self, entity_ids: list[int]) -> dict[int, int]:
        """Return total incoming plus outgoing relation counts for entities.

        Trigger: hub-aware scoring is requested after BFS context traversal.
        Why: relation degree is a lightweight proxy for hub/generic-node risk.
        Outcome: high-degree entities can be down-weighted during final ranking.
        """
        if not entity_ids:
            return {}

        query = text("""
            SELECT entity_id, COUNT(*) AS degree
            FROM (
                SELECT from_id AS entity_id
                FROM relation
                WHERE from_id IN :entity_ids
                  AND project_id = :project_id
                UNION ALL
                SELECT to_id AS entity_id
                FROM relation
                WHERE to_id IN :entity_ids
                  AND project_id = :project_id
            ) AS relation_degrees
            GROUP BY entity_id
        """)
        result = await self.search_repository.execute_query(
            query,
            params={
                "entity_ids": tuple(entity_ids),
                "project_id": self.search_repository.project_id,
            },
        )
        return {row.entity_id: row.degree for row in result.all()}

    def _apply_hub_penalty(
        self,
        rows: list[ContextResultRow],
        degrees: dict[int, int],
        depth_weight: float = 0.5,
    ) -> list[ContextResultRow]:
        """Re-rank context rows by depth and inverse relation degree.

        Trigger: context traversal returns related entity rows.
        Why: generic hub nodes with many relations often add less specific value than
             rare leaf nodes, so inverse-degree scoring reduces hub dominance.
        Outcome: rows are sorted by combined depth and hub-aware relevance score.
        """
        for row in rows:
            depth_penalty = 1.0 / (row.depth + 1)
            if not self.hub_penalty_enabled:
                row.relevance_score = depth_penalty**self.hub_penalty_weight
                continue

            degree = degrees.get(row.id, 0)
            if degree <= self.hub_degree_threshold:
                row.relevance_score = depth_penalty**self.hub_penalty_weight
                continue

            effective_degree = degree - self.hub_degree_threshold
            hub_penalty = 1.0 / math.sqrt(effective_degree + 1)
            row.relevance_score = (depth_penalty**self.hub_penalty_weight) * hub_penalty

        return sorted(rows, key=lambda row: row.relevance_score, reverse=True)

    def _build_postgres_query(  # pragma: no cover
        self,
        entity_id_values: str,
        date_filter: str,
        project_filter: str,
        relation_date_filter: str,
        relation_project_filter: str,
        timeframe_condition: str,
    ):
        """Build Postgres-specific CTE query using LATERAL joins."""
        return text(f"""
        WITH RECURSIVE entity_graph AS (
            -- Base case: seed entities
            SELECT
                e.id,
                'entity' as type,
                e.title,
                e.permalink,
                e.file_path,
                CAST(NULL AS INTEGER) as from_id,
                CAST(NULL AS INTEGER) as to_id,
                CAST(NULL AS TEXT) as relation_type,
                CAST(NULL AS TEXT) as content,
                CAST(NULL AS TEXT) as category,
                CAST(NULL AS INTEGER) as entity_id,
                0 as depth,
                e.id as root_id,
                e.created_at,
                e.created_at as relation_date
            FROM entity e
            WHERE e.id IN ({entity_id_values})
            {date_filter}
            {project_filter}

            UNION ALL

            -- Fetch BOTH relations AND connected entities in a single recursive step
            -- Postgres only allows ONE reference to the recursive CTE in the recursive term
            -- We use CROSS JOIN LATERAL to generate two rows (relation + entity) from each traversal
            SELECT
                CASE
                    WHEN step_type = 1 THEN r.id
                    ELSE e.id
                END as id,
                CASE
                    WHEN step_type = 1 THEN 'relation'
                    ELSE 'entity'
                END as type,
                CASE
                    WHEN step_type = 1 THEN r.relation_type || ': ' || r.to_name
                    ELSE e.title
                END as title,
                CASE
                    WHEN step_type = 1 THEN ''
                    ELSE COALESCE(e.permalink, '')
                END as permalink,
                CASE
                    WHEN step_type = 1 THEN e_from.file_path
                    ELSE e.file_path
                END as file_path,
                CASE
                    WHEN step_type = 1 THEN r.from_id
                    ELSE NULL
                END as from_id,
                CASE
                    WHEN step_type = 1 THEN r.to_id
                    ELSE NULL
                END as to_id,
                CASE
                    WHEN step_type = 1 THEN r.relation_type
                    ELSE NULL
                END as relation_type,
                CAST(NULL AS TEXT) as content,
                CAST(NULL AS TEXT) as category,
                CAST(NULL AS INTEGER) as entity_id,
                eg.depth + step_type as depth,
                eg.root_id,
                CASE
                    WHEN step_type = 1 THEN e_from.created_at
                    ELSE e.created_at
                END as created_at,
                CASE
                    WHEN step_type = 1 THEN e_from.created_at
                    ELSE eg.relation_date
                END as relation_date
            FROM entity_graph eg
            CROSS JOIN LATERAL (VALUES (1), (2)) AS steps(step_type)
            JOIN relation r ON (
                eg.type = 'entity' AND
                (r.from_id = eg.id OR r.to_id = eg.id)
            )
            JOIN entity e_from ON (
                r.from_id = e_from.id
                {relation_project_filter}
            )
            LEFT JOIN entity e ON (
                step_type = 2 AND
                e.id = CASE
                    WHEN r.from_id = eg.id THEN r.to_id
                    ELSE r.from_id
                END
                {date_filter}
                {project_filter}
            )
            WHERE eg.depth < :max_depth
            AND (step_type = 1 OR (step_type = 2 AND e.id IS NOT NULL AND e.id != eg.id))
            {timeframe_condition}
        )
        -- Materialize and filter
        SELECT DISTINCT
            type,
            id,
            title,
            permalink,
            file_path,
            from_id,
            to_id,
            relation_type,
            content,
            category,
            entity_id,
            MIN(depth) as depth,
            root_id,
            created_at
        FROM entity_graph
        WHERE depth > 0
        GROUP BY type, id, title, permalink, file_path, from_id, to_id,
                 relation_type, content, category, entity_id, root_id, created_at
        ORDER BY depth, type, id
        LIMIT :max_results
       """)

    def _build_sqlite_query(
        self,
        entity_id_values: str,
        date_filter: str,
        project_filter: str,
        relation_date_filter: str,
        relation_project_filter: str,
        timeframe_condition: str,
        values: str,
    ):
        """Build SQLite-specific CTE query using multiple UNION ALL branches."""
        return text(f"""
        WITH RECURSIVE entity_graph AS (
            -- Base case: seed entities
            SELECT
                e.id,
                'entity' as type,
                e.title,
                e.permalink,
                e.file_path,
                NULL as from_id,
                NULL as to_id,
                NULL as relation_type,
                NULL as content,
                NULL as category,
                NULL as entity_id,
                0 as depth,
                e.id as root_id,
                e.created_at,
                e.created_at as relation_date,
                0 as is_incoming
            FROM entity e
            WHERE e.id IN ({entity_id_values})
            {date_filter}
            {project_filter}

            UNION ALL

            -- Get relations from current entities
            SELECT
                r.id,
                'relation' as type,
                r.relation_type || ': ' || r.to_name as title,
                '' as permalink,
                e_from.file_path,
                r.from_id,
                r.to_id,
                r.relation_type,
                NULL as content,
                NULL as category,
                NULL as entity_id,
                eg.depth + 1,
                eg.root_id,
                e_from.created_at,
                e_from.created_at as relation_date,
                CASE WHEN r.from_id = eg.id THEN 0 ELSE 1 END as is_incoming
            FROM entity_graph eg
            JOIN relation r ON (
                eg.type = 'entity' AND
                (r.from_id = eg.id OR r.to_id = eg.id)
            )
            JOIN entity e_from ON (
                r.from_id = e_from.id
                {relation_date_filter}
                {relation_project_filter}
            )
            LEFT JOIN entity e_to ON (r.to_id = e_to.id)
            WHERE eg.depth < :max_depth
            AND (r.to_id IS NULL OR e_to.project_id = :project_id)

            UNION ALL

            -- Get entities connected by relations
            SELECT
                e.id,
                'entity' as type,
                e.title,
                CASE
                    WHEN e.permalink IS NULL THEN ''
                    ELSE e.permalink
                END as permalink,
                e.file_path,
                NULL as from_id,
                NULL as to_id,
                NULL as relation_type,
                NULL as content,
                NULL as category,
                NULL as entity_id,
                eg.depth + 1,
                eg.root_id,
                e.created_at,
                eg.relation_date,
                eg.is_incoming
            FROM entity_graph eg
            JOIN entity e ON (
                eg.type = 'relation' AND
                e.id = CASE
                    WHEN eg.is_incoming = 0 THEN eg.to_id
                    ELSE eg.from_id
                END
                {date_filter}
                {project_filter}
            )
            WHERE eg.depth < :max_depth
            {timeframe_condition}
        )
        SELECT DISTINCT
            type,
            id,
            title,
            permalink,
            file_path,
            from_id,
            to_id,
            relation_type,
            content,
            category,
            entity_id,
            MIN(depth) as depth,
            root_id,
            created_at
        FROM entity_graph
        WHERE depth > 0
        GROUP BY type, id, title, permalink, file_path, from_id, to_id,
                 relation_type, content, category, entity_id, root_id, created_at
        ORDER BY depth, type, id
        LIMIT :max_results
       """)
