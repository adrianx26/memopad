"""Tests for the G1 trigger-matching injection in ContextService (Tb G1).

Two layers are covered:

1. ``skill_service.match_trigger`` — the pure, deterministic predicate that
   decides whether a validated skill's ``[trigger]`` observation is relevant to
   a build_context topic. No DB, no async — straight string reasoning, so it is
   unit-tested directly.

2. ``ContextService._inject_trigger_skills`` — the integration step that lists
   validated skills, reads their ``[trigger]`` observations, and prepends any
   whose trigger matches the topic (and which did not already surface in primary
   search). This uses the real ``entity_repository`` + ``observation_repository``
   fixtures (same pattern as ``test_context_service_skill_boost.py``).

The end-to-end ``build_context`` path is intentionally NOT re-exercised here: it
already has a dedicated E2E test in the boost suite, and re-running it would
re-enter the recursive-CTE code path that has an unrelated pre-existing SQL
binding issue in this environment. Calling ``_inject_trigger_skills`` directly
isolates the new behaviour deterministically.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from memopad.config import MemoPadConfig
from memopad.schemas.search import SearchItemType
from memopad.services.context_service import ContextResultItem, ContextService
from memopad.services.skill_service import (
    CATEGORY_TRIGGER,
    SKILL_ENTITY_TYPE,
    SKILL_STATUS_KEY,
    SKILL_VERSION_KEY,
    STATUS_VALIDATED,
    match_trigger,
)


# --- Shared helpers (mirror test_context_service_skill_boost.py) --------------


def _config_with(**overrides) -> MemoPadConfig:
    cfg = MemoPadConfig()
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _make_item(entity_id: int, *, etype: str = SearchItemType.ENTITY.value):
    """A minimal ContextResultItem whose primary_result duck-types as a row."""
    prim = SimpleNamespace(type=etype, id=entity_id)
    return ContextResultItem(primary_result=prim)


async def _make_entity(repo, *, permalink, title, entity_type, metadata=None):
    now = datetime.now(timezone.utc)
    return await repo.create(
        {
            "title": title,
            "entity_type": entity_type,
            "permalink": permalink,
            "file_path": f"{permalink}.md",
            "content_type": "text/markdown",
            "entity_metadata": metadata or None,
            "created_at": now,
            "updated_at": now,
        }
    )


async def _add_obs(repo, entity_id: int, *, category: str, content: str) -> None:
    await repo.create({"entity_id": entity_id, "content": content, "category": category})


# ============================================================================
# 1. match_trigger — pure predicate
# ============================================================================


def test_match_trigger_true_when_topic_token_appears_in_trigger():
    # Broad trigger, focused topic — the topic word "reset" is also a word in
    # the trigger text, so it matches (token-set intersection on >= 3-char words).
    assert match_trigger(
        ["user asks to reset DB in prod"], "how to reset-db safely"
    ) is True


def test_match_trigger_token_boundary_no_substring_false_positive():
    # Token-boundary matching: a 3-char topic token must match a *word* in the
    # trigger, not a substring of a larger word. "test" is a substring of
    # "latest" but not a word in it, so a skill whose trigger is "latest fixes"
    # must NOT be injected for the topic "test". Bare-substring matching would
    # wrongly return True here — this test locks the token-boundary fix.
    assert match_trigger(["latest fixes in the release"], "test") is False
    # Converse: a topic word that does appear as a word still matches.
    assert match_trigger(["latest fixes in the release"], "latest") is True


def test_match_trigger_false_when_no_token_overlap():
    assert match_trigger(["deploy via helm chart"], "reset-db in prod") is False


def test_match_trigger_case_insensitive():
    assert match_trigger(["RESET the production DB"], "reset") is True


def test_match_trigger_empty_text_returns_false():
    assert match_trigger(["reset DB"], "") is False


def test_match_trigger_empty_triggers_returns_false():
    assert match_trigger([], "reset-db") is False
    assert match_trigger([""], "reset-db") is False


def test_match_trigger_short_tokens_are_dropped():
    # "db" is length 2 — below the >= 3 threshold — so it is not searched, even
    # though "db" literally appears in the trigger. This keeps signal-to-noise
    # high (avoids matching on noise like "id", "v2", path prefixes).
    assert match_trigger(["snapshot the db"], "db") is False


def test_match_trigger_none_inputs_return_false():
    assert match_trigger(["reset DB"], None) is False  # type: ignore[arg-type]
    assert match_trigger(None, "reset-db") is False  # type: ignore[arg-type]


def test_match_trigger_multiple_triggers_any_matches():
    triggers = ["deploy via helm", "reset production DB safely"]
    assert match_trigger(triggers, "reset-db") is True


# ============================================================================
# 2. _inject_trigger_skills — integration against real repos
# ============================================================================


@pytest.mark.asyncio
async def test_inject_prepends_matching_skill_not_in_results(
    entity_repository, observation_repository
):
    """A validated skill whose [trigger] matches the topic is prepended."""
    skill = await _make_entity(
        entity_repository, permalink="sk-trig", title="ResetDbSkill",
        entity_type=SKILL_ENTITY_TYPE,
        metadata={SKILL_VERSION_KEY: 1, SKILL_STATUS_KEY: STATUS_VALIDATED},
    )
    # Seed the Tb triple; only [trigger] matters for the match, but a
    # structurally-complete skill mirrors the real validated state.
    await _add_obs(observation_repository, skill.id, category=CATEGORY_TRIGGER,
                   content="user asks to reset DB in prod")
    await _add_obs(observation_repository, skill.id, category="step",
                   content="snapshot then reset")
    await _add_obs(observation_repository, skill.id, category="validation",
                   content="checksums match")
    note = await _make_entity(
        entity_repository, permalink="nt-trig", title="NoteTrig", entity_type="note"
    )

    svc = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        app_config=_config_with(skills_enabled=True),
    )
    out = await svc._inject_trigger_skills([_make_item(note.id)], topic="reset-db")

    ids = [i.primary_result.id for i in out]
    assert ids[0] == skill.id  # matched skill injected first
    assert note.id in ids  # existing result preserved


@pytest.mark.asyncio
async def test_inject_no_op_when_topic_does_not_match(
    entity_repository, observation_repository
):
    """A validated skill whose trigger is unrelated is not injected."""
    skill = await _make_entity(
        entity_repository, permalink="sk-unrelated", title="DeploySkill",
        entity_type=SKILL_ENTITY_TYPE,
        metadata={SKILL_VERSION_KEY: 1, SKILL_STATUS_KEY: STATUS_VALIDATED},
    )
    await _add_obs(observation_repository, skill.id, category=CATEGORY_TRIGGER,
                   content="deploy via helm chart")
    note = await _make_entity(
        entity_repository, permalink="nt-unrelated", title="N", entity_type="note"
    )

    svc = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        app_config=_config_with(skills_enabled=True),
    )
    out = await svc._inject_trigger_skills([_make_item(note.id)], topic="reset-db")
    assert [i.primary_result.id for i in out] == [note.id]  # unchanged


@pytest.mark.asyncio
async def test_inject_disabled_when_skills_flag_off(
    entity_repository, observation_repository
):
    """Flag off → the step is a no-op even if a trigger would match."""
    skill = await _make_entity(
        entity_repository, permalink="sk-off", title="ResetDbSkill",
        entity_type=SKILL_ENTITY_TYPE,
        metadata={SKILL_VERSION_KEY: 1, SKILL_STATUS_KEY: STATUS_VALIDATED},
    )
    await _add_obs(observation_repository, skill.id, category=CATEGORY_TRIGGER,
                   content="reset DB in prod")
    note = await _make_entity(
        entity_repository, permalink="nt-off", title="N", entity_type="note"
    )

    svc = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        app_config=MemoPadConfig(),  # skills_enabled defaults False
    )
    out = await svc._inject_trigger_skills([_make_item(note.id)], topic="reset-db")
    assert [i.primary_result.id for i in out] == [note.id]


@pytest.mark.asyncio
async def test_inject_no_op_when_topic_is_none(
    entity_repository, observation_repository
):
    """Wildcard/type-only lookups pass topic=None → no injection."""
    skill = await _make_entity(
        entity_repository, permalink="sk-notopic", title="ResetDbSkill",
        entity_type=SKILL_ENTITY_TYPE,
        metadata={SKILL_VERSION_KEY: 1, SKILL_STATUS_KEY: STATUS_VALIDATED},
    )
    await _add_obs(observation_repository, skill.id, category=CATEGORY_TRIGGER,
                   content="reset DB in prod")

    svc = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        app_config=_config_with(skills_enabled=True),
    )
    out = await svc._inject_trigger_skills([], topic=None)
    assert out == []


@pytest.mark.asyncio
async def test_inject_does_not_double_inject_already_present_skill(
    entity_repository, observation_repository
):
    """A skill already in the primary results is not injected again."""
    skill = await _make_entity(
        entity_repository, permalink="sk-present", title="ResetDbSkill",
        entity_type=SKILL_ENTITY_TYPE,
        metadata={SKILL_VERSION_KEY: 1, SKILL_STATUS_KEY: STATUS_VALIDATED},
    )
    await _add_obs(observation_repository, skill.id, category=CATEGORY_TRIGGER,
                   content="reset DB in prod")

    svc = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        app_config=_config_with(skills_enabled=True),
    )
    # The skill is already a primary result.
    out = await svc._inject_trigger_skills([_make_item(skill.id)], topic="reset-db")
    assert [i.primary_result.id for i in out] == [skill.id]  # no duplicate prepended


@pytest.mark.asyncio
async def test_inject_skips_draft_skill(
    entity_repository, observation_repository
):
    """Only validated skills are candidates; a draft skill is never injected."""
    draft = await _make_entity(
        entity_repository, permalink="sk-draft", title="DraftSkill",
        entity_type=SKILL_ENTITY_TYPE,
        metadata={SKILL_VERSION_KEY: 1, SKILL_STATUS_KEY: "draft"},
    )
    await _add_obs(observation_repository, draft.id, category=CATEGORY_TRIGGER,
                   content="reset DB in prod")
    note = await _make_entity(
        entity_repository, permalink="nt-draft", title="N", entity_type="note"
    )

    svc = ContextService(
        search_repository=None,  # type: ignore[arg-type]
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        app_config=_config_with(skills_enabled=True),
    )
    out = await svc._inject_trigger_skills([_make_item(note.id)], topic="reset-db")
    assert [i.primary_result.id for i in out] == [note.id]  # draft not injected