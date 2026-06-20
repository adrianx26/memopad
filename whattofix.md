# MemoPad Code Review - Items to Fix

## Fixed Issues

### 1. Missing `Entity` import in `entity_service.py`
- **File:** `src/memopad/services/entity_service.py:24`
- **Problem:** The code used `Entity` in return type hints (e.g., `-> List[Entity]`) but only imported it as `Entity as EntityModel`.
- **Fix:** Added `Entity` to the import: `from memopad.models import Entity, Observation, Relation`

### 2. Missing `ConflictServiceDep` definition
- **File:** `src/memopad/deps/services.py:257`
- **Problem:** `get_entity_service` function referenced `ConflictServiceDep` but it was never defined (only `ConflictServiceV2Dep` and `ConflictServiceV2ExternalDep` existed).
- **Fix:** Added `ConflictServiceDep = Annotated[ConflictService, Depends(get_conflict_service)]`

### 3. Missing `ConflictServiceV2Dep` and `ConflictServiceV2ExternalDep` exports
- **File:** `src/memopad/deps/__init__.py`
- **Problem:** `ConflictServiceV2Dep` and `ConflictServiceV2ExternalDep` were defined in `services.py` but not exported from the `deps` package.
- **Fix:** Added imports and exports for the missing deps.

## Potential Issues Found

### 1. Missing test for watch_service.py
- **File:** `src/memopad/sync/watch_service.py:304`
- **Note:** `# pragma: no cover TODO add test` - test not yet written

### 2. Pass statement in base.py
- **File:** `src/memopad/importers/base.py:56`
- **Note:** `pass  # pragma: no cover` - empty method, may need implementation

## Verification

All imports now work correctly:
- Entity model import chain
- Service dependency injection
- MCP tools imports
- Repository imports