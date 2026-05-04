"""Tests for confidence / source_method fields on Relation and RelationResponse.

These fields are infrastructure for a future AI-relation-extraction pass.
For now, every relation is user-authored (confidence=1.0, source_method='user_wikilink').
The tests verify:
  - Defaults are sensible when callers don't supply the new fields.
  - Explicit values round-trip correctly through schema validation.
  - RelationResponse picks up the fields from both dict and ORM-like objects.
  - Confidence is range-validated (0.0 ≤ confidence ≤ 1.0).
"""

import pytest
from pydantic import ValidationError

from memopad.schemas.base import Relation
from memopad.schemas.response import RelationResponse


# ---------------------------------------------------------------------------
# Base Relation schema
# ---------------------------------------------------------------------------


def test_relation_defaults_confidence_and_source_method():
    """When a caller omits the new fields they get baseline provenance values."""
    r = Relation(from_id="a/note", to_id="b/other", relation_type="relates_to")
    assert r.confidence == 1.0
    assert r.source_method == "user_wikilink"


def test_relation_explicit_confidence():
    """Explicit confidence value is accepted and stored."""
    r = Relation(
        from_id="a/note",
        to_id="b/other",
        relation_type="calls",
        confidence=0.75,
        source_method="ai_extracted",
    )
    assert r.confidence == 0.75
    assert r.source_method == "ai_extracted"


def test_relation_confidence_zero_is_valid():
    r = Relation(
        from_id="a/note", to_id="b/other", relation_type="calls", confidence=0.0
    )
    assert r.confidence == 0.0


def test_relation_confidence_one_is_valid():
    r = Relation(
        from_id="a/note", to_id="b/other", relation_type="calls", confidence=1.0
    )
    assert r.confidence == 1.0


def test_relation_confidence_above_one_is_rejected():
    with pytest.raises(ValidationError, match="less than or equal to 1"):
        Relation(
            from_id="a/note",
            to_id="b/other",
            relation_type="calls",
            confidence=1.5,
        )


def test_relation_confidence_below_zero_is_rejected():
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        Relation(
            from_id="a/note",
            to_id="b/other",
            relation_type="calls",
            confidence=-0.1,
        )


def test_relation_source_method_none_is_allowed():
    """None is acceptable (nullable column)."""
    r = Relation(
        from_id="a/note",
        to_id="b/other",
        relation_type="extends",
        confidence=None,
        source_method=None,
    )
    assert r.confidence is None
    assert r.source_method is None


# ---------------------------------------------------------------------------
# RelationResponse — dict path
# ---------------------------------------------------------------------------


def test_relation_response_carries_confidence_from_dict():
    data = {
        "permalink": "rel/1",
        "relation_type": "relates_to",
        "context": None,
        "to_name": None,
        "from_id": "a/note",
        "to_id": "b/other",
        "confidence": 0.5,
        "source_method": "ai_extracted",
    }
    rel = RelationResponse.model_validate(data)
    assert rel.confidence == 0.5
    assert rel.source_method == "ai_extracted"


def test_relation_response_defaults_confidence_when_missing_from_dict():
    data = {
        "permalink": "rel/2",
        "relation_type": "depends_on",
        "from_id": "a/note",
        "to_id": "b/other",
    }
    rel = RelationResponse.model_validate(data)
    assert rel.confidence == 1.0
    assert rel.source_method == "user_wikilink"


# ---------------------------------------------------------------------------
# RelationResponse — ORM-like object path
# ---------------------------------------------------------------------------


class _EntityLike:
    def __init__(self, permalink, file_path="path.md", title=None):
        self.permalink = permalink
        self.file_path = file_path
        self.title = title


class _RelationLike:
    def __init__(self, confidence=1.0, source_method="user_wikilink"):
        self.permalink = "rel/3"
        self.relation_type = "implements"
        self.context = None
        self.to_name = None
        self.confidence = confidence
        self.source_method = source_method
        self.from_entity = _EntityLike("a/note")
        self.to_entity = _EntityLike("b/other", title="Other")


def test_relation_response_carries_confidence_from_orm_object():
    orm = _RelationLike(confidence=0.8, source_method="ai_extracted")
    rel = RelationResponse.model_validate(orm)
    assert rel.confidence == 0.8
    assert rel.source_method == "ai_extracted"


def test_relation_response_user_wikilink_defaults_from_orm_object():
    orm = _RelationLike()
    rel = RelationResponse.model_validate(orm)
    assert rel.confidence == 1.0
    assert rel.source_method == "user_wikilink"
