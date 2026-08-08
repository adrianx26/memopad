"""Code-only L0->L1->L2->L3 distillation engine (Tb native distillation).

The Tb-borrowed scaffolding (G3 scheduler, G5 provenance, G1 skills) shipped under
flags but never connected to an engine: the scheduler emitted ``DistillationTrigger``
to a no-op callback, so the "L0-L3 memory" the docs describe didn't exist in running
code. This module is the engine that closes that loop — **natively, by default,
automatically, with no external tool / API / key**.

Why code-only works here
------------------------
MemoPad's L0 is already structured: ``- [category] content`` observations,
``- rel [[target]]`` relations, YAML frontmatter. Distillation is therefore
*graph reduction + scoring + clustering over structured observations*, not
"understand freeform prose" — deterministic code does this honestly. The tradeoff
is explicit: this engine extracts/distils **structured** observations and clusters
them; it does not synthesize novel prose. That is the deliberate ceiling of "no
external model".

Two layers (per the user's "skill + add-in hardness for future improving")
----------------------------------------------------------------------
- **Soft / improvable:** the ``distillation`` Skill asset (versioned: bump
  ``skill_version`` to retune). Carries the procedure as ``[trigger]/[step]/
  [validation]/[when]/[do]/[don't]`` observations AND a structured ``tunables``
  block in frontmatter. ``load_skill_tunables`` reads it; absent the skill
  (or with the skills feature off) it falls back to config defaults.
- **Hard / robust:** this Python service — fail-fast, idempotent, incremental.
  The ``FactExtractor`` Protocol is the "add-in" point: ``CodeExtractor`` ships
  as default; a future extractor (even an LLM one, behind a flag) implements the
  same Protocol with no backbone changes.

Pipeline
--------
- L1 pass: select L0 entities updated since the last pass watermark, extract
  atomic-fact candidates from distillable observations, dedup vs existing L1
  facts (embedding cosine when ``MEMOPAD_EMBEDDINGS_ENABLED``, else token-Jaccard)
  -> reconfirm (bump confidence) or create a new L1 fact entity carrying
  ``source_entities`` provenance + a ``derived_from`` relation. Idempotent.
- L2 pass: cluster L1 facts (shared tag | shared source | sim >= threshold) into
  connected components; each cluster (size >= 2) becomes an L2 scenario entity.
  Debounced via the scheduler's L2 min-interval.
- L3 pass: aggregate stable L1 facts (confidence >= ``l3_min_confidence``) by
  category into a single per-project L3 persona entity.

Every derived entity is written through ``EntityService`` (so G5 provenance is
enforced) and indexed into the search index (so ``build_context`` can surface it
and the level-weighted re-rank can promote it). Re-runs are idempotent: dedup
reconfirms instead of duplicating, and scenario/persona titles are stable
(membership-hash / fixed slug) so re-passes update in place.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, List, Optional, Protocol, Sequence, Tuple

from loguru import logger

from memopad.config import MemoPadConfig
from memopad.repository.entity_repository import EntityRepository
from memopad.repository.observation_repository import ObservationRepository
from memopad.repository.relation_repository import RelationRepository
from memopad.schemas import Entity as EntitySchema
from memopad.services.distillation_scheduler import (
    DistillationScheduler,
    DistillationTrigger,
    TRIGGER_L1_DISTILL,
    TRIGGER_L2_SCENARIO,
    TRIGGER_L3_PERSONA,
    TRIGGER_WARMUP,
)
from memopad.services.embedding_service import EmbeddingService, _cosine
from memopad.services.entity_service import EntityService
from memopad.services.provenance_service import (
    DERIVED_LEVELS,
    DERIVED_FROM_RELATION_TYPE,
    entity_level,
)
from memopad.services.search_service import SearchService
from memopad.services.skill_service import (
    SKILL_ENTITY_TYPE,
    SKILL_STATUS_KEY,
    SKILL_VERSION_KEY,
    STATUS_DRAFT,
)


# --- Constants -------------------------------------------------------------

FACT_ENTITY_TYPE: str = "fact"
SCENARIO_ENTITY_TYPE: str = "scenario"
PERSONA_ENTITY_TYPE: str = "persona"

L1_DIR: str = "levels/L1/facts"
L2_DIR: str = "levels/L2/scenarios"
L3_DIR: str = "levels/L3"
SKILL_DIR: str = "skills"

DISTILLATION_SKILL_TITLE: str = "distillation"
PERSONA_TITLE: str = "persona"  # fixed slug -> one persona per project, idempotent

# Category -> base confidence. Higher = more trustworthy as an atomic fact. These
# are the bootstrap defaults; the `distillation` skill's tunables block can override.
CATEGORY_BASE_CONFIDENCE = {
    "definition": 0.80,
    "rule": 0.85,
    "constraint": 0.85,
    "principle": 0.85,
    "fact": 0.75,
    "preference": 0.70,
    "summary": 0.60,
}
DEFAULT_BASE_CONFIDENCE: float = 0.60
# Reconfirmation floors the confidence here (a re-seen fact is trusted at least this much).
RECONFIRM_CONFIDENCE: float = 0.90
# Bonus per relation degree on the source L0 entity, capped (a well-connected source
# is a stronger signal, but never enough to override the category base materially).
DEGREE_BONUS_MAX: float = 0.15
DEGREE_BONUS_STEP: float = 0.03


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _slugify(text: str, max_len: int = 60) -> str:
    """Lowercase alnum kebab slug (used for entity titles -> file paths).

    Deliberately simple and content-oriented (unlike ``generate_permalink``, which
    is path-aware): a fact's title is derived from its text, so a stable slug of
    the text + a content hash makes identical facts re-resolve to the same file
    (idempotent) while distinct facts never collide.
    """
    out: list[str] = []
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    slug = "".join(out).strip("-")
    return slug[:max_len].rstrip("-")


def _title_from_text(text: str, source_permalink: str, category: str) -> str:
    """Stable, readable, collision-resistant title for an L1/L2 entity.

    Readable kebab slug from the first ~60 chars of the fact text, plus a 6-char
    content hash so two distinct facts can never share a file path (and identical
    facts always re-resolve to the same path -> idempotent update).
    """
    slug = _slugify(text[:60]) if text else ""
    if not slug:
        slug = _slugify(f"{source_permalink}-{category}")
    digest = hashlib.md5(text.encode("utf-8")).hexdigest()[:6] if text else "000000"
    return f"{slug}-{digest}"


def _tokenize(text: str) -> set[str]:
    """Lowercase alphanumeric token set (the Jaccard fallback's unit)."""
    out: set[str] = set()
    cur: list[str] = []
    for ch in text.lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.add("".join(cur))
            cur = []
    if cur:
        out.add("".join(cur))
    return out


def _jaccard(a: str, b: str) -> float:
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


# --- Candidates & the extractor Protocol ------------------------------------


@dataclass
class FactCandidate:
    """One atomic fact distilled from a single L0 observation."""

    title: str
    text: str
    category: str
    source_permalink: str
    source_entity_id: int
    tags: List[str] = field(default_factory=list)
    confidence: float = 0.0

    @property
    def source_memory_uri(self) -> str:
        return f"memory://entity/{self.source_permalink}"


class FactExtractor(Protocol):
    """The add-in seam: turn one L0 entity's observations into fact candidates.

    ``CodeExtractor`` is the default. A future extractor (LLM-backed, behind a
    flag) implements this interface with no backbone changes.
    """

    async def extract(
        self,
        entity,
        observations,
        *,
        categories: Sequence[str],
    ) -> List[FactCandidate]: ...


class CodeExtractor:
    """Default FactExtractor: deterministic selection + scoring over structured obs.

    Selects observations whose category is in ``categories`` (the distillable set)
    and emits one candidate per observation. Confidence = category base + a small
    relation-degree bonus on the source entity (capped), clamped to [0, 1].
    """

    async def extract(
        self,
        entity,
        observations,
        *,
        categories: Sequence[str],
    ) -> List[FactCandidate]:
        cats = set(categories)
        src_perm = entity.permalink
        degree = len(getattr(entity, "outgoing_relations", None) or []) + len(
            getattr(entity, "incoming_relations", None) or []
        )
        bonus = min(DEGREE_BONUS_MAX, max(0, degree) * DEGREE_BONUS_STEP)
        out: List[FactCandidate] = []
        for obs in observations or []:
            if obs.category not in cats:
                continue
            text = (obs.content or "").strip()
            if not text:
                continue
            base = CATEGORY_BASE_CONFIDENCE.get(obs.category, DEFAULT_BASE_CONFIDENCE)
            out.append(
                FactCandidate(
                    title=_title_from_text(text, src_perm, obs.category),
                    text=text,
                    category=obs.category,
                    source_permalink=src_perm,
                    source_entity_id=entity.id,
                    tags=list(obs.tags or []),
                    confidence=min(1.0, base + bonus),
                )
            )
        return out


# --- Persisted watermarks --------------------------------------------------


class DistillationState:
    """Tiny JSON watermark store for incremental passes.

    Keys: ``last_l1_at`` / ``last_l2_at`` / ``last_persona_at`` (ISO strings).
    Persisted scheduler state is explicitly deferrable (idempotent re-pass on
    restart is safe), but the watermark makes the common case incremental.
    """

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:  # pragma: no cover - corrupt state -> cold start
            logger.warning(f"distillation state unreadable ({e}); cold-starting")
            return {}

    def save(self, data: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data), encoding="utf-8")
        except Exception as e:  # pragma: no cover - state is best-effort
            logger.warning(f"distillation state write failed ({e}); continuing")


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


# --- The hard backbone -----------------------------------------------------


class DistillationService:
    """The deterministic L0->L1->L2->L3 distillation backbone.

    Built per-project per-pass (stateless except for the on-disk watermark); the
    scheduler/dispatcher construct it via the standalone factory. All writes go
    through ``EntityService`` (G5 provenance) + ``SearchService`` (indexing).
    """

    def __init__(
        self,
        entity_repository: EntityRepository,
        observation_repository: ObservationRepository,
        relation_repository: RelationRepository,
        search_service: SearchService,
        entity_service: EntityService,
        app_config: MemoPadConfig,
        project_id: int,
        *,
        extractor: Optional[FactExtractor] = None,
        scheduler: Optional[DistillationScheduler] = None,
        embedding_service: Optional[EmbeddingService] = None,
        state_path: Optional[Path] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        self.entity_repository = entity_repository
        self.observation_repository = observation_repository
        self.relation_repository = relation_repository
        self.search_service = search_service
        self.entity_service = entity_service
        self.app_config = app_config
        self.project_id = project_id
        self.extractor = extractor or CodeExtractor()
        self.scheduler = scheduler
        self.embedding_service = embedding_service
        self._clock = clock or _now_utc
        state_path = state_path or (
            Path(app_config.data_dir_path) / "distillation" / f"project-{project_id}-state.json"
        )
        self._state = DistillationState(state_path)
        self._embed_cache: dict[str, Optional[list[float]]] = {}

    # --- trigger dispatch ---

    async def handle_trigger(self, trigger: DistillationTrigger) -> None:
        """Dispatch a scheduler trigger to the matching pass."""
        if trigger.trigger_type == TRIGGER_L1_DISTILL:
            await self.run_l1_pass(trigger.max_memories)
            # New facts may form clusters — attempt L2 while we're here (debounced).
            await self.run_l2_pass()
        elif trigger.trigger_type == TRIGGER_L2_SCENARIO:
            await self.run_l2_pass()
        elif trigger.trigger_type == TRIGGER_L3_PERSONA:
            await self.run_l3_pass()
        elif trigger.trigger_type == TRIGGER_WARMUP:
            logger.info(
                f"distillation warmup depth={trigger.depth} for project {self.project_id} "
                f"(retrieval-only, no-op for the MVP code-only engine)"
            )
        else:
            logger.warning(f"unknown distillation trigger type: {trigger.trigger_type}")

    # --- tunables + skill asset ---

    async def load_skill_tunables(self) -> dict:
        """Config defaults, optionally overridden by the `distillation` skill."""
        base = {
            "distillable_categories": list(self.app_config.distillable_categories),
            "dedup_similarity_threshold": self.app_config.dedup_similarity_threshold,
            "l2_similarity_threshold": self.app_config.l2_similarity_threshold,
            "l3_min_confidence": self.app_config.l3_min_confidence,
        }
        if not (self.app_config and self.app_config.skills_enabled):
            return base
        try:
            skills = await self.entity_repository.list_by_entity_type(
                SKILL_ENTITY_TYPE, limit=200
            )
            skill = next(
                (s for s in skills if s.title == DISTILLATION_SKILL_TITLE), None
            )
            if skill is None:
                return base
            tunables = (skill.entity_metadata or {}).get("tunables") or {}
            if not isinstance(tunables, dict):
                return base
            for key, value in tunables.items():
                if key in base and value is not None:
                    base[key] = value
            return base
        except Exception as e:  # pragma: no cover - degrade to config defaults
            logger.warning(f"load_skill_tunables failed ({e}); using config defaults")
            return base

    async def ensure_distillation_skill(self) -> None:
        """Create the `distillation` skill asset on first run (when skills are on).

        The skill is the procedure-of-record + a tunables block, versioned and
        editable. Created as ``draft`` so it documents the procedure without being
        injected into every context (validate it via the G1 tool to surface it).
        """
        if not (self.app_config and self.app_config.skills_enabled):
            return  # skills feature off -> distillation uses config defaults
        skills = await self.entity_repository.list_by_entity_type(
            SKILL_ENTITY_TYPE, limit=200
        )
        if any(s.title == DISTILLATION_SKILL_TITLE for s in skills):
            return  # already present
        schema = EntitySchema(
            title=DISTILLATION_SKILL_TITLE,
            directory=SKILL_DIR,
            entity_type=SKILL_ENTITY_TYPE,
            entity_metadata={
                SKILL_VERSION_KEY: 1,
                SKILL_STATUS_KEY: STATUS_DRAFT,
                "tunables": {
                    "distillable_categories": list(self.app_config.distillable_categories),
                    "dedup_similarity_threshold": self.app_config.dedup_similarity_threshold,
                    "l2_similarity_threshold": self.app_config.l2_similarity_threshold,
                    "l3_min_confidence": self.app_config.l3_min_confidence,
                },
            },
            content=DISTILLATION_SKILL_TEMPLATE,
        )
        entity, _ = await self.entity_service.create_or_update_entity(schema)
        await self.search_service.index_entity(entity)
        logger.info(f"created `distillation` skill asset (entity {entity.id})")

    # --- L1 pass ---

    async def run_l1_pass(self, max_memories: int) -> int:
        """Distil new L0 observations into L1 atomic facts. Returns facts written."""
        await self.ensure_distillation_skill()
        self._embed_cache.clear()
        tunables = await self.load_skill_tunables()
        categories = set(tunables["distillable_categories"])
        dedup_threshold = float(tunables["dedup_similarity_threshold"])

        state = self._state.load()
        since = _parse_dt(state.get("last_l1_at"))
        l0_entities = await self.entity_repository.find_updated_since(
            since, limit=max_memories
        )

        existing = await self._load_facts_with_text()
        created = 0
        reconfirmed = 0
        now = self._clock()

        for entity in l0_entities:
            if entity_level(entity.entity_metadata or {}) in DERIVED_LEVELS:
                continue  # already a derived memory — skip L1/L2/L3 sources
            candidates = await self.extractor.extract(
                entity, entity.observations, categories=categories
            )
            for cand in candidates:
                similar = self._find_similar(cand.text, existing, dedup_threshold)
                if similar is not None:
                    sim_entity, sim_ftext = similar
                    await self._reconfirm(sim_entity, sim_ftext, cand, now)
                    reconfirmed += 1
                else:
                    fact = await self._create_l1_fact(cand, now)
                    existing.append((fact, cand.text))
                    created += 1

        state["last_l1_at"] = now.isoformat()
        self._state.save(state)
        logger.info(
            f"L1 pass: scanned {len(l0_entities)} L0 entities -> {created} new facts, "
            f"{reconfirmed} reconfirmed (project {self.project_id})"
        )
        return created

    async def _load_facts_with_text(self) -> List[Tuple[object, str]]:
        """Existing L1 facts as (entity, fact_text) for dedup/clustering."""
        facts = await self.entity_repository.list_by_entity_type(
            FACT_ENTITY_TYPE, limit=100000
        )
        out: List[Tuple[object, str]] = []
        for f in facts:
            fact_obs = next(
                (o for o in (f.observations or []) if o.category == "fact"), None
            )
            text = (fact_obs.content if fact_obs else f.title) or ""
            out.append((f, text))
        return out

    def _find_similar(
        self,
        text: str,
        existing: List[Tuple[object, str]],
        threshold: float,
    ) -> Optional[Tuple[object, str]]:
        for entity, ftext in existing:
            if self._similarity(text, ftext) >= threshold:
                return entity, ftext
        return None

    async def _create_l1_fact(self, cand: FactCandidate, now: datetime) -> object:
        metadata = {
            "level": "L1",
            "confidence": round(cand.confidence, 4),
            "source_entities": [cand.source_memory_uri],
            "last_confirmed": now.isoformat(),
            "importance_score": round(cand.confidence, 4),
            "decay_factor": 1.0,
            "category": cand.category,
            "tags": list(cand.tags),
        }
        content = (
            f"# {cand.title}\n\n"
            f"## Observations\n- [fact] {cand.text}\n\n"
            f"## Relations\n- {DERIVED_FROM_RELATION_TYPE} [[{cand.source_permalink}]]\n"
        )
        schema = EntitySchema(
            title=cand.title,
            directory=L1_DIR,
            entity_type=FACT_ENTITY_TYPE,
            entity_metadata=metadata,
            content=content,
        )
        entity, _ = await self.entity_service.create_or_update_entity(schema)
        await self.search_service.index_entity(entity)
        return entity

    async def _reconfirm(self, entity, ftext: str, cand: FactCandidate, now: datetime) -> None:
        """Bump an existing similar fact: confidence floor + provenance union.

        Routed through ``entity_service.create_or_update_entity`` (not a raw repo
        update) so the markdown file and DB row stay consistent — MemoPad treats
        the file as source of truth, and a raw repo update would leave the file
        stale (a re-sync/reindex would then silently lose the bump). The existing
        entity's title is reused so the write resolves to the same file (idempotent
        update in place, never a duplicate).
        """
        md = entity.entity_metadata or {}
        sources = self._as_list(md.get("source_entities"))
        if cand.source_memory_uri not in sources:
            sources.append(cand.source_memory_uri)
        source_permalinks = [
            uri[len("memory://entity/") :] if uri.startswith("memory://entity/") else uri
            for uri in sources
        ]
        rel_lines = [f"- {DERIVED_FROM_RELATION_TYPE} [[{p}]]" for p in source_permalinks]
        content = (
            f"# {entity.title}\n\n"
            f"## Observations\n- [fact] {ftext}\n\n"
            f"## Relations\n" + "\n".join(rel_lines) + "\n"
        )
        metadata = {
            "level": "L1",
            "confidence": max(float(md.get("confidence") or 0.0), RECONFIRM_CONFIDENCE),
            "source_entities": sources,
            "last_confirmed": now.isoformat(),
            "decay_factor": 1.0,
        }
        schema = EntitySchema(
            title=entity.title,
            directory=L1_DIR,
            entity_type=FACT_ENTITY_TYPE,
            entity_metadata=metadata,
            content=content,
        )
        updated, _ = await self.entity_service.create_or_update_entity(schema)
        await self.search_service.index_entity(updated)

    # --- L2 pass ---

    async def run_l2_pass(self) -> int:
        """Cluster L1 facts into L2 scenario entities. Returns scenarios written."""
        if self.scheduler is not None and not self.scheduler.should_trigger_l2(
            self.project_id, now=self._clock()
        ):
            return 0  # debounced by the scheduler's L2 min-interval
        self._embed_cache.clear()
        tunables = await self.load_skill_tunables()
        threshold = float(tunables["l2_similarity_threshold"])
        facts = await self._load_facts_with_text()
        if len(facts) < 2:
            return 0

        # Union-find over the similarity graph.
        n = len(facts)
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i in range(n):
            for j in range(i + 1, n):
                if self._facts_connected(facts[i], facts[j], threshold):
                    union(i, j)

        clusters: dict[int, List[int]] = defaultdict(list)
        for i in range(n):
            clusters[find(i)].append(i)

        now = self._clock()
        created = 0
        for members in clusters.values():
            if len(members) < 2:
                continue
            await self._create_l2_scenario([facts[i] for i in members], now)
            created += 1

        if self.scheduler is not None:
            self.scheduler.mark_l2_fired(self.project_id, now=now)
        state = self._state.load()
        state["last_l2_at"] = now.isoformat()
        self._state.save(state)
        logger.info(
            f"L2 pass: {n} facts -> {created} scenarios (project {self.project_id})"
        )
        return created

    def _facts_connected(
        self,
        a: Tuple[object, str],
        b: Tuple[object, str],
        threshold: float,
    ) -> bool:
        ea, ta = a
        eb, tb = b
        ma = ea.entity_metadata or {}
        mb = eb.entity_metadata or {}
        # shared tag
        ta_tags = set((ma.get("tags") or []))
        tb_tags = set((mb.get("tags") or []))
        if ta_tags & tb_tags:
            return True
        # shared source entity
        sa = set(self._as_list(ma.get("source_entities")))
        sb = set(self._as_list(mb.get("source_entities")))
        if sa & sb:
            return True
        # semantic / token similarity
        return self._similarity(ta, tb) >= threshold

    @staticmethod
    def _as_list(value) -> List[str]:
        if not value:
            return []
        if isinstance(value, str):
            return [value]
        return [str(v) for v in value]

    async def _create_l2_scenario(
        self, members: List[Tuple[object, str]], now: datetime
    ) -> None:
        entities = [e for e, _ in members]
        # dominant tag for a readable title; fall back to "scenario".
        tag_counts: dict[str, int] = defaultdict(int)
        for e in entities:
            for t in (e.entity_metadata or {}).get("tags") or []:
                tag_counts[t] += 1
        dominant = max(tag_counts, key=tag_counts.get) if tag_counts else "scenario"
        perm_hashes = sorted(e.permalink for e in entities)
        digest = hashlib.md5("|".join(perm_hashes).encode("utf-8")).hexdigest()[:6]
        title = f"{dominant}-{digest}"
        sources = [f"memory://entity/{e.permalink}" for e in entities]
        body_lines = ["## Observations"]
        for e, text in members:
            body_lines.append(f"- [scenario] {text}")
        body_lines.append("## Relations")
        for e in entities:
            body_lines.append(f"- {DERIVED_FROM_RELATION_TYPE} [[{e.permalink}]]")
        content = f"# {title}\n\n" + "\n".join(body_lines) + "\n"
        metadata = {
            "level": "L2",
            "confidence": 0.80,
            "source_entities": sources,
            "last_confirmed": now.isoformat(),
            "importance_score": 0.80,
            "decay_factor": 1.0,
        }
        schema = EntitySchema(
            title=title,
            directory=L2_DIR,
            entity_type=SCENARIO_ENTITY_TYPE,
            entity_metadata=metadata,
            content=content,
        )
        entity, _ = await self.entity_service.create_or_update_entity(schema)
        await self.search_service.index_entity(entity)

    # --- L3 pass ---

    async def run_l3_pass(self) -> int:
        """Aggregate stable L1 facts into one per-project L3 persona. Returns 0/1."""
        tunables = await self.load_skill_tunables()
        min_conf = float(tunables["l3_min_confidence"])
        facts = await self.entity_repository.list_by_entity_type(
            FACT_ENTITY_TYPE, limit=100000
        )
        stable = [
            f
            for f in facts
            if float((f.entity_metadata or {}).get("confidence") or 0.0) >= min_conf
        ]
        if not stable:
            return 0

        # Cap + order by confidence desc so the persona leads with the strongest facts.
        stable.sort(
            key=lambda f: float((f.entity_metadata or {}).get("confidence") or 0.0),
            reverse=True,
        )
        stable = stable[:100]

        now = self._clock()
        sources: List[str] = []
        obs_lines: List[str] = ["## Observations"]
        rel_lines: List[str] = ["## Relations"]
        for f in stable:
            category = (f.entity_metadata or {}).get("category") or "fact"
            fact_obs = next(
                (o for o in (f.observations or []) if o.category == "fact"), None
            )
            text = (fact_obs.content if fact_obs else f.title) or ""
            obs_lines.append(f"- [{category}] {text}")
            rel_lines.append(f"- {DERIVED_FROM_RELATION_TYPE} [[{f.permalink}]]")
            sources.append(f"memory://entity/{f.permalink}")

        content = f"# {PERSONA_TITLE}\n\n" + "\n".join(obs_lines) + "\n\n" + "\n".join(rel_lines) + "\n"
        metadata = {
            "level": "L3",
            "confidence": 0.90,
            "source_entities": sources,
            "last_confirmed": now.isoformat(),
            "importance_score": 0.90,
            "decay_factor": 1.0,
        }
        schema = EntitySchema(
            title=PERSONA_TITLE,
            directory=L3_DIR,
            entity_type=PERSONA_ENTITY_TYPE,
            entity_metadata=metadata,
            content=content,
        )
        entity, _ = await self.entity_service.create_or_update_entity(schema)
        await self.search_service.index_entity(entity)

        state = self._state.load()
        state["last_persona_at"] = now.isoformat()
        self._state.save(state)
        logger.info(
            f"L3 pass: persona regenerated from {len(stable)} stable facts "
            f"(project {self.project_id})"
        )
        return 1

    # --- similarity (embedding cosine when available, else token-Jaccard) ---

    def _embed(self, text: str) -> Optional[list[float]]:
        if not text:
            return None
        if text in self._embed_cache:
            return self._embed_cache[text]
        vec: Optional[list[float]] = None
        if self.embedding_service is not None and self.embedding_service.provider is not None:
            try:
                vecs = self.embedding_service.provider.embed([text])
                vec = vecs[0] if vecs else None
            except Exception as e:  # pragma: no cover - degrade to jaccard
                logger.warning(f"embedding failed ({e}); falling back to token-Jaccard")
                vec = None
        self._embed_cache[text] = vec
        return vec

    def _similarity(self, a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        va, vb = self._embed(a), self._embed(b)
        if va is not None and vb is not None:
            return _cosine(va, vb)
        return _jaccard(a, b)


# --- The callback that plugs into the scheduler ----------------------------


class DistillationDispatcher:
    """``DistillationCallback`` impl: build a per-project service and dispatch.

    Fire-and-forget: failures are logged and swallowed so a distillation error
    can never fail the write that triggered it (the scheduler calls this from the
    create path's ``asyncio.create_task``).
    """

    def __init__(
        self,
        service_factory: Callable[[int], Awaitable[Optional[DistillationService]]],
    ):
        self._service_factory = service_factory

    async def __call__(self, trigger: DistillationTrigger) -> None:
        try:
            service = await self._service_factory(trigger.project_id)
            if service is None:
                return
            await service.handle_trigger(trigger)
        except Exception as e:  # pragma: no cover - never propagate to the caller
            logger.error(
                f"distillation pass failed for project {trigger.project_id} "
                f"({trigger.trigger_type}): {e}"
            )


# --- The distillation skill template (procedure-of-record) -----------------

DISTILLATION_SKILL_TEMPLATE = """# distillation

The code-only L0->L1->L2->L3 distillation procedure for this project. Edit this
skill (and bump `skill_version`) to retune distillation without touching code;
the `tunables` block in the frontmatter overrides the config defaults.

## Observations
- [trigger] a new memory is written, or the L1 idle timeout elapses, or the
  persona every-N cadence fires (the DistillationScheduler decides *when*).
- [step] L1 — select L0 entities updated since the last pass, extract atomic
  facts from distillable observation categories, dedup vs existing L1 facts
  (embedding cosine when available, else token-Jaccard) -> reconfirm or create.
- [step] L2 — cluster L1 facts by shared tag / shared source / similarity into
  scenarios (connected components of size >= 2).
- [step] L3 — aggregate stable L1 facts (confidence >= l3_min_confidence) by
  category into the persona.
- [validation] every derived entity carries non-empty `source_entities`
  (G5 provenance) so the chain is reversible to L0 via drill_down.
- [validation] re-runs are idempotent — dedup reconfirms (no duplicates);
  scenario/persona titles are stable so re-passes update in place.
- [when] runs automatically, by default, with no user intervention (levels_enabled
  + levels_pipeline_automatic are ON).
- [do] keep distillable_categories narrow — only structured, factual categories.
- [do] raise `dedup_similarity_threshold` to avoid near-duplicate facts.
- [don't] expect novel prose — this engine extracts and clusters, it does not
  synthesize (that is the deliberate ceiling of the no-external-model design).
- [don't] set `skill_status` to `validated` unless you want this skill surfaced
  in every context lookup via the G1 trigger/boost path.
"""