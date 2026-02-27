## 2024-05-14 - Identify missing call to adaptive TTL calculation
**Learning:** In `FileService.get_file_metadata`, there is logic for an adaptive TTL `_calculate_adaptive_ttl`, but it's not being used during caching. The code uses `self._metadata_cache_ttl` directly.
**Action:** Update `get_file_metadata` and `_evict_metadata_cache_if_needed` to correctly store and utilize the adaptive TTL for each cached item.
