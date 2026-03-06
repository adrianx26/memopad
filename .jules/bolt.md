## 2024-05-18 - Missing SearchRepository implementation for `bulk_delete_by_permalinks`
**Learning:** `SearchRepositoryBase` and the `SearchRepository` protocol reference a `bulk_delete_by_permalinks` method according to instructions, but it is currently missing in the codebase.
**Action:** Implement `bulk_delete_by_permalinks` in `SearchRepository` and `SearchRepositoryBase`.
