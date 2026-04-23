# Regulation Executor -- Tool Specifications

## Tool: embed_and_search

| Property | Value |
|----------|-------|
| Decorator | `@regulation_executor.tool` |
| Retries | 1 |
| Timeout | 10s |
| Prepare | none |
| Returns | `str` (JSON array of candidate dicts) |

**Purpose**: Generate a 768-dim embedding for the query using `gemini-embedding-001`, then run vector similarity searches against three tables: `articles` (top 20), `sections` (top 10), and `regulations` (top 5). Returns combined candidates with similarity scores.

**Parameters**:

| Name | Type | Default | Description |
|------|------|---------|-------------|
| ctx | RunContext[RegulationSearchDeps] | -- | Injected by framework |
| query | str | -- | Arabic search query to embed and search |

**Implementation**:

```
1. embedding = await ctx.deps.embedding_fn(query)   # 768-dim vector
2. Run 3 Supabase RPC calls in parallel (asyncio.gather):
   a. articles: SELECT id, chunk_ref, title, article_num, identifier_number,
                LEFT(content, 500) as content_preview,
                regulation_id,
                1 - (embedding <=> query_embedding) as similarity
      FROM articles
      WHERE embedding IS NOT NULL
      ORDER BY embedding <=> query_embedding
      LIMIT 20;

   b. sections: SELECT id, chunk_ref, title,
                LEFT(content, 500) as content_preview,
                regulation_id,
                1 - (embedding <=> query_embedding) as similarity
      FROM sections
      WHERE embedding IS NOT NULL
      ORDER BY embedding <=> query_embedding
      LIMIT 10;

   c. regulations: SELECT id, regulation_ref, title, type,
                   main_category, authority_level, authority_score,
                   LEFT(regulation_summary, 300) as summary_preview,
                   1 - (embedding <=> query_embedding) as similarity
      FROM regulations
      WHERE embedding IS NOT NULL
      ORDER BY embedding <=> query_embedding
      LIMIT 5;
3. Merge all results into a single candidate list with source_table tag
4. Sort by similarity DESC
5. Return JSON string of candidates
```

**Return Value**: JSON string containing an array of candidate objects:
```json
[
  {
    "source_table": "articles",
    "id": "uuid",
    "chunk_ref": "17573_reg_264_article_01",
    "title": "تعريفات",
    "article_num": 1,
    "content_preview": "يُقصد بالألفاظ والعبارات الآتية...",
    "regulation_id": "uuid",
    "similarity": 0.82
  },
  ...
]
```

**Error Handling**:
- Embedding API failure: Raise `ModelRetry("فشل إنشاء تضمين الاستعلام. أعد المحاولة.")` to let the framework retry once
- DB query failure: Return `"[]"` (empty array) -- the agent will see zero results and may call `text_search_fallback`
- Partial failure (one table fails): Return results from successful tables only, log warning

**SQL Note**: The queries use raw SQL via `supabase.rpc()` or `supabase.postgrest.rpc()` because Supabase's Python client does not natively support pgvector operators. The implementation should use a Supabase RPC function or raw SQL execution. The HNSW indexes (`idx_art_embedding`, `idx_sec_embedding`, `idx_reg_embedding`) with `vector_cosine_ops` will be used automatically.

---

## Tool: text_search_fallback

| Property | Value |
|----------|-------|
| Decorator | `@regulation_executor.tool` |
| Retries | 0 |
| Timeout | 5s |
| Prepare | none |
| Returns | `str` (JSON array of candidate dicts) |

**Purpose**: Fallback text search using PostgreSQL `ILIKE` with `pg_trgm` for fuzzy matching. Called when vector search returns weak results (fewer than 3 results or low similarity scores).

**Parameters**:

| Name | Type | Default | Description |
|------|------|---------|-------------|
| ctx | RunContext[RegulationSearchDeps] | -- | Injected by framework |
| query | str | -- | Arabic search query for text matching |

**Implementation**:

```
1. Extract key terms from query (split on spaces, filter stopwords)
2. Build ILIKE patterns for each key term
3. Search articles.content and articles.title using ILIKE:
   SELECT id, chunk_ref, title, article_num, identifier_number,
          LEFT(content, 500) as content_preview,
          regulation_id,
          0.0 as similarity  -- text matches get 0.0 similarity (reranker will score them)
   FROM articles
   WHERE content ILIKE '%term1%'
      OR content ILIKE '%term2%'
      OR title ILIKE '%term1%'
   LIMIT 15;
4. Also search regulations.title:
   SELECT id, regulation_ref, title, type,
          main_category, authority_level, authority_score,
          LEFT(regulation_summary, 300) as summary_preview,
          0.0 as similarity
   FROM regulations
   WHERE title ILIKE '%term1%'
      OR title ILIKE '%term2%'
   LIMIT 5;
5. Return JSON string of candidates (same format as embed_and_search)
```

**Return Value**: Same JSON format as `embed_and_search`. The `similarity` field is set to `0.0` for text matches -- the reranker will assign proper relevance scores.

**Error Handling**:
- DB error: Return `"[]"` -- agent proceeds with whatever vector search found
- No results: Return `"[]"` -- agent knows both search methods failed

**Note on pg_trgm**: The `pg_trgm` extension is installed (verified in DB state). However, there are currently no GIN/GiST trigram indexes on `articles.content` or `articles.title`. The ILIKE queries will work but may be slow on 8,987 articles. A migration to add `CREATE INDEX idx_art_content_trgm ON articles USING gin (content gin_trgm_ops)` is recommended but not required for initial implementation.

---

## Tool: rerank_results

| Property | Value |
|----------|-------|
| Decorator | `@regulation_executor.tool` |
| Retries | 1 |
| Timeout | 8s |
| Prepare | none |
| Returns | `str` (JSON array of reranked candidates with scores) |

**Purpose**: Rerank combined candidates using the Jina Reranker API. Takes the query and a list of candidate IDs, sends their text content to Jina for cross-encoder scoring, returns results sorted by reranker relevance score.

**Parameters**:

| Name | Type | Default | Description |
|------|------|---------|-------------|
| ctx | RunContext[RegulationSearchDeps] | -- | Injected by framework |
| query | str | -- | Original Arabic search query |
| candidate_ids | list[str] | -- | List of candidate IDs from embed_and_search and/or text_search_fallback |

**Implementation**:

```
1. Deduplicate candidate_ids
2. Look up candidate texts from the in-memory candidate cache
   (stored on deps._candidate_cache by embed_and_search / text_search_fallback)
3. Build Jina reranker request:
   POST https://api.jina.ai/v1/rerank
   Headers: Authorization: Bearer {ctx.deps.jina_api_key}
   Body: {
     "model": "jina-reranker-v2-base-multilingual",
     "query": query,
     "documents": [candidate.content_preview for each candidate],
     "top_n": 10
   }
4. Parse response: extract index + relevance_score for each result
5. Map back to candidate objects, add reranker_score field
6. Sort by reranker_score DESC
7. Return JSON string of reranked candidates
```

**Return Value**: JSON string containing reranked candidates:
```json
[
  {
    "source_table": "articles",
    "id": "uuid",
    "chunk_ref": "17573_reg_264_article_01",
    "title": "تعريفات",
    "article_num": 1,
    "content_preview": "...",
    "regulation_id": "uuid",
    "similarity": 0.82,
    "reranker_score": 0.94
  },
  ...
]
```

**Error Handling**:
- Jina API timeout/error: Fall back to similarity-only ranking. Return candidates sorted by their original `similarity` score. Log warning. Do NOT raise -- degraded results are better than no results.
- Empty candidate list: Return `"[]"` -- agent knows no results were found.

**Candidate Cache Pattern**: The `embed_and_search` and `text_search_fallback` tools store their results in `deps._candidate_cache: dict[str, dict]` keyed by candidate ID. This avoids re-querying the DB to look up candidate text for reranking. The `rerank_results` tool reads from this cache.

---

## Tool: unfold_context

| Property | Value |
|----------|-------|
| Decorator | `@regulation_executor.tool` |
| Retries | 1 |
| Timeout | 5s |
| Prepare | none |
| Returns | `str` (JSON array of enriched result dicts) |

**Purpose**: For the top reranked results, load full article content (not just preview), parent regulation metadata, and neighboring articles for context. Applies truncation to stay within token budget.

**Parameters**:

| Name | Type | Default | Description |
|------|------|---------|-------------|
| ctx | RunContext[RegulationSearchDeps] | -- | Injected by framework |
| top_result_ids | list[str] | -- | IDs of top reranked results to unfold (max 10) |

**Implementation**:

```
1. For each result ID (up to 10):
   a. If source_table == "articles":
      - Load full content: SELECT content, article_context FROM articles WHERE id = ?
      - Load parent regulation: SELECT title, type, authority_level, authority_score,
                                       main_category, regulation_ref
                                FROM regulations WHERE id = article.regulation_id
      - Load sibling articles (same section, +/- 2 article_num):
        SELECT title, article_num, LEFT(content, 500) as content_preview
        FROM articles
        WHERE section_id = article.section_id
          AND article_num BETWEEN (article.article_num - 2) AND (article.article_num + 2)
          AND id != article.id
        ORDER BY article_num
        LIMIT 4

   b. If source_table == "sections":
      - Load full content: SELECT content, section_summary FROM sections WHERE id = ?
      - Load parent regulation (same as above)
      - Load child articles:
        SELECT title, article_num, LEFT(content, 500) as content_preview
        FROM articles
        WHERE section_id = section.id
        ORDER BY article_num
        LIMIT 5

   c. If source_table == "regulations":
      - Load summary: SELECT regulation_summary, content_md FROM regulations WHERE id = ?
      - Truncate content_md to 3000 chars

2. Apply per-result truncation:
   - content: max 3,000 chars
   - article_context / section_summary: max 500 chars
   - sibling/child articles: max 1,500 chars total
   - regulation metadata: max 300 chars

3. Accumulate results, tracking total chars
4. Stop when total budget (40,000 chars) is reached
5. Return JSON string of enriched results
```

**Return Value**: JSON string containing enriched results:
```json
[
  {
    "source_table": "articles",
    "chunk_ref": "17573_reg_264_article_01",
    "title": "تعريفات",
    "article_num": 1,
    "content": "Full article text (up to 3000 chars)...",
    "article_context": "AI-generated context summary...",
    "reranker_score": 0.94,
    "regulation": {
      "title": "نظام الرهن التجاري",
      "type": "نظام",
      "authority_level": "binding_law",
      "authority_score": 9,
      "regulation_ref": "17573_reg_264"
    },
    "siblings": [
      {"title": "المادة 2", "article_num": 2, "content_preview": "..."},
      {"title": "المادة 3", "article_num": 3, "content_preview": "..."}
    ]
  },
  ...
]
```

**Error Handling**:
- DB lookup failure for a single result: Skip that result, continue with others. Log warning.
- All lookups fail: Return `"[]"` -- agent returns weak quality assessment.
- Budget exceeded: Return accumulated results with note about omitted results.

---

## Toolset Membership

All 4 tools are registered directly on `regulation_executor`. No shared toolsets.

No prepare functions -- all tools are always visible. The system prompt prescribes the exact workflow order.

## Tool Interaction Pattern

The tools share state via `RegulationSearchDeps._candidate_cache`:

```
embed_and_search ──writes──> deps._candidate_cache
                                    |
text_search_fallback ──writes──>    |  (appends)
                                    |
rerank_results ──reads──────────────┘
                     |
                     └──writes──> deps._reranked_results
                                         |
unfold_context ──reads───────────────────┘
```

This avoids redundant DB queries and ensures the reranker operates on the same candidate data that the search tools found.
