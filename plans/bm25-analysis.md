# BM25 Analysis in MemoPad

## Current BM25 Implementation

**BM25 is already active** in MemoPad's SQLite search. It's the default ranking function used by FTS5.

### Where It's Used

**File**: [`src/memopad/repository/sqlite_search_repository.py`](src/memopad/repository/sqlite_search_repository.py:485)

```sql
SELECT
    search_index.project_id,
    search_index.id,
    search_index.title,
    search_index.permalink,
    search_index.file_path,
    search_index.type,
    search_index.metadata,
    search_index.from_id,
    search_index.to_id,
    search_index.relation_type,
    search_index.entity_id,
    search_index.content_snippet,
    search_index.category,
    search_index.created_at,
    search_index.updated_at,
    bm25(search_index) as score  -- <-- BM25 RANKING HERE
FROM {from_clause}
WHERE {where_clause}
ORDER BY score ASC  -- Lower score = more relevant
LIMIT :limit
OFFSET :offset
```

### How BM25 Works in FTS5

SQLite FTS5's `bm25()` function implements the **Okapi BM25** algorithm:

```
score = bm25(search_index, k1, b)

Where:
- k1 = 1.2 (term frequency saturation parameter)
- b = 0.75 (length normalization parameter)

Default formula:
score = IDF * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * (doc_length / avg_doc_length)))
```

**What this means**:
1. **IDF (Inverse Document Frequency)**: Rare terms get higher weight
2. **TF (Term Frequency)**: More occurrences = higher score (but saturates)
3. **Length Normalization**: Longer documents get slightly penalized

### Current Limitations

#### 1. No Column Weights

**Current**: All columns have equal weight (1.0)
```sql
bm25(search_index)  -- All columns: title, content_stems, etc. = weight 1.0
```

**Problem**: A match in the title is often more important than a match in the content, but they're treated equally.

#### 2. No Parameter Tuning

**Current**: Uses default k1=1.2, b=0.75
```sql
bm25(search_index)  -- Equivalent to bm25(search_index, 1.2, 0.75)
```

**Problem**: Default parameters may not be optimal for knowledge base search.

---

## How to Improve BM25

### Option 1: Column Weights (Recommended)

FTS5 supports column-specific weights as additional arguments to `bm25()`:

```sql
-- Syntax: bm25(table, w1, w2, w3, ...) where wN is weight for column N

-- Current table structure:
-- Column 0: title
-- Column 1: content_stems
-- Column 2: content_snippet
-- Column 3: permalink
-- ... (UNINDEXED columns don't count)

-- Improved query with title weighted 10x higher than content:
SELECT *, bm25(search_index, 10.0, 1.0, 0.0, 5.0) as weighted_score
FROM search_index
WHERE search_index MATCH 'keyword'
ORDER BY weighted_score
```

**Proposed weights**:
| Column | Weight | Reason |
|--------|--------|--------|
| title | 10.0 | Most important - usually contains key terms |
| content_stems | 1.0 | Baseline - main searchable content |
| content_snippet | 0.0 | Not for search (display only) |
| permalink | 5.0 | Important for path-based discovery |

**Implementation**:

```python
# In: src/memopad/repository/sqlite_search_repository.py

# Define column weights as class constant
COLUMN_WEIGHTS = [10.0, 1.0, 0.0, 5.0]  # title, content_stems, content_snippet, permalink

async def search(self, ...):
    # ... build query ...
    
    # Build bm25 with column weights
    weights_str = ', '.join(str(w) for w in self.COLUMN_WEIGHTS)
    
    sql = f"""
        SELECT
            ...,
            bm25(search_index, {weights_str}) as score
        FROM {from_clause}
        WHERE {where_clause}
        ORDER BY score ASC
        LIMIT :limit
        OFFSET :offset
    """
```

**Impact**: Title matches will rank significantly higher than content matches.

---

### Option 2: BM25 Parameter Tuning

Adjust k1 and b parameters for knowledge base search:

```sql
-- Default: bm25(search_index, 1.2, 0.75)
-- Tuned:   bm25(search_index, 2.0, 0.3)

-- k1 = 2.0: Less saturation (more weight to term frequency)
-- b = 0.3: Less length normalization (shorter docs don't get as much boost)
```

**Trade-offs**:
- Higher k1: Longer documents with many occurrences rank higher
- Lower b: Less penalty for long documents
- Optimal values depend on your content characteristics

---

### Option 3: Combine BM25 with Recency

Blend relevance score with recency for active projects:

```sql
SELECT 
    *,
    bm25(search_index, 10.0, 1.0, 0.0, 5.0) as relevance_score,
    -- Recency boost: newer documents get higher score
    -- Exponential decay: score * e^(-age_in_days / half_life)
    (
        bm25(search_index, 10.0, 1.0, 0.0, 5.0) * 
        (2.718281828 ^ (-julianday('now') - julianday(updated_at)) / 30.0))
    ) as final_score
FROM search_index
WHERE content_stems MATCH 'keyword'
ORDER BY final_score
```

**Half-life = 30 days**: Documents from 30 days ago get 50% relevance boost, 60 days = 25%, etc.

---

### Option 4: First-Occurrence Boost

BM25 treats all term occurrences equally. We could boost documents where the term appears early:

```sql
-- Complex query using auxiliary functions
-- Would need custom SQLite function or post-processing
```

This is harder to implement in pure SQL and may require application-level sorting.

---

## PostgreSQL Parity

PostgreSQL uses `ts_rank()` instead of BM25:

```sql
-- Current Postgres implementation
SELECT 
    *,
    ts_rank(search_index.textsearchable_index_col, to_tsquery('english', :text)) as score
FROM search_index
WHERE ...
ORDER BY score DESC  -- Note: Postgres uses DESC (higher = better)
```

**ts_rank() with weights**:
```sql
-- ts_rank(weights, vector, query, normalization)
-- weights: {D, C, B, A} for title, content_stems, etc.
SELECT ts_rank('{0.1, 0.2, 0.4, 1.0}', textsearchable_index_col, query, 32)
```

**Note**: Weights are in reverse order in PostgreSQL: D=0.1, C=0.2, B=0.4, A=1.0

---

## Implementation Recommendation

### Phase 1: Add Column Weights (SQLite Only)

```python
# src/memopad/repository/sqlite_search_repository.py

class SQLiteSearchRepository(SearchRepositoryBase):
    """SQLite FTS5 implementation with weighted BM25 ranking."""
    
    # Column order: title, content_stems, content_snippet, permalink
    BM25_COLUMN_WEIGHTS = [10.0, 1.0, 0.0, 5.0]
    
    async def search(self, ...):
        # ... build conditions and params ...
        
        weights_sql = ', '.join(str(w) for w in self.BM25_COLUMN_WEIGHTS)
        
        sql = f"""
            SELECT
                search_index.project_id,
                search_index.id,
                search_index.title,
                search_index.permalink,
                search_index.file_path,
                search_index.type,
                search_index.metadata,
                search_index.from_id,
                search_index.to_id,
                search_index.relation_type,
                search_index.entity_id,
                search_index.content_snippet,
                search_index.category,
                search_index.created_at,
                search_index.updated_at,
                bm25(search_index, {weights_sql}) as score
            FROM {from_clause}
            WHERE {where_clause}
            ORDER BY score ASC
            LIMIT :limit
            OFFSET :offset
        """
        # ... execute ...
```

### Phase 2: Add ts_rank() Weights (PostgreSQL)

```python
# src/memopad/repository/postgres_search_repository.py

class PostgresSearchRepository(SearchRepositoryBase):
    """PostgreSQL implementation with weighted ts_rank()."""
    
    # Weights for ts_rank: {D, C, B, A}
    # Our columns: content_snippet(D), permalink(C), content_stems(B), title(A)
    TS_RANK_WEIGHTS = '{0.1, 0.5, 1.0, 10.0}'
    
    async def search(self, ...):
        # ... 
        
        score_expr = f"""
            ts_rank(
                '{self.TS_RANK_WEIGHTS}', 
                search_index.textsearchable_index_col, 
                to_tsquery('english', :text),
                32  -- normalization: divide by document length
            )
        """
        
        sql = f"""
            SELECT
                ...,
                {score_expr} as score
            FROM {from_clause}
            WHERE {where_clause}
            ORDER BY score DESC
            LIMIT :limit
            OFFSET :offset
        """
```

---

## Testing the Improvement

Create a test to verify column weights work:

```python
# tests/repository/test_search_repository_column_weights.py

@pytest.mark.asyncio
async def test_title_matches_rank_higher_than_content(search_repository, session_maker):
    """Test that title matches get higher relevance than content matches."""
    # Create entity with "python" in title
    entity_title = await create_entity(
        session_maker,
        title="Python Programming Guide",
        content="This is a guide about coding"
    )
    
    # Create entity with "python" only in content
    entity_content = await create_entity(
        session_maker,
        title="Programming Guide",
        content="This is about python programming"
    )
    
    # Index both
    await index_entity(search_repository, entity_title)
    await index_entity(search_repository, entity_content)
    
    # Search for "python"
    results = await search_repository.search(search_text="python")
    
    # Title match should rank higher (lower bm25 score)
    assert results[0].title == "Python Programming Guide"
    assert results[0].score < results[1].score  # Lower = more relevant
```

---

## Summary

| Aspect | Current | With Weights |
|--------|---------|--------------|
| Title weight | 1.0 | 10.0 |
| Content weight | 1.0 | 1.0 |
| Permalink weight | 1.0 | 5.0 |
| Search quality | Good | Better |
| Implementation effort | None | Low |

**Recommendation**: Add column weights to both SQLite (bm25) and PostgreSQL (ts_rank) implementations for better result relevance.
