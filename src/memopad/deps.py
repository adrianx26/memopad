"""Dependency injection functions for memopad services.

DEPRECATED: This module is a backwards-compatibility shim.
Import from memopad.deps package submodules instead:
- memopad.deps.config for configuration
- memopad.deps.db for database/session
- memopad.deps.projects for project resolution
- memopad.deps.repositories for data access
- memopad.deps.services for business logic
- memopad.deps.importers for import functionality

This file will be removed once all callers are migrated.
"""

# Re-export everything from the deps package for backwards compatibility
from memopad.deps import *  # noqa: F401, F403  # pragma: no cover
