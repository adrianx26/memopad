"""Two-Queue (2Q) Cache Implementation.

Phase 2 Optimization #5: Advanced cache eviction policy with scan resistance.

The 2Q algorithm maintains two LRU queues:
- A1 (Am-In): Recent items (FIFO) - 25% of cache
- Am (Am-Out): Frequently accessed items (LRU) - 75% of cache

Benefits:
- Scan-resistant: One-time reads stay in A1, don't pollute Am
- Better hit rates: 10-20% improvement over pure LRU
- Adaptive: Automatically learns access patterns

Research: "2Q: A Low Overhead High Performance Buffer Management Replacement Algorithm" (VLDB 1994)
"""

from collections import OrderedDict
from typing import Generic, Optional, TypeVar

from loguru import logger

K = TypeVar("K")
V = TypeVar("V")


class TwoQueueCache(Generic[K, V]):
    """Two-Queue cache with scan resistance.
    
    Maintains two queues:
    - A1 (recent): FIFO queue for recently accessed items
    - Am (frequent): LRU queue for frequently accessed items
    
    When an item is accessed:
    1. First access: Goes to A1 (recent queue)
    2. Second access: Promoted to Am (frequent queue)
    3. Subsequent access in Am: Moved to end (LRU)
    
   This prevents scan pollution - sequential one-time reads
    stay in A1 and don't evict hot data from Am.
    """
    
    def __init__(self, total_size: int):
        """Initialize 2Q cache.
        
        Args:
            total_size: Total cache capacity
        """
        if total_size < 4:
            raise ValueError("Cache size must be at least 4")
        
        # Split capacity: 25% recent, 75% frequent
        self.a1_size = max(1, total_size // 4)
        self.am_size = total_size - self.a1_size
        
        # A1: FIFO queue for recent items (first access)
        self.a1: OrderedDict[K, V] = OrderedDict()
        
        # Am: LRU queue for frequent items (accessed 2+ times)
        self.am: OrderedDict[K, V] = OrderedDict()
        
        # Track metrics
        self.hits = 0
        self.misses = 0
        self.promotions = 0  # A1 -> Am promotions
        
        logger.debug(
            f"Initialized 2Q cache: total={total_size}, "
            f"A1={self.a1_size}, Am={self.am_size}"
        )
    
    def get(self, key: K) -> Optional[V]:
        """Get value from cache.
        
        Args:
            key: Cache key
        
        Returns:
            Cached value or None if not found
        """
        # Check frequent queue first (hot path)
        if key in self.am:
            # Move to end (most recently used)
            self.am.move_to_end(key)
            self.hits += 1
            logger.trace(f"2Q hit (Am): {key}")
            return self.am[key]
        
        # Check recent queue
        if key in self.a1:
            # Second access - promote to frequent queue
            value = self.a1.pop(key)
            self.am[key] = value
            self.am.move_to_end(key)
            self.promotions += 1
            self.hits += 1
            self._evict_if_needed()
            logger.trace(f"2Q hit (A1->Am promotion): {key}")
            return value
        
        # Cache miss
        self.misses += 1
        logger.trace(f"2Q miss: {key}")
        return None
    
    def put(self, key: K, value: V) -> None:
        """Put value into cache.
        
        New entries go to A1 (recent queue).
        
        Args:
            key: Cache key
            value: Value to cache
        """
        # If already in Am, update and move to end
        if key in self.am:
            self.am[key] = value
            self.am.move_to_end(key)
            logger.trace(f"2Q update (Am): {key}")
            return
        
        # If already in A1, update (don't promote yet)
        if key in self.a1:
            self.a1[key] = value
            logger.trace(f"2Q update (A1): {key}")
            return
        
        # New entry goes to A1 (recent queue)
        self.a1[key] = value
        self._evict_if_needed()
        logger.trace(f"2Q insert (A1): {key}")
    
    def _evict_if_needed(self) -> None:
        """Evict entries if queues exceed capacity."""
        # Evict from A1 (FIFO - remove oldest)
        while len(self.a1) > self.a1_size:
            evicted_key, _ = self.a1.popitem(last=False)
            logger.trace(f"2Q evict A1 (FIFO): {evicted_key}")
        
        # Evict from Am (LRU - remove least recently used)
        while len(self.am) > self.am_size:
            evicted_key, _ = self.am.popitem(last=False)
            logger.trace(f"2Q evict Am (LRU): {evicted_key}")
    
    def clear(self) -> None:
        """Clear entire cache."""
        cleared = len(self.a1) + len(self.am)
        self.a1.clear()
        self.am.clear()
        logger.debug(f"Cleared 2Q cache ({cleared} entries)")
    
    def __len__(self) -> int:
        """Return total number of cached items."""
        return len(self.a1) + len(self.am)
    
    def __contains__(self, key: K) -> bool:
        """Check if key is in cache."""
        return key in self.a1 or key in self.am
    
    @property
    def hit_rate(self) -> float:
        """Calculate cache hit rate."""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0
    
    @property
    def stats(self) -> dict:
        """Get cache statistics."""
        return {
            "total_size": self.a1_size + self.am_size,
            "a1_size": self.a1_size,
            "am_size": self.am_size,
            "a1_entries": len(self.a1),
            "am_entries": len(self.am),
            "total_entries": len(self),
            "hits": self.hits,
            "misses": self.misses,
            "promotions": self.promotions,
            "hit_rate": self.hit_rate,
        }
    
    def reset_stats(self) -> None:
        """Reset hit/miss statistics."""
        self.hits = 0
        self.misses = 0
        self.promotions = 0
