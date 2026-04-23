# Deep Search Planner — Tool Specifications

## Tool: search_regulations

| Property | Value |
|----------|-------|
| Decorator | `@planner_agent.tool` |
| Retries | 1 |
| Timeout | 30s |
| Prepare | none |
| Returns | `str` (markdown summary) |

**Purpose**: Search Saudi statutory and regulatory law via a delegation executor agent.

**Parameters**:
| Name | Type | Default | Description |
|------|------|---------|-------------|
| ctx | RunContext[SearchDeps] | — | Injected by framework |
| query | str | — | Arabic search query for regulations |

**Return Value**: Markdown summary of findings — articles, sections, regulations found, with quality self-assessment. Example:
```
## نتائج البحث في الأنظمة
**الجودة: قوية**
### المادة 77 — إنهاء العقد بتعويض
> نص المادة: يحق لأي من طرفي العقد...
```

**Error Handling**:
- Timeout: Return `"خطأ: انتهت مهلة البحث. جرب صياغة مختلفة للاستعلام."` (ModelRetry not needed — planner decides whether to retry)
- Executor error: Return error message string, let planner decide next step

**Implementation Note**: Currently returns mock markdown. When real executors are built, this tool will call `regulation_executor.run(query, deps=ctx.deps)` and return `result.output`.

---

## Tool: search_cases_courts

| Property | Value |
|----------|-------|
| Decorator | `@planner_agent.tool` |
| Retries | 1 |
| Timeout | 30s |
| Prepare | none |
| Returns | `str` (markdown summary) |

**Purpose**: Search Saudi judicial precedents and court rulings.

**Parameters**:
| Name | Type | Default | Description |
|------|------|---------|-------------|
| ctx | RunContext[SearchDeps] | — | Injected by framework |
| query | str | — | Arabic search query for court cases |

**Return Value**: Markdown summary of cases found — court level, dates, referenced regulations, precedent patterns.

**Error Handling**: Same as `search_regulations`.

---

## Tool: search_compliance

| Property | Value |
|----------|-------|
| Decorator | `@planner_agent.tool` |
| Retries | 1 |
| Timeout | 30s |
| Prepare | none |
| Returns | `str` (markdown summary) |

**Purpose**: Search government services and compliance procedures.

**Parameters**:
| Name | Type | Default | Description |
|------|------|---------|-------------|
| ctx | RunContext[SearchDeps] | — | Injected by framework |
| query | str | — | Arabic search query for services/compliance |

**Return Value**: Markdown summary of government services and entities found.

**Error Handling**: Same as `search_regulations`.

---

## Tool: ask_user

| Property | Value |
|----------|-------|
| Decorator | `@planner_agent.tool` |
| Retries | 0 |
| Timeout | none (waits for user) |
| Prepare | none |
| Returns | `str` (user's reply) |

**Purpose**: Ask the user a clarifying question before or during search. Pauses agent execution until user responds.

**Parameters**:
| Name | Type | Default | Description |
|------|------|---------|-------------|
| ctx | RunContext[SearchDeps] | — | Injected by framework |
| question | str | — | Arabic clarifying question |

**Return Value**: The user's reply text.

**Implementation Note**: This tool is special — it bridges async agent execution with real-time user interaction. Recommended approach (Option A from design doc): The tool itself is an async function that awaits user input (via WebSocket message or Redis pub/sub event). The `CallToolsNode` blocks until all tool calls complete, so `ask_user` naturally pauses everything.

The orchestrator also intercepts this tool call during the `agent.iter()` loop to emit SSE events:
1. Emits SSE `ask_user` event with the question
2. Tool awaits user reply (via WebSocket or Redis pub/sub)
3. User reply becomes the tool return value
4. Agent resumes `.next()`

---

## Tool: respond_to_user

| Property | Value |
|----------|-------|
| Decorator | `@planner_agent.tool` |
| Retries | 0 |
| Timeout | 5s |
| Prepare | none |
| Returns | `None` |

**Purpose**: Send a mid-search status update to the user. Fire-and-forget — does not pause the agent.

**Parameters**:
| Name | Type | Default | Description |
|------|------|---------|-------------|
| ctx | RunContext[SearchDeps] | — | Injected by framework |
| message | str | — | Arabic status message |

**Return Value**: `None` (fire-and-forget).

**Implementation Note**: Orchestrator intercepts during `agent.iter()` loop, emits SSE `status` event with the message text. The tool function itself emits the SSE event and returns.

---

## Tool: create_report

| Property | Value |
|----------|-------|
| Decorator | `@planner_agent.tool` |
| Retries | 1 |
| Timeout | 10s |
| Prepare | none |
| Returns | `str` (artifact_id) |

**Purpose**: Create or update a markdown research report artifact in the DB.

**Parameters**:
| Name | Type | Default | Description |
|------|------|---------|-------------|
| ctx | RunContext[SearchDeps] | — | Injected by framework |
| title | str | — | Arabic report title |
| content_md | str | — | Full markdown report content |
| citations | list[Citation] | — | Structured citation list for the report |

**Return Value**: The `artifact_id` (UUID string) of the created/updated artifact.

**Implementation Note**: Orchestrator intercepts, calls `create_agent_artifact()` or updates existing artifact via Supabase. Emits `artifact_created` or `artifact_updated` SSE event.

The `citations` parameter carries structured `Citation` objects alongside the markdown content. The `content_md` includes human-readable references in its References section, while the `citations` list provides machine-readable structured data for future frontend features (click-through linking, source highlighting, citation counts).

---

## Tool: get_previous_report

| Property | Value |
|----------|-------|
| Decorator | `@planner_agent.tool` |
| Retries | 1 |
| Timeout | 5s |
| Prepare | none |
| Returns | `str` (markdown content) |

**Purpose**: Load a previously created research report by artifact_id for editing.

**Parameters**:
| Name | Type | Default | Description |
|------|------|---------|-------------|
| ctx | RunContext[SearchDeps] | — | Injected by framework |
| artifact_id | str | — | UUID of the artifact to load |

**Return Value**: Full markdown content of the artifact.

**Error Handling**:
- Not found: Return `"لم يُعثر على التقرير المطلوب."` — planner proceeds without it
- DB error: Return error message string

---

## Toolset Membership

All 7 tools are registered directly on `planner_agent`. No shared toolsets — these tools are specific to the deep search planner.

No prepare functions — all tools are always visible. The planner's instructions guide when to use each.
