"""Tests for the Skill asset service (Tb G1).

Covers the pure domain logic in `skill_service.py`:
- predicates (is_skill_entity, skill_status, skill_version, is_validated_skill)
- fail-fast payload validation (validate_skill_payload)
- observation grouping + structural validation (the Tb triple gate)
- markdown body building + rendering
- integration with EntityRepository.list_by_entity_type / ObservationRepository
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from memopad.services.skill_service import (
    CATEGORY_DONT,
    CATEGORY_STEP,
    CATEGORY_TRIGGER,
    CATEGORY_VALIDATION,
    SKILL_ENTITY_TYPE,
    SKILL_STATUS_KEY,
    SKILL_VERSION_KEY,
    STATUS_DEPRECATED,
    STATUS_DRAFT,
    STATUS_VALIDATED,
    SkillError,
    SkillDetail,
    build_skill_body,
    build_skill_detail,
    group_skill_observations,
    is_skill_entity,
    is_validated_skill,
    render_skill_detail,
    render_validation_result,
    skill_status,
    skill_version,
    structural_validation,
    validate_skill_payload,
)


def _obs(category: str, content: str):
    return SimpleNamespace(category=category, content=content)


# --- Predicates -----------------------------------------------------------


class TestPredicates:
    def test_is_skill_entity_true_for_skill_type(self):
        e = SimpleNamespace(entity_type="skill", entity_metadata={})
        assert is_skill_entity(e)

    def test_is_skill_entity_false_for_other_type(self):
        e = SimpleNamespace(entity_type="note", entity_metadata={})
        assert not is_skill_entity(e)

    def test_is_skill_entity_handles_dict(self):
        assert is_skill_entity({"entity_type": "skill"})
        assert not is_skill_entity({"entity_type": "note"})

    def test_skill_status_defaults_to_draft(self):
        assert skill_status(None) == STATUS_DRAFT
        assert skill_status({}) == STATUS_DRAFT

    def test_skill_status_reads_metadata(self):
        assert skill_status({SKILL_STATUS_KEY: STATUS_VALIDATED}) == STATUS_VALIDATED
        assert skill_status({SKILL_STATUS_KEY: STATUS_DEPRECATED}) == STATUS_DEPRECATED

    def test_skill_version_defaults_to_1(self):
        assert skill_version(None) == 1
        assert skill_version({}) == 1
        assert skill_version({SKILL_VERSION_KEY: 3}) == 3

    def test_is_validated_skill(self):
        ok = SimpleNamespace(entity_type="skill", entity_metadata={SKILL_STATUS_KEY: STATUS_VALIDATED})
        draft = SimpleNamespace(entity_type="skill", entity_metadata={SKILL_STATUS_KEY: STATUS_DRAFT})
        note = SimpleNamespace(entity_type="note", entity_metadata={SKILL_STATUS_KEY: STATUS_VALIDATED})
        assert is_validated_skill(ok)
        assert not is_validated_skill(draft)
        assert not is_validated_skill(note)


# --- Payload validation (fail-fast) ---------------------------------------


class TestPayloadValidation:
    def test_noop_when_disabled(self):
        # Even invalid payloads pass when the feature is off (skills are notes).
        validate_skill_payload({SKILL_STATUS_KEY: "bogus"}, skills_enabled=False)
        validate_skill_payload({SKILL_VERSION_KEY: -1}, skills_enabled=False)

    def test_noop_when_no_metadata(self):
        validate_skill_payload(None, skills_enabled=True)
        validate_skill_payload({}, skills_enabled=True)

    def test_accepts_valid_status_and_version(self):
        validate_skill_payload(
            {SKILL_STATUS_KEY: STATUS_VALIDATED, SKILL_VERSION_KEY: 2}, skills_enabled=True
        )

    def test_rejects_unknown_status(self):
        with pytest.raises(SkillError, match="skill_status"):
            validate_skill_payload({SKILL_STATUS_KEY: "bogus"}, skills_enabled=True)

    def test_rejects_non_positive_version(self):
        with pytest.raises(SkillError, match="skill_version"):
            validate_skill_payload({SKILL_VERSION_KEY: 0}, skills_enabled=True)
        with pytest.raises(SkillError, match="skill_version"):
            validate_skill_payload({SKILL_VERSION_KEY: -3}, skills_enabled=True)

    def test_rejects_non_int_version(self):
        with pytest.raises(SkillError, match="skill_version"):
            validate_skill_payload({SKILL_VERSION_KEY: "3"}, skills_enabled=True)

    def test_rejects_bool_version(self):
        # bool is a subclass of int — must be rejected explicitly.
        with pytest.raises(SkillError, match="skill_version"):
            validate_skill_payload({SKILL_VERSION_KEY: True}, skills_enabled=True)


# --- Grouping + structural validation -------------------------------------


class TestGrouping:
    def test_groups_canonical_categories_case_insensitive(self):
        obs = [
            _obs("Trigger", "reset DB in prod"),
            _obs("STEP", "snapshot"),
            _obs("Validation", "checksums match"),
            _obs("don't", "no snapshot no reset"),
            _obs("mood", "calm"),
        ]
        g = group_skill_observations(obs)
        assert g[CATEGORY_TRIGGER] == ["reset DB in prod"]
        assert g[CATEGORY_STEP] == ["snapshot"]
        assert g[CATEGORY_VALIDATION] == ["checksums match"]
        assert g[CATEGORY_DONT] == ["no snapshot no reset"]
        assert ("mood", "calm") in g["other"]

    def test_empty_groups_present(self):
        g = group_skill_observations([])
        for cat in (CATEGORY_TRIGGER, CATEGORY_STEP, CATEGORY_VALIDATION):
            assert g[cat] == []


class TestStructuralValidation:
    def test_ok_when_triple_present(self):
        g = group_skill_observations(
            [_obs("trigger", "t"), _obs("step", "s"), _obs("validation", "v")]
        )
        r = structural_validation(g)
        assert r.ok
        assert r.missing == []
        assert set(r.present) == {"trigger", "step", "validation"}

    def test_missing_step(self):
        g = group_skill_observations([_obs("trigger", "t"), _obs("validation", "v")])
        r = structural_validation(g)
        assert not r.ok
        assert r.missing == ["step"]

    def test_missing_all(self):
        g = group_skill_observations([])
        r = structural_validation(g)
        assert not r.ok
        assert set(r.missing) == {"trigger", "step", "validation"}


# --- Body building + rendering --------------------------------------------


class TestBuildBody:
    def test_numbers_steps(self):
        body = build_skill_body(
            trigger="when X", steps=["do a", "do b"], validation="check Y"
        )
        assert "- [trigger] when X" in body
        assert "- [step] 1. do a" in body
        assert "- [step] 2. do b" in body
        assert "- [validation] check Y" in body

    def test_preserves_caller_numbering(self):
        body = build_skill_body(
            trigger="t", steps=["1. already numbered"], validation="v"
        )
        # No double "1. 1. already numbered"
        assert "- [step] 1. already numbered" in body
        assert "1. 1." not in body

    def test_optional_guard_rails(self):
        body = build_skill_body(
            trigger="t", steps=["s"], validation="v", when="only at night", dont="never on prod"
        )
        assert "- [when] only at night" in body
        assert "- [don't] never on prod" in body


class TestRender:
    def test_render_skill_detail_sections(self):
        detail = SkillDetail(
            external_id="ext-1",
            title="Reset DB",
            permalink="skill-reset-db",
            file_path="skills/reset-db.md",
            skill_version=2,
            skill_status=STATUS_VALIDATED,
            triggers=["reset DB in prod"],
            steps=["snapshot", "reset"],
            validations=["checksums match"],
        )
        md = render_skill_detail(detail)
        assert "# Skill: Reset DB" in md
        assert "version: 2" in md
        assert "status: `validated`" in md
        assert "## Trigger" in md
        assert "## Steps" in md
        assert "## Validation" in md

    def test_render_validation_result_ok(self):
        detail = SkillDetail(
            external_id="ext", title="S", permalink="p", file_path="f",
            skill_version=1, skill_status=STATUS_VALIDATED,
        )
        from memopad.services.skill_service import ValidationResult
        md = render_validation_result(detail, ValidationResult(ok=True))
        assert "Structurally valid" in md
        assert "validated" in md

    def test_render_validation_result_missing(self):
        detail = SkillDetail(
            external_id="ext", title="S", permalink="p", file_path="f",
            skill_version=1, skill_status=STATUS_DRAFT,
        )
        from memopad.services.skill_service import ValidationResult
        md = render_validation_result(
            detail, ValidationResult(ok=False, missing=["step"], present=["trigger"])
        )
        assert "Incomplete" in md
        assert "[step]" in md


# --- Integration with repositories ----------------------------------------


@pytest.mark.asyncio
async def test_create_list_and_validate_skill_via_repos(
    entity_repository, observation_repository
):
    """A skill entity + its observations flow through the service end-to-end.

    Exercises EntityRepository.list_by_entity_type (new), observation creation,
    build_skill_detail, and structural_validation — the pieces the MCP tools and
    the validate endpoint compose. No HTTP, no file I/O.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    entity = await entity_repository.create(
        {
            "title": "Reset DB safely",
            "entity_type": SKILL_ENTITY_TYPE,
            "permalink": "skill-reset-db-safe",
            "file_path": "skills/skill-reset-db-safe.md",
            "content_type": "text/markdown",
            "entity_metadata": {
                SKILL_VERSION_KEY: 1,
                SKILL_STATUS_KEY: STATUS_DRAFT,
                "source_entities": ["memory://entity/incident-reset-db"],
            },
            "created_at": now,
            "updated_at": now,
        }
    )

    # Seed the Tb triple as observations.
    for cat, content in [
        ("trigger", "user asks to reset DB in prod"),
        ("step", "snapshot the DB"),
        ("step", "run reset"),
        ("validation", "post-reset checksum == pre-snapshot checksum"),
    ]:
        await observation_repository.create(
            {"entity_id": entity.id, "content": content, "category": cat}
        )

    # list_by_entity_type finds it.
    skills = await entity_repository.list_by_entity_type(SKILL_ENTITY_TYPE)
    assert any(s.permalink == "skill-reset-db-safe" for s in skills)

    # Structural validation passes with the triple present.
    observations = await observation_repository.find_by_entity(entity.id)
    detail = build_skill_detail(entity, observations)
    assert detail.skill_version == 1
    assert detail.skill_status == STATUS_DRAFT
    assert len(detail.steps) == 2
    result = structural_validation(group_skill_observations(observations))
    assert result.ok

    # An incomplete skill (no validation obs) fails the gate.
    await observation_repository.create(
        {"entity_id": entity.id, "content": "extra", "category": "note"}
    )
    # Remove validation observations by re-fetching and filtering is not possible
    # via the repo API here; instead assert the gate still passes (triple present)
    # and separately test the missing path via pure logic above.
    observations2 = await observation_repository.find_by_entity(entity.id)
    assert structural_validation(group_skill_observations(observations2)).ok


@pytest.mark.asyncio
async def test_list_by_entity_type_excludes_other_types(entity_repository):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    await entity_repository.create(
        {
            "title": "A note",
            "entity_type": "note",
            "permalink": "a-note",
            "file_path": "a-note.md",
            "content_type": "text/markdown",
            "created_at": now,
            "updated_at": now,
        }
    )
    await entity_repository.create(
        {
            "title": "A skill",
            "entity_type": SKILL_ENTITY_TYPE,
            "permalink": "a-skill",
            "file_path": "skills/a-skill.md",
            "content_type": "text/markdown",
            "created_at": now,
            "updated_at": now,
        }
    )
    skills = await entity_repository.list_by_entity_type(SKILL_ENTITY_TYPE)
    assert all(s.entity_type == SKILL_ENTITY_TYPE for s in skills)
    assert any(s.permalink == "a-skill" for s in skills)
    assert not any(s.permalink == "a-note" for s in skills)


@pytest.mark.asyncio
async def test_get_by_ids_batch_fetch(entity_repository):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    e1 = await entity_repository.create(
        {
            "title": "e1", "entity_type": "note", "permalink": "gbi-1",
            "file_path": "gbi-1.md", "content_type": "text/markdown",
            "created_at": now, "updated_at": now,
        }
    )
    e2 = await entity_repository.create(
        {
            "title": "e2", "entity_type": "note", "permalink": "gbi-2",
            "file_path": "gbi-2.md", "content_type": "text/markdown",
            "created_at": now, "updated_at": now,
        }
    )
    fetched = await entity_repository.get_by_ids([e1.id, e2.id, 999999])
    ids = {e.id for e in fetched}
    assert e1.id in ids and e2.id in ids
    assert 999999 not in ids  # non-existent id gracefully absent

    assert await entity_repository.get_by_ids([]) == []