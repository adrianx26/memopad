"""Shared text-similarity metrics.

Phase 6.3: `schema_service._name_overlap` and `conflict_service._similarity_ratio`
both implemented the same character-multiset (Dice-like) overlap independently.
This module holds the shared core so the metric is defined — and tested — once.
Callers compose their own normalization/equality semantics on top.
"""

from collections import Counter


def character_overlap(a: str, b: str) -> float:
    """Character-multiset overlap (Sørensen–Dice coefficient over character bags).

    Symmetric, order-independent measure of how many characters two strings share:
        2 * |multiset(a) ∩ multiset(b)| / (|multiset(a)| + |multiset(b)|)

    Returns a float in [0, 1]:
      - 1.0 when both strings are empty (vacuously identical — callers that
        want different semantics for empty input should guard before calling),
      - 1.0 when the strings are the same multiset of characters,
      - 0.0 when they share no characters.

    This is intentionally a *raw* metric: it does no case-folding or whitespace
    normalization. Callers that need normalization (e.g. conflict detection wants
    case-insensitive, whitespace-collapsed comparison) should normalize their
    inputs first, then call this.

    Args:
        a: First string.
        b: Second string.

    Returns:
        Overlap ratio in [0, 1].
    """
    ca, cb = Counter(a), Counter(b)
    common = sum((ca & cb).values())
    total = sum(ca.values()) + sum(cb.values())
    if total == 0:
        return 1.0
    return (2 * common) / total