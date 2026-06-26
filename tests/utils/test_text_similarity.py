"""Tests for the shared `character_overlap` metric (Phase 6.3).

One metric, tested once. `conflict_service._similarity_ratio` and
`schema_service._name_overlap` both delegate here, so their existing tests
already exercise it transitively; this file pins the contract directly.
"""

from memopad.text_similarity import character_overlap


def test_identical_strings_score_one():
    assert character_overlap("status", "status") == 1.0


def test_disjoint_strings_score_zero():
    assert character_overlap("abc", "xyz") == 0.0


def test_both_empty_score_one():
    # vacuous-identity contract; callers guard if they want different semantics
    assert character_overlap("", "") == 1.0


def test_one_empty_score_zero():
    assert character_overlap("abc", "") == 0.0


def test_partial_overlap_in_unit_interval():
    score = character_overlap("active", "active2")
    assert 0.0 < score < 1.0


def test_symmetric():
    assert character_overlap("foo", "foobar") == character_overlap("foobar", "foo")


def test_case_sensitive_by_design():
    # Raw metric — no normalization. Callers normalize first if they need it.
    assert character_overlap("Status", "status") < 1.0


def test_order_independent():
    # anagram of the same characters -> 1.0
    assert character_overlap("listen", "silent") == 1.0