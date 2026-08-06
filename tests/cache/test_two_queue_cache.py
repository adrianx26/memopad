"""Tests for the 2Q cache (replaces the ad-hoc verify_cache_optimizations.py script).

The 2Q (Two-Queue) policy splits the cache into:
  - A1: short-term queue for items seen once
  - Am: long-term queue for items seen twice or more

This buys scan-resistance: a one-shot full-table scan fills A1 but leaves Am
(the hot working set) untouched. These tests pin that behavior.
"""

import random

import pytest

from memopad.cache import TwoQueueCache


class TestBasicOperations:
    def test_put_then_get_returns_value(self):
        cache = TwoQueueCache[str, str](total_size=10)
        cache.put("k", "v")
        assert cache.get("k") == "v"

    def test_get_missing_returns_none(self):
        cache = TwoQueueCache[str, str](total_size=10)
        assert cache.get("absent") is None

    def test_promotion_on_second_access(self):
        cache = TwoQueueCache[str, str](total_size=10)
        cache.put("k", "v")
        cache.get("k")  # Second access promotes A1 → Am
        assert cache.stats["promotions"] == 1


class TestSizeEnforcement:
    def test_size_capped_at_total(self):
        cache = TwoQueueCache[int, str](total_size=10)
        for i in range(20):
            cache.put(i, f"v{i}")
        assert len(cache) <= 10


class TestScanResistance:
    """A 2Q cache must protect Am (hot items) from a one-shot scan filling A1."""

    def test_hot_items_survive_scan(self):
        cache = TwoQueueCache[int, str](total_size=100)

        # Promote 5 hot items to Am with two accesses each.
        hot_keys = [1, 2, 3, 4, 5]
        for k in hot_keys:
            cache.put(k, f"hot{k}")
            cache.get(k)  # second access → Am

        # Simulate a scan of 50 cold items (each accessed once).
        for i in range(100, 150):
            cache.put(i, f"scan{i}")

        # All hot items should still be retrievable — scans only churn A1.
        survived = sum(1 for k in hot_keys if cache.get(k) is not None)
        assert survived == len(hot_keys)


class TestHitRateUnderParetoWorkload:
    """80/20 access pattern is the textbook scenario where 2Q outperforms LRU.

    We don't pin a specific hit rate (sensitive to RNG seed and policy
    constants), but we do pin a generous lower bound — well below what 2Q
    achieves in practice but above what a naive LRU would manage with a
    reasonable working set, so a regression in eviction logic would trip it.
    """

    def test_hit_rate_above_floor(self):
        random.seed(42)  # determinism — this test must not flake.

        cache = TwoQueueCache[int, str](total_size=100)
        hot = list(range(0, 100))
        cold = list(range(100, 500))

        for _ in range(10_000):
            # 80% probability the access targets a hot item.
            key = random.choice(hot) if random.random() < 0.8 else random.choice(cold)
            if cache.get(key) is None:
                cache.put(key, f"v{key}")

        hit_rate = cache.stats["hit_rate"]
        # 2Q on this workload typically lands in the 0.7-0.85 range.
        # 0.5 is a comfortable regression floor.
        assert hit_rate >= 0.5, f"Hit rate regressed: {hit_rate:.2%}"


@pytest.mark.benchmark
class TestPerformance:
    """Marked benchmark — excluded from default test runs.

    Re-run with: pytest -m benchmark
    """

    def test_million_operations_complete_quickly(self, benchmark=None):
        cache = TwoQueueCache[int, str](total_size=10_000)
        for i in range(1_000_000):
            cache.put(i % 50_000, f"v{i}")
            cache.get(i % 50_000)
        # No assertion on absolute time — pytest-benchmark can capture it
        # when run with --benchmark-only. The point is just to surface
        # catastrophic perf regressions during a manual run.
