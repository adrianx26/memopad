# FTS5 Improvement Recommendations for MemoPad

## Overview

While MemoPad already uses FTS5 effectively, there are several improvements that could enhance the "memory" experience. These recommendations are organized by impact (High/Medium/Low) and effort (Easy/Medium/Hard).

---

## High Impact Improvements

### 1. Add Result Highlighting (Snippets)

**Current State**: Search results show content snippets but don't highlight matching terms.

**Improvement**: Use FTS5's `snippet()` function to highlight matching terms.

```sql
-- Current query (simplified)
SELECT title, content_snippet FROM search_index WHERE content_stems MATCH 'keyword'

-- Improved with highlighting
SELECT 
    title,
    snippet(search_index, 0, '<mark>', '</mark>', '...', 32) as highlighted_title,
    snippet(search_index, 1, '<mark>', '</mark>', '...', 32) as highlighted_content
FROM search_index 
WHERE content_stems MATCH 'keyword'
```

**Benefits**:
- Users immediately see why results matched
- Better UX for understanding search relevance
- Helps debug search queries

**Implementation**: 
- File: [`src/memopad/repository/sqlite_search_repository.py`](src/memopad/repository/sqlite_search_repository.py)
- Add `highlight` field to [`SearchIndexRow`](src/memopad/repository/search_index_row.py)
- Update [`SearchResponse`](src/memopad/schemas/search.py) schema

**Effort**: Medium
**Impact**: High

---

### 2. Column-Specific Weights for Better Ranking

**Current State**: All searchable columns have equal weight in BM25 ranking.

**Improvement**: Weight title matches higher than content matches.

```sql
-- FTS5 supports column weights in CREATE VIRTUAL TABLE
CREATE VIRTUAL TABLE search_index USING fts5(
    title,                          -- Default weight: 1.0
    content_stems,                  -- Default weight: 1.0
    -- Could use auxiliary functions for custom ranking
);

-- Or use bm25 with column weights in query
SELECT *, bm25(search_index, 10.0, 1.0) as weighted_score  -- title=10x weight
FROM search_index 
WHERE search_index MATCH 'keyword'
ORDER BY weighted_score
```

**Benefits**:
- Title matches appear higher (usually more relevant)
- Better relevance ranking
- More intuitive search results

**Implementation**:
- Modify [`bm25()`](src/memopad/repository/sqlite_search_repository.py:485) call to include weights
- May need auxiliary table for column weights

**Effort**: Medium
**Impact**: High

---

### 3. Proximity Search (NEAR Operator)

**Current State**: Boolean operators (AND/OR/NOT) work, but proximity isn't supported.

**Improvement**: Add NEAR operator support for phrase proximity.

```sql
-- Find "machine" within 5 words of "learning"
SELECT * FROM search_index 
WHERE content_stems MATCH 'machine NEAR/5 learning'

-- Current workaround (less precise)
SELECT * FROM search_index 
WHERE content_stems MATCH 'machine AND learning'
```

**Benefits**:
- More precise phrase matching
- Better for technical documentation
- "Machine learning" vs "learning machine" distinction

**Implementation**:
- Update [`_prepare_search_term()`](src/memopad/repository/sqlite_search_repository.py:273) to parse NEAR syntax
- Example: `"machine learning"~5` → `machine NEAR/5 learning`

**Effort**: Easy
**Impact**: Medium-High

---

## Medium Impact Improvements

### 4. Autocomplete Suggestions API

**Current State**: No autocomplete/suggestions feature.

**Improvement**: Leverage prefix matching for autocomplete.

```sql
-- Get suggestions for partial input "proj"
SELECT DISTINCT term FROM search_index_vocab 
WHERE term LIKE 'proj%' 
ORDER BY doc_frequency DESC
LIMIT 10
```

**Alternative using existing index**:
```python
# Use existing prefix capability
suggestions = await search_repository.search(
    search_text="proj*",
    limit=10
)
# Extract unique terms from results
```

**Benefits**:
- Better UX for search discovery
- Helps users find the right terms
- Reduces typos in searches

**Implementation**:
- New MCP tool: `search_suggestions(prefix: str, limit: int = 10)`
- New API endpoint: `/v2/projects/{id}/search/suggestions`
- Use [`SQLiteSearchRepository`](src/memopad/repository/sqlite_search_repository.py) with pattern matching

**Effort**: Medium
**Impact**: Medium

---

### 5. Search Query Spell Correction

**Current State**: Typos return no results.

**Improvement**: Suggest corrections for misspelled terms.

```python
# In: src/memopad/repository/sqlite_search_repository.py

def _suggest_correction(self, term: str) -> Optional[str]:
    """Suggest correction for misspelled term using edit distance."""
    # Query FTS5 vocabulary for similar terms
    sql = """
        SELECT term, edit_distance(term, :input) as dist
        FROM search_index_vocab
        WHERE dist <= 2
        ORDER BY dist, doc_frequency DESC
        LIMIT 1
    """
    # Return suggestion if found
```

**Benefits**:
- Better UX when users make typos
- More forgiving search
- Could suggest "Did you mean: ...?"

**Implementation**:
- FTS5 has a `vocab` auxiliary table for term frequency
- Add [`edit_distance()`](src/memopad/repository/sqlite_search_repository.py) SQLite function
- Return suggestions in [`SearchResponse`](src/memopad/schemas/search.py)

**Effort**: Hard
**Impact**: Medium

---

### 6. Recent/Frequent Search Boosting

**Current State**: All results ranked purely by BM25 relevance.

**Improvement**: Boost recently updated or frequently accessed content.

```sql
-- Combine BM25 score with recency boost
SELECT 
    *,
    bm25(search_index) as relevance_score,
    (julianday('now') - julianday(updated_at)) as age_days,
    -- Recency decay: newer = higher score
    (relevance_score * (1.0 / (1.0 + age_days / 30.0))) as final_score
FROM search_index
WHERE content_stems MATCH 'keyword'
ORDER BY final_score
```

**Benefits**:
- Recent notes appear higher
- Better for active projects
- Combines relevance with freshness

**Implementation**:
- Modify [`search()`](src/memopad/repository/sqlite_search_repository.py:293) SQL to include recency factor
- Add `recency_boost` parameter to [`SearchQuery`](src/memopad/schemas/search.py)

**Effort**: Medium
**Impact**: Medium

---

### 7. Semantic Search Hybrid (Future)

**Current State**: Pure keyword-based search.

**Improvement**: Hybrid keyword + semantic (embedding-based) search.

```python
# Pseudo-implementation
async def hybrid_search(query: str, semantic_weight: float = 0.3):
    # 1. Get FTS5 results
    fts_results = await fts_search(query)
    
    # 2. Get semantic results (requires embeddings)
    embedding = await generate_embedding(query)
    semantic_results = await vector_search(embedding)
    
    # 3. Combine and re-rank
    combined = merge_results(fts_results, semantic_results, weights=[0.7, 0.3])
    return combined
```

**Note**: This requires significant infrastructure (embedding model, vector storage).

**Effort**: Hard
**Impact**: Very High

---

## Low Impact / Quick Wins

### 8. Search Query Analytics

**Current State**: No visibility into what users search for.

**Improvement**: Log search queries (anonymized) for analysis.

```python
# In: src/memopad/services/search_service.py
async def search(self, query: SearchQuery, ...):
    logger.info(f"Search query: {query.text}, filters: {query.metadata_filters}")
    # ... rest of search
```

**Benefits**:
- Understand user search patterns
- Identify missing content
- Optimize search ranking

**Effort**: Easy
**Impact**: Low (internal)

---

### 9. Empty Result Suggestions

**Current State**: Empty results return nothing.

**Improvement**: Suggest alternative queries when no results found.

```python
# Already partially implemented in _format_search_error_response()
# Could expand to suggest related terms

async def get_related_terms(failed_query: str) -> List[str]:
    """Suggest alternative terms when search fails."""
    # Break query into terms
    # Find related terms in vocabulary
    # Return suggestions
```

**Benefits**:
- Better UX for failed searches
- Guides users to relevant content

**Effort**: Easy
**Impact**: Low-Medium

---

## Implementation Roadmap

### Phase 1: Quick Wins (1-2 weeks)
1. ✅ Empty result suggestions (already partially done)
2. ✅ Search query analytics (add logging)
3. ✅ Proximity search (NEAR operator)

### Phase 2: UX Improvements (2-4 weeks)
4. 🔲 Result highlighting (snippets)
5. 🔲 Column-specific weights
6. 🔲 Autocomplete API

### Phase 3: Advanced Features (4-8 weeks)
7. 🔲 Spell correction
8. 🔲 Recency boosting
9. 🔲 Semantic search (requires infrastructure)

---

## PostgreSQL Parity

Some improvements should be implemented for both backends:

| Feature | SQLite FTS5 | PostgreSQL tsvector |
|---------|-------------|---------------------|
| Highlighting | `snippet()` | `ts_headline()` |
| Weights | `bm25(weights)` | `ts_rank(weights)` |
| Autocomplete | `vocab` table | `pg_trgm` extension |
| Spell correction | Custom | `pg_trgm` similarity |
| Recency boost | Custom formula | Custom formula |

**Files to update for parity**:
- [`src/memopad/repository/sqlite_search_repository.py`](src/memopad/repository/sqlite_search_repository.py)
- [`src/memopad/repository/postgres_search_repository.py`](src/memopad/repository/postgres_search_repository.py)

---

## Trade-offs to Consider

### Index Size vs. Features
- **N-gram tokenizer**: Enables substring matching but increases index size 3-5x
- **Trigrams were explicitly disabled** due to bloat (see issue #351)

### Performance vs. Accuracy
- **Spell correction**: Requires querying vocabulary table (slower)
- **Semantic search**: Requires embedding generation (much slower)

### Maintenance vs. UX
- **Autocomplete**: Needs caching to be fast
- **Analytics**: Needs storage and aggregation

---

## Recommended Priority

1. **Start with**: Result highlighting (high user value, medium effort)
2. **Then**: Column weights (better relevance ranking)
3. **Follow up**: Autocomplete (discoverability improvement)
4. **Consider**: Recency boosting (for active projects)
5. **Future**: Semantic search (when infrastructure ready)

---

## References

- [SQLite FTS5 Auxiliary Functions](https://www.sqlite.org/fts5.html#auxiliary_functions)
- [PostgreSQL Full Text Search](https://www.postgresql.org/docs/current/textsearch.html)
- [pg_trgm for fuzzy search](https://www.postgresql.org/docs/current/pgtrgm.html)
