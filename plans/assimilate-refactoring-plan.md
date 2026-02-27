# Assimilate.py Refactoring Implementation Plan

## Overview
This plan details the step-by-step refactoring of `src/memopad/mcp/tools/assimilate.py` to improve code quality, performance, and maintainability.

## Phase 1: Critical Bug Fix (Immediate)
**Goal:** Fix the duplicated exception handling block
**Risk:** Low - Simple deletion of duplicate code
**Estimated Impact:** Fixes potential double-update bug

### Tasks
1. Remove duplicated exception handling block at lines 1264-1282
2. Verify the fix preserves original logic

## Phase 2: Modular Architecture
**Goal:** Split monolithic file into focused modules
**Risk:** Medium - Need to maintain backward compatibility
**Estimated Impact:** Significant maintainability improvement

### New Module Structure
```
src/memopad/mcp/tools/assimilate/
├── __init__.py          # Main tool entry point + exports
├── config.py            # Configuration constants and dataclasses
├── types.py             # Type definitions and TypedDicts
├── html_utils.py        # HTML parsing: LinkExtractor, HTMLToText
├── file_processor.py    # FileProcessor class for PDF/DOCX/XLSX/Image
├── content_detector.py  # Content type detection patterns and logic
├── note_builders.py     # Consolidated note builder factory
├── crawler.py           # Web crawler with connection pooling
├── github.py            # GitHub repository cloning
└── utils.py             # Shared utilities (_safe_truncate, etc.)
```

### Phase 2 Tasks
1. Create directory structure
2. Extract configuration constants to `config.py`
3. Create type definitions in `types.py`
4. Move HTML utilities to `html_utils.py`
5. Move FileProcessor to `file_processor.py`
6. Move content detection to `content_detector.py`
7. Implement factory pattern in `note_builders.py`
8. Create crawler module with connection pooling
9. Move GitHub operations to `github.py`
10. Update main `__init__.py` to wire everything together

## Phase 3: Performance Optimizations
**Goal:** Implement performance improvements
**Risk:** Low-Medium - Need to maintain behavior
**Estimated Impact:** Better resource usage and speed

### Tasks
1. Implement shared HTTP client with connection pooling
2. Optimize content detection with compiled regex
3. Add memory-efficient chunked file reading
4. Implement request caching with LRU
5. Add concurrent file processing with semaphore

## Phase 4: Testing & Validation
**Goal:** Ensure all changes work correctly
**Risk:** Low - Using existing tests
**Estimated Impact:** Confidence in refactoring

### Tasks
1. Run existing unit tests
2. Run integration tests
3. Perform manual smoke test
4. Verify no regressions

## Detailed Implementation Steps

### Step 1: Critical Bug Fix
```python
# In assimilate.py, lines 1264-1282
# DELETE the duplicate block - only one update attempt needed
```

### Step 2: Configuration Module (config.py)
```python
from dataclasses import dataclass

@dataclass(frozen=True)
class AssimilateConfig:
    max_file_read_size: int = 1_000_000_000
    default_max_files: int = 2_000
    max_note_content: int = 49_000_000
    large_file_threshold: int = 10_000_000
    git_timeout: float = 300.0
    rate_limit_delay: float = 0.5
    max_crawl_depth: int = 10
    http_timeout: float = 15.0
    max_concurrent_requests: int = 5
```

### Step 3: Type Definitions (types.py)
```python
from typing import TypedDict, NotRequired

class PageData(TypedDict):
    url: str
    text: str
    content_types: list[str]
    links: dict[str, list[str]]
    is_file: bool

class CrawlResult(TypedDict):
    pages: list[PageData]
    all_github_links: list[str]
    all_external_links: list[str]
    errors: list[str]
```

### Step 4: Note Builder Factory (note_builders.py)
```python
from dataclasses import dataclass

@dataclass
class NoteBuilderConfig:
    content_type: str
    title: str
    description: str
    max_chars: int = 5000

NOTE_BUILDERS = [
    NoteBuilderConfig("agent_profile", "Agent Profiles & System Prompts", 
                     "Extracted agent profiles, system prompts, and AI instructions"),
    NoteBuilderConfig("skills_rules", "Skills, Rules & Workflows",
                     "Extracted skills definitions, rules files, and workflow patterns"),
    # ... etc
]

def build_note(data: CrawlResult, config: NoteBuilderConfig) -> str | None:
    """Factory function for building notes."""
    ...
```

### Step 5: HTTP Client with Pooling (crawler.py)
```python
import httpx
from contextlib import asynccontextmanager

@asynccontextmanager
async def get_http_client():
    limits = httpx.Limits(
        max_connections=10,
        max_keepalive_connections=5
    )
    async with httpx.AsyncClient(
        headers=DEFAULT_HEADERS,
        limits=limits,
        timeout=httpx.Timeout(15.0)
    ) as client:
        yield client
```

## Migration Strategy

### Option A: Incremental (Recommended)
1. Create new modules alongside existing code
2. Migrate functions one at a time
3. Update imports gradually
4. Remove old code after validation

### Option B: Big Bang
1. Create all modules at once
2. Replace original file
3. Run full test suite
4. Fix any issues

**Recommendation:** Use Option A (Incremental) to minimize risk and allow rollback at each step.

## Rollback Plan

At each phase:
1. Keep backup of original file until phase is complete
2. Use git commits between phases
3. Maintain compatibility layer if needed

## Success Criteria

- [ ] All existing tests pass
- [ ] No functional regressions
- [ ] Code coverage maintained at 100%
- [ ] File size reduced per module (<300 lines each)
- [ ] Duplicated code eliminated
- [ ] Performance improved or maintained

## Dependencies to Add

None - using only existing dependencies (httpx, etc.)

## Timeline Estimate

- Phase 1 (Bug Fix): 15 minutes
- Phase 2 (Modularization): 2-3 hours
- Phase 3 (Optimizations): 1-2 hours
- Phase 4 (Testing): 30 minutes
- Total: ~4-6 hours
