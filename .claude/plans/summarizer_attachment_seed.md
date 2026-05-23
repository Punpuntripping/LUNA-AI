# Plan — Summarizer Attachment Flow (first version)

> Separate plan referenced by `ocr_extraction_agent.md`. Implemented by its own
> agent, alongside the OCR work. This is the **first version** ("as beginning").

## Goal

The `artifact_summarizer` today has one flow: an agent-facing coverage summary for
agent-generated artifacts (`agent_search` / `agent_writing`). Add a **second flow**
for `kind='attachment'` items (OCR'd documents). For an attachment it produces:

- A **title** — meaningful, grounded in what the document actually contains
  (replaces / improves on the raw filename).
- A **summary** — what the document contains, **and how it relates to the user's
  context / the conversation context.**

So the attachment summary is context-aware: it doesn't just describe the file in
isolation, it connects it to what the conversation is about.

## Hard constraint — same settings, only the prompt differs

The summarizer agent uses **identical settings for every flow** — same model
(tier), same `model_settings`, same `retries`, same usage limits. The **only**
difference between the generic flow and the attachment flow is the **system
prompt**. Do not fork the agent's configuration; fork only the prompt.

## The seed / branch point

`ocr_extraction_agent.md` creates `agents/memory/summarize.py`:

```python
async def summarize_workspace_item(supabase, item_id, *, force=False) -> bool
```

It loads the row and knows the item's `kind`. That function is the **single
branch point**: `kind == 'attachment'` → attachment prompt + context; everything
else → existing prompt. Both callers (the inline OCR path and the
`POST /internal/summarize-workspace-item` webhook) go through it — no orchestrator
or webhook changes needed.

## Changes

### `agents/memory/artifact_summarizer/prompts.py`
- Add `SYSTEM_PROMPT_ATTACHMENT_AR` — Arabic system prompt for the attachment
  flow. It instructs the model to (1) write a grounded title, (2) write a summary
  of the document, (3) explain how the document relates to the user's / the
  conversation's context.
- Add a `build_attachment_user_message(...)` renderer that packs: the OCR
  `content_md`, the filename, and the **conversation context** (recent messages
  and/or the latest `convo_context` summary).

### `agents/memory/artifact_summarizer/agent.py`
- A way to build the agent with the attachment prompt **without changing any
  other setting** — e.g. a `kind`-parameterised factory that swaps only
  `instructions`/the system prompt. Model, `model_settings`, `retries`, output
  handling stay identical.

### `agents/memory/artifact_summarizer/models.py`
- An attachment output carrying `title` + `summary_md` (+ optional context-link
  text). Output type is not a "model setting" — it may differ per flow.

### `agents/memory/summarize.py` (the branch)
- On `kind == 'attachment'`: load conversation context, run the attachment flow,
  persist **`title`** and **`summary`** onto the `workspace_items` row.
- Else: existing generic flow (persist `summary` only).

### Persistence
- The attachment flow also updates `workspace_items.title` (the generic flow does
  not). `summary` / `summary_source_length` / `summary_updated_at` as today.
- Recorded as `agent_runs` (`agent_family='memory'`, subtype e.g.
  `summarize_attachment`).

## Open questions for later iterations
- How much conversation context to feed (recent N messages vs the compaction
  summary) — start small.
- Whether extracted case identifiers (case number, parties) should also flow into
  `case_memories` for case-scoped conversations — deferred.

## Dependency
Build after `agents/memory/summarize.py` exists (from `ocr_extraction_agent.md`).
