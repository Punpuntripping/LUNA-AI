# `fetch_article` — Planner Article-Lookup Tool — Design Plan

A Pydantic AI **planner tool** that fetches the verbatim text of **one article (مادة)**
from a **named regulation**, deterministically, *before* the planner decides the search —
and the planner folds that text into **`planner_brief`** so it flows to the executors and
the aggregator.

> Scope: direct article retrieval from `articles_v2.content` by
> `(regulation_title, article_number)`. The fetched text is carried into the
> planner's `planner_brief` context channel — it grounds the restatement/scope and
> reaches the aggregator as authoritative brief content.
>
> Status: **DESIGN ONLY — not built.** Decisions locked 2026-06-25/26:
> source = `articles_v2.content` · output lands in `planner_brief` ·
> `article_number` is text · ask-the-user on ambiguity · build deferred.

---

## 1. Why this tool exists

Semantic search **cannot reliably retrieve an article by its number.** The corpus
writes article numbers as Arabic words inside the prose ("المادة الحادية والثمانون"),
not as the digit "81". So a user asking about "المادة 81 من نظام العمل" can run the full
pipeline and still get an answer whose own gaps say:

> *"لم تتضمن المراجع النص الحرفي للمادة 81 من نظام العمل"*

(Observed live in conversation `a91945f0-…`, route deep_search, 2026-06-25.)

The fix is deterministic structured lookup against the **article-level** table.

---

## 2. Schema reality — use `articles_v2` (one row per article)

`articles_v2` is the article-grain table. Columns:

| column | type | notes |
|---|---|---|
| `id` | uuid | |
| `article_ref` | text | e.g. `17609_reg_122_Article_81` |
| `chunk_parent_id` | uuid | the `chunks_v2` chunk this article was split from |
| `regulation_id` | uuid | FK → `regulations_v2.id` |
| `article_number` | **text** | `"81"`, but also compound `"1-1"`, `"1-1-2"` in executive regs |
| `content` | text | the **verbatim** article body — what we return |
| `ingested_at` | timestamptz | |

Verified live — نظام العمل (`regulation_id = da51024f-…`), article 81:

```sql
SELECT content FROM articles_v2
WHERE regulation_id = 'da51024f-…' AND article_number = '81';
-- → "##### **المادة الحادية والثمانون:**  يحق للعامل أن يترك العمل دون إشعار، مع
--    احتفاظه بحقوقه النظامية كلها، وذلك في أي من الحالات الآتية: 1. إذا لم يقم صاحب
--    العمل بالوفاء بالتزاماته العقدية أو النظامية …"
```

This **supersedes** the earlier `chunks_v2.owns->'MADDA'` approach — `articles_v2`
gives one exact row per article, no chunk-neighbor bleed, no jsonb containment.

**`article_number` is text, not int.** Many values are compound (`"1-1"`, `"1-1-2"`),
so the corpus stores them as strings. The tool arg is therefore **`str`**, matched by
**exact text equality** — this also covers `مكرر`/bis variants if present. (A plain
`81` still works; it's just the string `"81"`.)

Regulation identity: still **title only** (`regulations_v2.title`/`clean_title`); there
is no public "regulation number" (`reg_ref` is an internal ingestion id). The tool talks
to Supabase via **PostgREST (no raw SQL, no new RPC/migration)**, so title matching is:
PostgREST **ILIKE candidate-fetch → normalize + rank in Python**. (`pg_trgm` exists but
SQL trigram would need a new RPC; ranking in Python keeps the tool self-contained and
sidesteps migration drift.)

---

## 3. Tool contract

Mirrors `unfold_workspace_item` (plain-string return, registered on the decider only).

```python
@agent.tool
async def fetch_article(
    ctx: RunContext[PlannerDeps],
    regulation_title: str,    # "نظام العمل" — as the user named it
    article_number: str,      # "81" (or compound "1-1") — exact-text key
) -> str:
    """Fetch the verbatim text of ONE article (مادة) from a named regulation,
    BEFORE deciding the search, so you can carry it into planner_brief. Use when
    the user cites a specific article by number in a specific law/regulation.
    Returns the article's actual text. It does NOT replace the search."""
```

`PlannerDeps` already carries everything needed — `.supabase`, `.user_id`,
`.conversation_id`. **No deps change.**

---

## 4. Body — two deterministic steps

### Step 1 · Resolve `regulation_title` → `regulation_id` (the only fuzzy part)

1. **Normalize app-side** (Python — `unaccent` is NOT installed): strip tashkeel,
   unify alef (أ/إ/آ → ا), ة → ه, ى → ي, collapse whitespace, drop leading "ال".
2. **Candidate fetch via PostgREST** (no raw SQL): `supabase.table("regulations_v2")
   .select("id,title,clean_title,doc_type_bucket,status_class").ilike("title", f"%{token}%")`
   — and the same on `clean_title` — where `token` is a distinctive **raw** substring of
   the user's title (ILIKE is exact-char, so use the raw form, not the normalized one).
   If empty, retry with the single most distinctive token.
3. **Rank in Python:** normalize BOTH the query and each candidate title, then —
   exact normalized match wins outright; else score by string similarity (`rapidfuzz`
   if available, else `difflib.SequenceMatcher`) with a `doc_type_bucket` preference
   (`law_statute` for "نظام", `executive_regulation` for "لائحة") and a shorter-title
   tiebreak. Verified ground truth: bare "نظام العمل" → exact win; next candidate
   "نظام العمل التطوعي" is a clear step down.
4. **Ambiguity gate (locked: ASK).** If there is no exact normalized match *and* the
   top-2 scores are close (≈ within 0.1), return an `AMBIGUOUS:` payload listing 2–3
   candidate titles. The planner then calls its existing **`ask_user`** deferred tool to
   ask which regulation the user means. The tool **never silently grabs the wrong law.**
   (Scar: the انفيجو misclassification → wrong legal frame.)
5. **Score floor (`_MIN_MATCH_SCORE = 0.40`).** If the best candidate is not exact *and*
   scores below the floor, return **no match** (→ the not-found path, which names the
   user's own title) rather than confidently accepting a low-similarity wrong law.
   Calibrated against the corpus: a genuine partial match («العمل» → «نظام العمل») ≈ 0.48;
   a spurious fallback-token hit («نظام الفساد المالي والإداري», absent, → «لائحة اشتراطات
   السلامة … والإدارية») ≈ 0.38. Added 2026-06-27 after `convo_ccd1afea` resolved an
   absent law onto an unrelated bylaw and returned a misleading "article not found in
   <wrong law>".

### Step 2 · Fetch the article

```sql
SELECT content FROM articles_v2
WHERE regulation_id = :rid AND article_number = :num   -- :num is text
LIMIT 1;
```

- `content` is the **only** column the tool needs — it's what the planner folds into
  `planner_brief`. We deliberately do NOT select `article_ref` (an internal ingestion id
  like `17609_reg_122_Article_81`) — it helps neither the brief nor the aggregator, and
  internal refs are never surfaced as public numbers. (At most, log `article_ref` as a
  forensics tag — never return it.)
- Sync Supabase client wrapped in `asyncio.to_thread` (established agent pattern), via
  `ctx.deps.supabase`.
- **Not found** (article absent / repealed / wrong number form) → return
  `"المادة N غير موجودة في <reg>"`, so the planner falls back to normal semantic search
  instead of fabricating.

---

## 5. Output → `planner_brief` (the whole point)

`fetch_article` returns the article text as a string in the tool-call loop. The planner
must then **copy that verbatim text into `planner_brief`** — the facts channel that, per
`planner/prompts.py:109`, is one of only **three blocks that flow to the executors and the
aggregator** (`case_brief`, `planner_brief`, `prior_search_lessons`). This is the same
move `unfold_workspace_item` enables (`agent.py:184`: "anchor query_restatement /
planner_brief on a specific named regulation").

Consequence: the **verbatim article reaches the aggregator** as authoritative brief
content — directly closing the "references didn't contain the literal text of article 81"
gap. By design the article moves as **text only**; it is never converted into a formal
`[n]` citation.

### Prompt change
Add a `## fetch_article` section to `PLANNER_DECIDER_SYSTEM_PROMPT`
(`planner/prompts.py`, after the `unfold_workspace_item` section ~line 111, alongside the
existing `## planner_brief` section ~line 115). It must state:

1. **When** — the user names a specific article number in a specific regulation.
1b. **Article cited but law NOT named → ask, don't guess/search.** If the user cites
   an article by number without naming the نظام (and it isn't unambiguous from context —
   e.g. «نُفّذت عليّ المادة 46» spans نظام التنفيذ / نظام التنفيذ أمام ديوان المظالم /
   لائحة مقدمي خدمات التنفيذ…), the planner must `ask_user` «المادة N من أي نظام؟» FIRST,
   then call `fetch_article`. It must NOT fall through to a generic search past the cited
   article. (`fetch_article` requires a `regulation_title`; an article number alone can't
   drive it.) Added 2026-06-27 after the planner searched instead of asking on a real
   enforcement memo.
2. **Pass `article_number` as the string the user used** ("81", "1-1") — including
   converting Arabic ordinals / Arabic-Indic digits ("الحادية والثمانون", "٨١") to the
   plain form.
3. **Carry the result into `planner_brief` verbatim** so it flows downstream.
4. **Still search.** The article grounds scope/restatement only; the planner must STILL
   run the normal reg_search so the answer gets its supporting sources and citations from
   the corpus. The fetched article itself stays purely as `planner_brief` text — it is
   never turned into a citation.

Per project convention: edit the prompt **in the `.py`**, then regenerate the reference
catalog via `scripts/extract_prompts_md.py` (editing the `.md` alone is a no-op).

---

## 6. Files touched (when greenlit)

| File | Change |
|---|---|
| `agents/tool_repository/fetch_article.py` *(new)* | tool fn + `register_fetch_article(agent)` + Python title-normalizer + resolver + `articles_v2` fetch |
| `agents/deep_search_v4/planner/agent.py` | call `register_fetch_article(agent)` on the decider (next to `register_unfold_workspace_item`) |
| `agents/deep_search_v4/planner/prompts.py` | add the `fetch_article` section + the "carry into `planner_brief`" rule |
| `agents/deep_search_v4/planner/deps.py` | none — `PlannerDeps` already carries `.supabase` |
| `agents/tool_repository/tests/` + prompt-catalog regen | unit tests; rerun `extract_prompts_md.py` |

---

## 7. Locked decisions

- **Source: `articles_v2.content`** — one exact row per article, keyed by
  `(regulation_id, article_number:text)`. Select `content` only.
- **Text-only into `planner_brief`** — the planner folds the verbatim article text in; it
  flows to executors + aggregator as brief content. The article is **never** turned into
  a formal `[n]` citation — no URA injection, no `chunk_parent_id`, no "pin as citation".
  This is the final, intended behavior, not a first cut.
- **Ambiguity: ask the user** — surface candidates → planner `ask_user`.

---

## 8. Edge cases to cover in tests

- Bare canonical title ("نظام العمل") → exact, unique. ✅ verified.
- Lookalike titles ("نظام العمل التطوعي", "اللائحة التنفيذية لنظام العمل") present →
  must NOT be picked over the exact law.
- Plain `"81"` and compound `"1-1"` / `"1-1-2"` article numbers both resolve.
- Article absent / repealed → "not found" string, no fabrication.
- Two close trigram candidates, no exact → `AMBIGUOUS:` → planner asks.
- Arabic-Indic digits / Arabic ordinals in the user message → planner normalizes to the
  stored text form (assert the exact-text key is what hits the DB).
- The planner actually places the fetched text into `planner_brief` (assert via a
  decision-level test, not just the tool return).

---

## 9. House conventions — mirror the existing `tool_repository/` tools

Derived from reading all five existing tools (`unfold_workspace_item`, `save_memo`,
`edit_artifact`, `add_user_template`, `edit_supabase_md`). `fetch_article.py` must match:

1. **Module docstring** — what / why-a-tool / "Design notes" / a `Registration::` block /
   the deps-contract name. (Every tool has this.)
2. `from __future__ import annotations`; `logger = logging.getLogger(__name__)`.
3. **Schema-config constants block** with the one-line-rename comment, e.g.
   `_REGS_TABLE = "regulations_v2"`, `_ARTICLES_TABLE = "articles_v2"`.
4. **`@runtime_checkable class HasCorpusContext(Protocol)`** — loose `supabase: object`
   to avoid a hard client import. `fetch_article` needs **only `.supabase`** (the corpus
   is public — no `user_id`/RLS scoping, unlike unfold/save_memo). Simpler than the
   workspace tools.
5. **Separate pure layer**, unit-testable without an agent or live DB — the `unfold`
   (`unfold_item` / `resolve_used_sources` / `render_unfold_md`) and `save_memo`
   (`save_memo_core`) pattern. For us: `normalize_title()`, `resolve_regulation(supabase, …)`,
   `fetch_article_text(supabase, reg_id, num)`, and a thin `fetch_article_md(...)` that
   composes them. The `@agent.tool` wrapper just calls the composer.
6. `register_fetch_article(agent)` entry point; inner
   `@agent.tool async def fetch_article(ctx, …) -> str:  # noqa: RUF029 — supabase sync`.
7. **Rich docstring** on the tool: when-to-use, `Args`, `Returns`. (English is fine —
   the planner analog `unfold` uses English; the Arabic guidance lives in the prompts.py
   section.)
8. **Failure contract = plain string, never `ModelRetry`** (the explicit `edit_artifact`
   house rule: "brief/react on the NEXT model turn, don't burn tool-retry budget; keeps
   TestModel smoke runs completing"). So: ambiguous → `AMBIGUOUS:` string; not-found →
   Arabic string; resolve/fetch error → caught, logged (`# noqa: BLE001`), degrade to the
   not-found string. **No `ModelRetry` anywhere** (`add_user_template` only uses it for a
   model-fixable empty title — we have no such case).
9. Sync Supabase reads wrapped in `asyncio.to_thread`; `getattr(resp, "data", None) or []`.
10. **`__all__`** exporting `register_fetch_article` + the pure functions + the Protocol,
    so tests import the pure layer directly (mirrors every tool's `__all__`).

---

## 10. Confidence score + auto-pin (built 2026-06-27)

A successful fetch now carries a **confidence** and is **auto-pinned** as a durable
workspace item — a lean reuse of `save_memo`'s persistence pattern.

**Confidence** — derived from the resolver's existing state, no new matching:
- `high` — exact normalized regulation match (`ResolveResult.exact`) **and** article found.
- `medium` — above-floor non-exact match (the law was a best-guess) **and** article found.
- (`AMBIGUOUS:` / below-floor / article-not-found are not confident results.)

On a `medium` match the returned **text** gets a trailing «(ثقة متوسطة …)» verify note —
the model-facing `text` only, never the pinnable `content`. The prompt tells the planner
to verify (or `ask_user`) and to carry only the article body into `planner_brief`.

**Pin — ONE `statute_package` per search** (gate: HIGH or MEDIUM → any successful fetch;
depth: lean insert). Not one card per article — the N (parallel) `fetch_article` calls in
a turn **accumulate**, and the runner flushes them into a single bundle:
- `fetch_article_result()` returns a `FetchArticleResult {text, status, confidence,
  reg_id, reg_name, article_number, content}`; `fetch_article_text()` is a thin wrapper.
- The tool body, on `status=="ok"`, calls `accumulate_fetched_article(deps, …)` — a plain
  `list.append` to `deps._fetched_articles` (atomic on the event loop; no DB write, no
  lock). Not-found / ambiguous don't accumulate.
- `agents/deep_search_v4/planner/runner.py` calls **`flush_statute_package(deps)`** once
  per turn — at the decision-in-hand point AND on the `ask_user` pause path — writing ONE
  `create_workspace_item(kind=note, subtype='statute_package', …)` via the
  lazy/monkeypatchable `_insert_statute_item`. Body = «📌 نصوص المواد المثبّتة من البحث»
  marker + one `## نص المادة …` section per article; metadata `{subtype, articles:[{regulation,
  article_number, confidence}…]}`; title names the single article or «نصوص المواد المستشهد
  بها (N)».
- **Dedup within the turn** by `(regulation, article_number)` (the planner's retry loop may
  fetch the same article twice). Each search writes its own package — no cross-turn dedup.
- **Best-effort**: the accumulator is snapshotted-and-cleared up front; a flush failure
  (missing scope / DB error) NEVER affects the returned text — guarded, logged, `None`.
- **Lean, not full `save_memo`**: a plain insert (persists + loads as a summary + unfolds
  next turn) + an *optional* `workspace_item_created` chip via `emit_sse`. **No** router
  force-attach/alias sinks (`PlannerDeps` doesn't carry them; the articles already ground
  THIS turn via `planner_brief`).
- **Identity**: `subtype='statute_package'` (NOT `'memo'`) — corpus text, not a user message.

Deps: a new `PlannerDeps._fetched_articles: list` accumulator (rebuilt empty each turn);
the flush reads `.user_id` / `.conversation_id` / `.emit_sse` via `getattr`, skipping the
write if scope is absent. The tool's `HasSupabase` read contract is unchanged.
