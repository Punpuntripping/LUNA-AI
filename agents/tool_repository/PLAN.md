# Markdown Section Editor Tool — Design Plan

A Pydantic AI tool that **deletes one section** of a markdown file and **adds another** — reliably, even when the LLM caller is imprecise about whitespace or duplicate headings.

> Scope: section-level edits on markdown (`.md`) files. Not a general-purpose code editor.

---

## 1. The Core Problem

A section-editing tool must answer two questions reliably:

1. **Which section do we delete?** (targeting)
2. **Where exactly does the new section go?** (anchoring)

Naive implementations fail because:

- Headings repeat (`## Notes` appears 3 times)
- Whitespace drifts (`## Old Notes ` vs `## Old Notes`)
- `#` appears inside fenced code blocks and gets mistaken for a heading
- Custom anchor syntax (`## Old Notes {#old-notes}`) breaks naive equality
- HTML blocks contain headings the parser ignores

---

## 2. Targeting Strategy Options

Ordered weakest → strongest, with what each is good for.

### Option A — First-match line scan
```python
for i, line in enumerate(lines):
    if line.strip() == heading.strip():
        return i
```
- **Reliability:** Low
- **Pros:** trivial code, no dependencies
- **Cons:** silent wrong-section edits when heading repeats; fooled by `#` in code fences
- **Verdict:** rejected. This is what my v1 draft did and it's the failure mode every research source warns about.

### Option B — Exact match + uniqueness enforcement (Claude Code model)
```python
matches = [i for i, l in enumerate(lines) if l.strip() == heading.strip()]
if len(matches) != 1:
    raise AmbiguousMatch(f"{len(matches)} matches for '{heading}'")
```
- **Reliability:** Medium-high
- **Pros:** simple, fails loudly, forces caller to provide a unique identifier
- **Cons:** still fooled by code fences; whitespace-fragile
- **Verdict:** good fallback. This is exactly what Claude Code's `Edit` tool does.

### Option C — Fuzzy match ladder (Aider model)
1. Try exact match
2. Try whitespace-normalized match
3. Try regex with flexible whitespace between words
- **Reliability:** High in practice
- **Pros:** tolerates "almost correct" LLM output (very common)
- **Cons:** more complex error messages; can mask real bugs
- **Verdict:** worth adopting as a fallback **after** uniqueness check fails on exact match.

### Option D — Markdown AST + heading path (RECOMMENDED)
Parse with `markdown-it-py` → walk the token stream → identify `heading_open` tokens with their level and inline content → compute section span from heading to next same-or-higher level heading.
- **Reliability:** High
- **Pros:** ignores `#` inside code fences automatically; understands heading levels; survives whitespace; can target by heading path (`Guide > Setup > Windows`)
- **Cons:** requires `markdown-it-py` dependency; needs token→source-offset mapping
- **Verdict:** **primary strategy.**

### Option E — Stable slug anchors
Use `markdown-it-py` slug rules (e.g. `## Old Notes` → `old-notes`) as the unique key. Caller passes `slug="old-notes"` instead of heading text.
- **Reliability:** Highest
- **Pros:** survives any whitespace/punctuation change; round-trippable; matches GitHub/MkDocs anchor convention
- **Cons:** caller must know the slug; needs collision handling for duplicate slugs
- **Verdict:** **expose as optional `target_slug` parameter alongside heading text.**

---

## 3. Recommended Architecture

```
target = slug | heading_path | heading_text  (caller picks; tool validates uniqueness)
        ↓
markdown-it-py AST
        ↓
locate heading_open token + section span (until next ≤-level heading)
        ↓
splice source by character offset (not line index)
        ↓
write back, return diff summary
```

### Tool signature
```python
class EditMarkdownSection(BaseModel):
    file_path: str
    # Targeting — exactly one must be provided:
    target_slug: str | None = None              # strongest: "old-notes"
    target_path: list[str] | None = None        # strong: ["Guide", "Setup", "Windows"]
    target_heading: str | None = None           # weakest: "## Old Notes"
    # New section:
    new_heading: str
    new_body: str
    # Placement of new section:
    insert_after_slug: str | None = None        # if None → append at end
    # Safety:
    dry_run: bool = False                       # return diff without writing
```

### Return value
Always return a structured result, never silently succeed/fail:
```python
class EditResult(BaseModel):
    success: bool
    deleted_span: tuple[int, int] | None        # char offsets
    inserted_at: int | None
    diff: str                                   # unified diff
    error: str | None                           # populated when not unique / not found
```

---

## 4. Best Practices (drawn from research)

### From Claude Code's `Edit` tool
- **Fail loudly on ambiguity.** Never silently pick the first of N matches.
- **Force the caller to provide a unique target.** Either uniqueness must be inherent (slug) or the caller must add disambiguating context.
- **Read-before-edit invariant.** Tool should refuse to edit a file it hasn't read in this session (or hash-check the file).

### From Aider
- **Fault-tolerant matching ladder.** Real LLMs produce whitespace-drifted output ~30% of the time. Have a fallback ladder; don't punish near-misses.
- **Edit format dominates accuracy.** GPT-4 Turbo: 20% on SEARCH/REPLACE → 61% on unified diffs. Translation for us: prefer a format the model finds easy to emit correctly — short slugs beat long heading strings.
- **Helpful error messages.** When a match fails, return *what the tool actually saw* (closest match + Levenshtein distance), not just "not found".

### From AST-based code agents (CODESTRUCT)
- **Operate on named units, not text spans.** Pass@1 up 1.2–5.0%, tokens down 12–38%.
- **Avoid false positives from strings/comments.** AST awareness is the only reliable way; in markdown that means parsing fences correctly.

### From markdown-it ecosystem
- `markdown-it-anchor` skips headings inside HTML blocks — document this limitation.
- Slug generation needs a deterministic, collision-handled rule (GitHub-style: lowercase, replace non-alphanumeric with `-`, append `-1`, `-2` on collision).
- TOC generation is a useful free byproduct — expose `list_sections()` as a sibling tool so the LLM can introspect before editing.

### General LLM-tool ergonomics
- **Dry-run mode.** Let the agent preview the diff before committing.
- **Idempotency hint.** If the new section already exists with identical content, return success without writing.
- **Atomic write.** Write to a temp file, then rename. Prevents corruption on crash.
- **Return a diff, not just "ok".** The calling agent can show the user what changed without re-reading the file.

---

## 5. Failure Modes & Mitigations

| Failure | Mitigation |
|---|---|
| Heading appears twice | Refuse; surface both locations in error |
| Heading inside code fence | AST parser ignores; line-scan fallback skips fences |
| Heading inside HTML block | Document limitation; opt-in `--include-html` flag later |
| Trailing whitespace / anchor suffix | Whitespace-normalized fallback; slug-based targeting bypasses |
| File changed since last read | Compare mtime or hash; refuse stale edits |
| New section heading collides with existing | Refuse with clear error; suggest `replace_section` instead |
| Concurrent writer | Atomic rename + advisory lock |
| Cross-platform line endings (CRLF/LF) | Detect on read, preserve on write |

---

## 6. Out of Scope (for v1)

- Editing tables / lists / code blocks below section granularity
- Cross-file edits (e.g. moving a section between files)
- Frontmatter editing (YAML/TOML)
- Live preview / round-trip rendering
- Conflict resolution across concurrent edits

These belong in v2 as separate tools.

---

## 7. Implementation Sketch (recommended path)

```python
# agents/tool_repository/markdown_editor.py
from markdown_it import MarkdownIt
from pydantic import BaseModel
from pydantic_ai import Agent

md = MarkdownIt()

def slug_of(heading_text: str) -> str:
    import re
    s = re.sub(r"[^\w\s-]", "", heading_text.lower()).strip()
    return re.sub(r"[\s_]+", "-", s)

def locate_section(source: str, *, slug=None, heading=None) -> tuple[int, int]:
    tokens = md.parse(source)
    # 1. find heading_open whose inline content slug matches `slug` (or whose
    #    raw text matches `heading` after uniqueness check)
    # 2. walk forward until next heading_open with level <= current level
    # 3. return (start_char_offset, end_char_offset) using token.map → line → offset
    ...
```

---

## 8. Decision Summary

**Primary strategy:** Markdown AST parsing (`markdown-it-py`) + slug-first targeting.
**Fallback chain:** slug → heading path → exact heading match (with uniqueness check) → whitespace-normalized match.
**Error policy:** fail loudly with the closest near-match included in the error.
**Output:** structured `EditResult` with a unified diff.

This combines the **uniqueness discipline** of Claude Code's Edit tool, the **fault tolerance** of Aider's match ladder, and the **structural awareness** of AST-based code agents — applied to markdown's specific quirks.

---

## 9. Sources

- Claude Code `Edit` tool reference — https://code.claude.com/docs/en/tools-reference
- Claude Code Edit silent-fail bug — https://github.com/anthropics/claude-code/issues/52241
- Aider edit formats — https://aider.chat/docs/more/edit-formats.html
- Aider unified diffs (3× improvement over SEARCH/REPLACE) — https://aider.chat/docs/unified-diffs.html
- Aider Search/Replace logic deep dive — https://deepwiki.com/Aider-AI/aider/3.2-prompt-engineering-and-templates
- Sumit Gouthaman: hidden sophistication behind LLM file editing — https://sumitgouthaman.com/posts/file-editing-for-llms/
- Fabian Hertwig: Code Surgery — https://fabianhertwig.com/blog/coding-assistants-file-edits/
- CODESTRUCT paper (AST action spaces) — https://arxiv.org/pdf/2604.05407
- markdown-it-anchor — https://www.npmjs.com/package/markdown-it-anchor
- markdown-it-py architecture — https://markdown-it-py.readthedocs.io/en/latest/architecture.html
