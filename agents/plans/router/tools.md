# Router Agent — Tool Specifications

## Tool: get_artifact

| Property | Value |
|----------|-------|
| Decorator | `@router_agent.tool` |
| Retries | 1 |
| Timeout | 5s |
| Prepare | none |
| Returns | `str` (artifact content or error message) |

**Purpose**: Read-only access to a previously created artifact (report, contract, summary, etc.) by its artifact_id. Used when the user asks questions about a previous artifact's content.

**Parameters**:
| Name | Type | Default | Description |
|------|------|---------|-------------|
| ctx | RunContext[RouterDeps] | -- | Injected by framework |
| artifact_id | str | -- | UUID of the artifact to load |

**Return Value**: The full markdown content (`content_md`) of the artifact, prefixed with the artifact title. If not found, returns an Arabic error message. If a DB error occurs, returns an Arabic error message.

**Example return**:
```
# تقرير: أحكام الفصل التعسفي

## الإطار النظامي
...
```

**Error Handling**:
- Not found: Return `"لم يُعثر على المستند المطلوب."` -- router handles gracefully, may ask user to clarify
- DB error: Return `"حدث خطأ أثناء تحميل المستند. يرجى المحاولة مرة أخرى."` -- router informs user

**Implementation**:

```python
@router_agent.tool
async def get_artifact(ctx: RunContext[RouterDeps], artifact_id: str) -> str:
    """Read a previous artifact (report, contract, summary) by its ID.

    Use this when the user asks about the content of a previous report,
    contract, or other document. Returns the full markdown content.

    Args:
        artifact_id: The UUID of the artifact to retrieve.
    """
    try:
        result = (
            ctx.deps.supabase.table("artifacts")
            .select("title, content_md")
            .eq("artifact_id", artifact_id)
            .eq("user_id", ctx.deps.user_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
        if result and result.data:
            title = result.data.get("title", "")
            content = result.data.get("content_md", "")
            return f"# {title}\n\n{content}" if title else content
        return "لم يُعثر على المستند المطلوب."
    except Exception as e:
        logger.warning("Error loading artifact %s: %s", artifact_id, e)
        return "حدث خطأ أثناء تحميل المستند. يرجى المحاولة مرة أخرى."
```

**Security Note**: The query filters by `user_id` to ensure the user can only access their own artifacts. The `deleted_at` check respects soft deletes.

---

## Toolset Membership

The router has exactly ONE tool: `get_artifact`. This is a deliberate design decision:

- **Ask = router** (via get_artifact) -- user asks about an artifact's content, router reads and answers
- **Modify = task** -- user wants to edit/extend an artifact, router opens a new task with artifact_id

No prepare functions -- the tool is always visible. The router's instructions guide when to use it.

No shared toolsets -- this tool is specific to the router agent.

---

## Tool Usage Patterns

### Pattern 1: User asks about a specific artifact

User: "ماذا قال التقرير عن المادة 80؟"

The conversation history contains a task summary like:
```
[TASK COMPLETED -- deep_search]
بحث الفصل التعسفي: 3 مواد، 2 حكم
Artifact: abc-123-def
```

Router extracts `artifact_id` from the summary, calls `get_artifact("abc-123-def")`, reads content, answers the user's question directly via ChatResponse.

### Pattern 2: User references an artifact ambiguously

User: "ما محتوى التقرير السابق؟"

If there are multiple artifacts referenced in the conversation history, the router should ask the user which one they mean via ChatResponse, NOT call get_artifact blindly.

### Pattern 3: User wants to edit an artifact

User: "عدّل التقرير السابق وأضف المادة 81"

Router does NOT call get_artifact. Instead, returns OpenTask with the appropriate task_type and artifact_id set, so the task agent loads and edits the artifact.
