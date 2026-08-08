"""Tests for the code-only L0->L1->L2->L3 distillation backbone (Tb native distillation).

These exercise ``DistillationService`` end-to-end through the real ``EntityService``
+ SQLite repos (no mocks of the write path) so the G5 provenance invariant,
idempotent re-runs, and the level metadata all hold against the actual file+DB
dual store. Embeddings are off (the default) so dedup uses token-Jaccard —
deterministic and environment-independent. A custom ``FactExtractor`` test
exercises the Protocol seam; a skills-on test exercises the versioned skill asset
+ tunables loading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List

import pytest

from memopad.schemas import Entity as EntitySchema
from memopad.services.distillation_scheduler import (
    DistillationScheduler,
    DistillationTrigger,
    PipelineConfig,
    TRIGGER_L1_DISTILL,
    TRIGGER_L3_PERSONA,
    TRIGGER_WARMUP,
)
from memopad.services.distillation_service import (
    DISTILLATION_SKILL_TITLE,
    CodeExtractor,
    DistillationDispatcher,
    DistillationService,
    FactCandidate,
    FactExtractor,
    PERSONA_ENTITY_TYPE,
    SCENARIO_ENTITY_TYPE,
    FACT_ENTITY_TYPE,
)
from memopad.services.skill_service import SKILL_ENTITY_TYPE


def _service(
    entity_repository,
    observation_repository,
    relation_repository,
    search_service,
    entity_service,
    app_config,
    tmp_path,
    *,
    scheduler=None,
    extractor=None,
    embedding_service=None,
    state_path=None,
) -> DistillationService:
    return DistillationService(
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        search_service=search_service,
        entity_service=entity_service,
        app_config=app_config,
        project_id=entity_repository.project_id,
        scheduler=scheduler,
        extractor=extractor,
        embedding_service=embedding_service,
        state_path=state_path or (tmp_path / "distillation-state.json"),
    )


async def _note(
    entity_service,
    search_service,
    *,
    title: str,
    observations: List[tuple],
    directory: str = "notes",
):
    """Create an L0 note with the given (category, content) observations and index it."""
    lines = [f"# {title}", "", "## Observations"]
    for cat, content in observations:
        lines.append(f"- [{cat}] {content}")
    content = "\n".join(lines) + "\n"
    schema = EntitySchema(
        title=title, directory=directory, entity_type="note", content=content
    )
    entity = await entity_service.create_entity(schema)
    await search_service.index_entity(entity)
    return entity


def _level(entity) -> str:
    return (entity.entity_metadata or {}).get("level")


def _sources(entity) -> list:
    return (entity.entity_metadata or {}).get("source_entities") or []


# --- L1 pass ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_l1_creates_fact_from_distillable_observation(
    entity_repository, observation_repository, relation_repository,
    search_service, entity_service, app_config, tmp_path,
):
    """A `[definition]` observation distils into one L1 fact carrying provenance."""
    await _note(
        entity_service, search_service,
        title="Widgets",
        observations=[("definition", "A widget is a reusable UI component")],
    )
    svc = _service(
        entity_repository, observation_repository, relation_repository,
        search_service, entity_service, app_config, tmp_path,
    )
    created = await svc.run_l1_pass(max_memories=50)

    assert created == 1
    facts = await entity_repository.list_by_entity_type(FACT_ENTITY_TYPE, limit=100)
    assert len(facts) == 1
    fact = facts[0]
    assert _level(fact) == "L1"
    assert _sources(fact)  # G5 provenance: non-empty
    assert _sources(fact)[0].startswith("memory://entity/")
    # definition base confidence (0.80) + zero relation-degree bonus. Numeric
    # metadata is stringified by the frontmatter normalizer (issue #236), so cast.
    assert float((fact.entity_metadata or {}).get("confidence")) >= 0.80


@pytest.mark.asyncio
async def test_l1_skips_non_distillable_categories(
    entity_repository, observation_repository, relation_repository,
    search_service, entity_service, app_config, tmp_path,
):
    """A `[note]` (not in distillable_categories) produces no L1 fact."""
    await _note(
        entity_service, search_service,
        title="Diary",
        observations=[("note", "today I felt tired")],
    )
    svc = _service(
        entity_repository, observation_repository, relation_repository,
        search_service, entity_service, app_config, tmp_path,
    )
    created = await svc.run_l1_pass(max_memories=50)
    assert created == 0
    facts = await entity_repository.list_by_entity_type(FACT_ENTITY_TYPE, limit=100)
    assert facts == []


@pytest.mark.asyncio
async def test_l1_dedup_reconfirms_identical_fact(
    entity_repository, observation_repository, relation_repository,
    search_service, entity_service, app_config, tmp_path,
):
    """A second sighting of the same fact reconfirms (no duplicate), bumps confidence."""
    await _note(
        entity_service, search_service,
        title="Src A",
        observations=[("definition", "A widget is a reusable UI component")],
    )
    svc = _service(
        entity_repository, observation_repository, relation_repository,
        search_service, entity_service, app_config, tmp_path,
    )
    await svc.run_l1_pass(max_memories=50)
    facts = await entity_repository.list_by_entity_type(FACT_ENTITY_TYPE, limit=100)
    assert len(facts) == 1
    first_conf = (facts[0].entity_metadata or {}).get("confidence")
    first_sources = set(_sources(facts[0]))

    # Second source sighting the identical fact.
    src_b = await _note(
        entity_service, search_service,
        title="Src B",
        observations=[("definition", "A widget is a reusable UI component")],
    )
    await svc.run_l1_pass(max_memories=50)
    facts = await entity_repository.list_by_entity_type(FACT_ENTITY_TYPE, limit=100)
    assert len(facts) == 1  # reconfirmed, not duplicated
    assert float((facts[0].entity_metadata or {}).get("confidence")) >= 0.90  # reconfirm floor
    # The new source is unioned into provenance.
    assert f"memory://entity/{src_b.permalink}" in _sources(facts[0])
    assert set(_sources(facts[0])) > first_sources


@pytest.mark.asyncio
async def test_l1_idempotent_cold_restart_no_duplicates(
    entity_repository, observation_repository, relation_repository,
    search_service, entity_service, app_config, tmp_path,
):
    """Losing the watermark (cold restart) and re-passing creates no duplicates."""
    await _note(
        entity_service, search_service,
        title="Cold",
        observations=[("rule", "Never commit secrets to the repository")],
    )
    svc = _service(
        entity_repository, observation_repository, relation_repository,
        search_service, entity_service, app_config, tmp_path,
    )
    await svc.run_l1_pass(max_memories=50)
    assert len(await entity_repository.list_by_entity_type(FACT_ENTITY_TYPE, limit=100)) == 1

    # Simulate a cold restart: a brand-new service with NO state file (since=None).
    svc2 = _service(
        entity_repository, observation_repository, relation_repository,
        search_service, entity_service, app_config, tmp_path,
        state_path=tmp_path / "fresh-state.json",  # nonexistent -> since=None
    )
    created = await svc2.run_l1_pass(max_memories=50)
    # The L0 is re-processed but the candidate dedups against the existing fact.
    assert created == 0  # no new fact, only a reconfirm
    assert len(await entity_repository.list_by_entity_type(FACT_ENTITY_TYPE, limit=100)) == 1


# --- L2 pass ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_l2_clusters_facts_sharing_a_source(
    entity_repository, observation_repository, relation_repository,
    search_service, entity_service, app_config, tmp_path,
):
    """Two distinct facts from one L0 (shared source) form one L2 scenario."""
    await _note(
        entity_service, search_service,
        title="Dual",
        observations=[
            ("definition", "A widget renders the UI tree"),
            ("definition", "A gadget renders the logic tree"),
        ],
    )
    svc = _service(
        entity_repository, observation_repository, relation_repository,
        search_service, entity_service, app_config, tmp_path,
    )
    await svc.run_l1_pass(max_memories=50)
    # scheduler=None -> run_l2_pass is not debounced, runs immediately.
    scenarios = await svc.run_l2_pass()

    assert scenarios == 1
    scs = await entity_repository.list_by_entity_type(SCENARIO_ENTITY_TYPE, limit=100)
    assert len(scs) == 1
    assert _level(scs[0]) == "L2"
    assert len(_sources(scs[0])) == 2


@pytest.mark.asyncio
async def test_l2_no_cluster_for_unrelated_facts(
    entity_repository, observation_repository, relation_repository,
    search_service, entity_service, app_config, tmp_path,
):
    """Two unrelated facts (no shared tag/source/similarity) form no scenario."""
    await _note(
        entity_service, search_service,
        title="Alpha",
        observations=[("rule", "Always rotate credentials on schedule")],
    )
    await _note(
        entity_service, search_service,
        title="Beta",
        observations=[("definition", "A photon is a light quantum")],
    )
    svc = _service(
        entity_repository, observation_repository, relation_repository,
        search_service, entity_service, app_config, tmp_path,
    )
    await svc.run_l1_pass(max_memories=50)
    scenarios = await svc.run_l2_pass()
    assert scenarios == 0


# --- L3 pass ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_l3_aggregates_stable_facts_into_persona(
    entity_repository, observation_repository, relation_repository,
    search_service, entity_service, app_config, tmp_path,
):
    """Stable L1 facts (confidence >= l3_min_confidence) aggregate into one persona."""
    await _note(
        entity_service, search_service,
        title="Rules",
        observations=[("rule", "Never commit secrets to the repository")],
    )
    svc = _service(
        entity_repository, observation_repository, relation_repository,
        search_service, entity_service, app_config, tmp_path,
    )
    await svc.run_l1_pass(max_memories=50)
    # rule base confidence 0.85 >= default l3_min_confidence 0.70 -> stable.
    written = await svc.run_l3_pass()
    assert written == 1
    personas = await entity_repository.list_by_entity_type(PERSONA_ENTITY_TYPE, limit=100)
    assert len(personas) == 1
    p = personas[0]
    assert _level(p) == "L3"
    assert _sources(p)  # provenance back to the stable facts

    # Idempotent: re-running L3 updates the single persona in place (fixed title).
    await svc.run_l3_pass()
    personas = await entity_repository.list_by_entity_type(PERSONA_ENTITY_TYPE, limit=100)
    assert len(personas) == 1


@pytest.mark.asyncio
async def test_l3_no_persona_when_no_stable_facts(
    entity_repository, observation_repository, relation_repository,
    search_service, entity_service, app_config, tmp_path,
):
    """With no facts at all, L3 writes nothing."""
    svc = _service(
        entity_repository, observation_repository, relation_repository,
        search_service, entity_service, app_config, tmp_path,
    )
    written = await svc.run_l3_pass()
    assert written == 0
    assert await entity_repository.list_by_entity_type(PERSONA_ENTITY_TYPE, limit=100) == []


# --- trigger dispatch + warmup ---------------------------------------------


@pytest.mark.asyncio
async def test_handle_trigger_l1_dispatch(
    entity_repository, observation_repository, relation_repository,
    search_service, entity_service, app_config, tmp_path,
):
    """An L1 trigger dispatches the L1 (+L2) passes via handle_trigger."""
    await _note(
        entity_service, search_service,
        title="Trig",
        observations=[("definition", "A port is a network endpoint")],
    )
    svc = _service(
        entity_repository, observation_repository, relation_repository,
        search_service, entity_service, app_config, tmp_path,
    )
    trigger = DistillationTrigger(
        trigger_type=TRIGGER_L1_DISTILL,
        project_id=entity_repository.project_id,
        reason="test",
        target_level="L1",
        max_memories=50,
        fired_at=datetime.now(timezone.utc),
    )
    await svc.handle_trigger(trigger)
    assert len(await entity_repository.list_by_entity_type(FACT_ENTITY_TYPE, limit=100)) == 1


@pytest.mark.asyncio
async def test_handle_trigger_warmup_is_noop(
    entity_repository, observation_repository, relation_repository,
    search_service, entity_service, app_config, tmp_path,
):
    """A warmup trigger (retrieval-only for MVP) writes nothing."""
    await _note(
        entity_service, search_service,
        title="Warm",
        observations=[("definition", "A socket is a file descriptor")],
    )
    svc = _service(
        entity_repository, observation_repository, relation_repository,
        search_service, entity_service, app_config, tmp_path,
    )
    trigger = DistillationTrigger(
        trigger_type=TRIGGER_WARMUP,
        project_id=entity_repository.project_id,
        reason="test",
        target_level="L0",
        max_memories=50,
        fired_at=datetime.now(timezone.utc),
        depth=1,
    )
    await svc.handle_trigger(trigger)
    assert await entity_repository.list_by_entity_type(FACT_ENTITY_TYPE, limit=100) == []


# --- embeddings-off similarity (Jaccard) -----------------------------------


def test_similarity_jaccard_when_embeddings_off(entity_repository, tmp_path):
    """With no embedding_service, similarity is token-Jaccard (deterministic)."""
    svc = DistillationService(
        entity_repository=entity_repository,
        observation_repository=None,  # type: ignore[arg-type]
        relation_repository=None,  # type: ignore[arg-type]
        search_service=None,  # type: ignore[arg-type]
        entity_service=None,  # type: ignore[arg-type]
        app_config=None,  # type: ignore[arg-type]
        project_id=entity_repository.project_id,
        state_path=tmp_path / "s.json",
    )
    assert svc._similarity("hello world", "world hello") == 1.0  # same token set
    assert svc._similarity("hello world", "hello there") == pytest.approx(1 / 3)
    assert svc._similarity("completely different", "totally unrelated text") == 0.0


# --- custom extractor seam --------------------------------------------------


@pytest.mark.asyncio
async def test_custom_extractor_is_used(
    entity_repository, observation_repository, relation_repository,
    search_service, entity_service, app_config, tmp_path,
):
    """A custom FactExtractor's candidates flow through to L1 creation."""

    class UpperExtractor:
        async def extract(self, entity, observations, *, categories):
            return [
                FactCandidate(
                    title="custom-fact-abcdef",
                    text="CUSTOM DISTILLED FACT",
                    category="fact",
                    source_permalink=entity.permalink,
                    source_entity_id=entity.id,
                    confidence=0.99,
                )
            ]

    await _note(
        entity_service, search_service,
        title="Custom Src",
        observations=[("note", "anything")],  # would be skipped by CodeExtractor
    )
    svc = _service(
        entity_repository, observation_repository, relation_repository,
        search_service, entity_service, app_config, tmp_path,
        extractor=UpperExtractor(),  # type: ignore[arg-type]
    )
    created = await svc.run_l1_pass(max_memories=50)
    assert created == 1
    facts = await entity_repository.list_by_entity_type(FACT_ENTITY_TYPE, limit=100)
    assert len(facts) == 1
    # The custom text reached the fact's observation.
    fact_obs = next(
        (o for o in (facts[0].observations or []) if o.category == "fact"), None
    )
    assert fact_obs is not None
    assert "CUSTOM DISTILLED FACT" in fact_obs.content


# --- skills-on: skill asset + tunables -------------------------------------


@pytest.mark.asyncio
async def test_ensure_distillation_skill_creates_asset_when_skills_on(
    entity_repository, observation_repository, relation_repository,
    search_service, entity_service, app_config, tmp_path,
):
    """With skills_enabled on, first run materializes the `distillation` skill."""
    skills_config = app_config.model_copy(update={"skills_enabled": True})
    svc = _service(
        entity_repository, observation_repository, relation_repository,
        search_service, entity_service, skills_config, tmp_path,
    )
    await svc.ensure_distillation_skill()
    skills = await entity_repository.list_by_entity_type(SKILL_ENTITY_TYPE, limit=100)
    assert any(s.title == DISTILLATION_SKILL_TITLE for s in skills)
    # Idempotent: a second call does not create a duplicate.
    await svc.ensure_distillation_skill()
    skills = await entity_repository.list_by_entity_type(SKILL_ENTITY_TYPE, limit=100)
    assert sum(1 for s in skills if s.title == DISTILLATION_SKILL_TITLE) == 1


@pytest.mark.asyncio
async def test_load_skill_tunables_falls_back_to_config_when_skills_off(
    entity_repository, observation_repository, relation_repository,
    search_service, entity_service, app_config, tmp_path,
):
    """With skills off, tunables come straight from config defaults."""
    svc = _service(
        entity_repository, observation_repository, relation_repository,
        search_service, entity_service, app_config, tmp_path,
    )
    tunables = await svc.load_skill_tunables()
    assert tunables["dedup_similarity_threshold"] == app_config.dedup_similarity_threshold
    assert tunables["l2_similarity_threshold"] == app_config.l2_similarity_threshold
    assert tunables["l3_min_confidence"] == app_config.l3_min_confidence


@pytest.mark.asyncio
async def test_load_skill_tunables_reads_skill_overrides(
    entity_repository, observation_repository, relation_repository,
    search_service, entity_service, app_config, tmp_path,
):
    """A tunables block in the `distillation` skill overrides config defaults."""
    skills_config = app_config.model_copy(update={"skills_enabled": True})
    svc = _service(
        entity_repository, observation_repository, relation_repository,
        search_service, entity_service, skills_config, tmp_path,
    )
    await svc.ensure_distillation_skill()
    # Mutate the skill's tunables to a custom threshold.
    skills = await entity_repository.list_by_entity_type(SKILL_ENTITY_TYPE, limit=100)
    skill = next(s for s in skills if s.title == DISTILLATION_SKILL_TITLE)
    md = dict(skill.entity_metadata or {})
    md["tunables"] = {**md.get("tunables", {}), "dedup_similarity_threshold": 0.123}
    await entity_repository.update(skill.id, {"entity_metadata": md})

    tunables = await svc.load_skill_tunables()
    assert tunables["dedup_similarity_threshold"] == 0.123


# --- L2 debounce via scheduler ----------------------------------------------


@pytest.mark.asyncio
async def test_l2_debounced_by_scheduler(
    entity_repository, observation_repository, relation_repository,
    search_service, entity_service, app_config, tmp_path,
):
    """When the scheduler says L2 is not due, run_l2_pass is a no-op (debounced)."""
    await _note(
        entity_service, search_service,
        title="Debounced",
        observations=[
            ("definition", "A queue is a FIFO buffer"),
            ("definition", "A stack is a LIFO buffer"),
        ],
    )
    scheduler = DistillationScheduler(PipelineConfig.from_app_config(app_config))
    # Force the debounce watermark to "just fired" so should_trigger_l2 is False.
    scheduler.mark_l2_fired(entity_repository.project_id, now=datetime.now(timezone.utc))
    svc = _service(
        entity_repository, observation_repository, relation_repository,
        search_service, entity_service, app_config, tmp_path,
        scheduler=scheduler,
    )
    await svc.run_l1_pass(max_memories=50)
    scenarios = await svc.run_l2_pass()  # debounced -> 0
    assert scenarios == 0


# --- dispatcher degrades gracefully -----------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_swallows_service_failures(
    entity_repository, observation_repository, relation_repository,
    search_service, entity_service, app_config, tmp_path,
):
    """A failing service factory must never propagate to the caller (fire-and-forget)."""

    async def failing_factory(project_id: int):
        raise RuntimeError("boom")

    dispatcher = DistillationDispatcher(failing_factory)  # type: ignore[arg-type]
    trigger = DistillationTrigger(
        trigger_type=TRIGGER_L1_DISTILL,
        project_id=1,
        reason="test",
        target_level="L1",
        max_memories=50,
        fired_at=datetime.now(timezone.utc),
    )
    # Must not raise.
    await dispatcher(trigger)


@pytest.mark.asyncio
async def test_dispatcher_routes_trigger_to_service(
    entity_repository, observation_repository, relation_repository,
    search_service, entity_service, app_config, tmp_path,
):
    """The dispatcher builds the service and routes the trigger through handle_trigger."""
    await _note(
        entity_service, search_service,
        title="Dispatch",
        observations=[("definition", "A cache stores computed results")],
    )
    svc = _service(
        entity_repository, observation_repository, relation_repository,
        search_service, entity_service, app_config, tmp_path,
    )

    async def factory(project_id: int):
        return svc

    dispatcher = DistillationDispatcher(factory)  # type: ignore[arg-type]
    trigger = DistillationTrigger(
        trigger_type=TRIGGER_L1_DISTILL,
        project_id=entity_repository.project_id,
        reason="test",
        target_level="L1",
        max_memories=50,
        fired_at=datetime.now(timezone.utc),
    )
    await dispatcher(trigger)
    assert len(await entity_repository.list_by_entity_type(FACT_ENTITY_TYPE, limit=100)) == 1