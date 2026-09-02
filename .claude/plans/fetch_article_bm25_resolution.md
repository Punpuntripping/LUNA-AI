# `fetch_article` — BM25 Identity Resolution, a Planner-Side Cap, and a Structural Forwarding Channel

Rebuild `fetch_article`'s **regulation-resolution** leg on the BM25 ladder that
`simple_search/manual_search.py` already proved, stop the resolver from silently
committing to a non-exact match, **cap the article text the planner sees**, and —
the part the brief did not anticipate — give the full text its **own downstream
channel** so the cap does not propagate to the executors and the aggregator.

> **Scope:** `agents/tool_repository/fetch_article.py`, the planner decider prompt,
> and one new `ContextBlock` label in the orchestrator. Not `manual_search` itself
> (reuse, don't modify). Not the `articles_v2` keyed lookup. Not the executors.
>
> **Status: BUILT 2026-09-02 — not yet deployed.** 1,237 tests green across
> `tool_repository`, `simple_search` and `deep_search_v4`. Verified end to end
> against prod by replaying conversation `631a69af`: both cited articles now
> resolve at the pin and both bodies reach the executors. §9 records the
> measured outcome, including the one open decision (the shortlist floor).
>
> **Live-verified 2026-09-02** against `dwgghvxogtwyaxmbgjod`. Every constant
> below carries a measured justification reproducible with the quoted queries.

---

## 0. Correction to the brief

The brief said: *"the planner will hand it to the workflow, so no need to think
about the rest."*

**Half right.** The planner is the handoff point — but the handoff happens by
**retyping**, not by reference.

`orchestrator._build_candidate_context_blocks` (`orchestrator.py:926`) emits
exactly three labels: `case_brief`, `planner_brief`, `prior_search_lessons`.
There is no channel that carries `deps._fetched_articles` anywhere. The article
text reaches the expanders and the aggregator **only** because the planner
copy-pastes it into `planner_brief` by hand, as `prompts.py:126` instructs:

> "Carry the returned article text **verbatim into `planner_brief`** so it flows
> to the executors and the aggregator."

So a planner-side cap propagates: cap what the planner sees, and the planner can
only retype the capped version, and the executors get the capped version too.
**"The rest have to see it completely" is unachievable by capping alone.**

The codebase already carries a scar from this coupling. From
`orchestrator.py:1000`:

> "A forgotten label must never again silently drop a fetched-article brief from
> the expander/aggregator (scar: convo `ccd1afea` — planner wrote the brief but
> emitted `context_labels=["prior_search_lessons"]` so the 3 fetched articles
> reached nobody)."

That was patched by force-forwarding `planner_brief`. The underlying coupling —
*article text only survives if the planner retypes it correctly* — is still
there. §3C removes it, which is what makes §3B safe.

---

## 1. Why this exists — two measured defects

Traced from conversation `631a69af-ff78-4115-bfbc-9a4f9f272e1f`, trace
`01a062ddef0b5ae3b742f1b45bfca848` (2026-09-02 16:05). A judgment was attached
citing **المادة (الحادية والثلاثون) من نظام المرافعات الشرعية** and **المادة
(الثانية والعشرون) من نظام الإيجار التمويلي**. Neither reached retrieval.

**Defect 1 — the tool was never called.** `prompts.py:120` arms `fetch_article`
only when *"the **user** cites a specific article number"*. The user wrote only
«هل تقدر تعترض على الحكم»; both citations live inside the attachment. The tool
was loaded (confirmed in the run's `function_tools`) and the full `content_md`
was inline in `<attached_items>` — the planner even called
`unfold_workspace_item("WI-1")` and got the same string back. It just had no rule
saying a citation *inside an attachment* is a trigger. It carried «المادة (31)»
as a bare number into `planner_brief` and dropped 22 entirely.

**Defect 2 — the resolver returns the wrong law, silently.** Probed live:

```
resolve("نظام المرافعات الشرعية") → e3ae2a82 نظام المرافعات الشرعية    exact=True   ✅
resolve("نظام الإيجار التمويلي")  → cad83273 اللائحة التنفيذية لنظام…  exact=False  ❌
```

It returns the **لائحة**, not the statute, with no `AMBIGUOUS:` — and hands back
a different مادة 22 (صك الملكية، البطاقة الجمركية…) instead of هلاك الأصل المؤجر.

Mechanism: the corpus spells the statute with a **bare alef** — «نظام الايجار
التمويلي» (`cddc6ebd`) — while the لائحة and every natural spelling use hamza.
`_reg_ilike_patterns` (`fetch_article.py:441`) deliberately excludes the alef
fold because ILIKE is exact-char, so the full-string stage returns exactly one
row (the لائحة); being non-empty it short-circuits the token retry
(`:579`), and the statute is never a candidate. `difflib` then scores the only
row it has above `_MIN_MATCH_SCORE=0.40` and commits.

This is the failure the function's own docstring names — *"normalized before
RANKING but not before FETCHING"* — with the alef case excluded on the premise
that folding yields "an orthography no row contains". **False: 2,126 of 3,956
titles carry a hamza-alef.** Six statute↔decoy pairs currently diverge this way
(إنتاج المواد التعليمية، إيرادات الدولة، **الإيجار التمويلي**، المشاركة بالوقت،
التأمينات الاجتماعية، نظام العمل).

**Defect 3 — no length bound.** A live `statute_package` row
(`90cd5a8d…`, 2026-08-09) is **244,519 chars** — one article of اللائحة
التنفيذية لنظام ضريبة القيمة المضافة that went whole into the decider's context.

---

## 2. The measured ground

### 2.1 BM25 resolves both target laws at the pin, today

`bm25_search(array['regulation'], <q>, null, '{}', 8, 0, 100)`, measured
2026-09-02:

| query | rank‑1 | score | rank‑2 | ratio |
|---|---|---|---|---|
| «نظام المرافعات الشرعية» | نظام المرافعات الشرعية | **1016.59** | 11.41 | 89× |
| «نظام الإيجار التمويلي» | **نظام الايجار التمويلي** | **1014.19** | 13.40 | 76× |

`luna_normalize_ar` folds the hamza — verified:
`luna_normalize_ar('نظام الإيجار التمويلي') = luna_normalize_ar('نظام الايجار التمويلي')` → **true**.
So the 1000‑point exact bonus fires on exactly the pair today's ILIKE cannot see.
The wrongly-returned لائحة does not even make the top 4. **Defect 2 dies at
Gate 1, with no threshold involved.**

Both are present in `search_index` (verified) — this case is genuinely reachable.

### 2.2 BM25 alone would regress reach — the ladder is not optional

`search_index` row counts, re-measured 2026-09-02:

| corpus | BM25 rows | source rows | cover |
|---|---|---|---|
| regulation | 1,689 | 3,956 | **42.7 %** |
| circular | 1,843 | 1,843 | 100 % |
| judgment | 10,000 | 30,531 | 32.8 % |
| service | 100 | 4,746 | 2.1 % |
| *article* | **0** | 52,012 | **0 %** |

Today's ILIKE reaches all 3,956 regulations. BM25 reaches 1,689. So the BM25 rung
**adds** a pin and a ranking signal; it does not replace the ILIKE rungs. And
BM25 can never reach a مادة — the two-stage stands.

### 2.3 Article length — where to put the cap

`articles_v2`, n = 52,012, measured 2026-09-02:

| p50 | p90 | p95 | p99 | max |
|---|---|---|---|---|
| 325 | 1,332 | 2,008 | 5,088 | **244,419** |

| threshold | articles above | share |
|---|---|---|
| 4,000 | 768 | 1.48 % |
| 8,000 | 270 | 0.52 % |
| 20,000 | 86 | 0.17 % |

**`_PLANNER_ARTICLE_CAP = 4_000`.** 98.5 % of articles pass through whole; only
the pathological tail is trimmed, and the 244 KB case is cut by 98 %.

---

## 3. The four changes

### A. Resolution — BM25 ladder + `manual_search` gates

Replace the body of `resolve_regulation_id` (`fetch_article.py:675`). Keep the
signature and `ResolveResult`; widen it with the shortlist.

**Rungs**, merged into one candidate pool, each tagged with the rung that found it:

1. `bm25_search(['regulation'], title, …, p_limit=8, p_candidates=100)` — the new
   primary. Carries the exact pin.
2. `_fetch_reg_candidates_full` — the existing full-string ILIKE, kept for the
   57 % of regulations BM25 cannot reach.
3. `_fetch_reg_candidates_token` — the distinctive-token retry, **recall-only**
   (may populate the shortlist, may never win a gate alone). Carry
   `manual_search`'s `_RECALL_ONLY_RUNGS` bar verbatim: measured 3 of 3 wins by
   this rung were on must-refuse fixtures.

Rank with `manual_search._rank`'s key — `(pin, coverage, score)` — **coverage
above score, never a score floor.** The inversion table in `manual_search`'s
docstring is the reason: the two wrong answers scored 14.79 and 12.52 against
correct non-exact ones at 3.14–5.46.

**Gates**, ported from `manual_search.decide`:

| gate | condition | outcome |
|---|---|---|
| 1 | exactly one candidate with `score ≥ 1000` (normalized-title pin) | **resolve**, `high` |
| 1b | more than one pin (10 duplicate-normalized-title groups exist among regs) | **shortlist** |
| 2 | no pin | **shortlist** — never auto-resolve |
| 3 | shortlist would be empty | **not found** |

Gate 2 is the behavioural change the brief asked for: **`_MIN_MATCH_SCORE`,
`_AMBIGUITY_MARGIN` and the «ثقة متوسطة» auto-commit are deleted.** A non-exact
match is never an answer; it is a candidate. `FetchArticleResult.confidence`
collapses to `"high"` or `""`.

**Shortlist admission** — `_SHORTLIST_MIN_COVERAGE = 0.85`, top 3.

> **Stated assumption, flagged.** The brief specified 0.85. Applied to coverage
> (never to the raw BM25 score — §2.2's inversion), 0.85 is *stricter* than
> `manual_search`'s measured `_MIN_TITLE_COVERAGE = 0.60`, which was calibrated
> at the widest gap between correct resolutions (1.00/1.00/0.75/0.67) and wrong
> ones (0.50/0.33/0.20). At 0.85 a measured-correct near-miss like «نظام العمل
> السعودي» (0.67) is **not shown at all** — the planner gets an empty list and
> falls through to the normal search. That is *safe* (it can produce no wrong
> answer) but costs recall. §5's fixture run reports exactly what 0.85 costs
> versus 0.60; both are one-line constants.

### B. The planner-side cap

`FetchArticleResult` already separates the two audiences — its docstring says
*"`text` is what the model sees … `content` is the verbatim article body … the
body that gets pinned."* The cap lands on that seam, in `fetch_article_result`
(`:737`), and nowhere else:

- `content` — **always the full body.** Feeds `accumulate_fetched_article` →
  `deps._fetched_articles` → §3C's context block and §3D's workspace item.
- `text` — truncated to `_PLANNER_ARTICLE_CAP` on a paragraph boundary, with an
  explicit Arabic tail marker so the planner knows it is holding a fragment:

  ```
  … [اقتُطع نص المادة هنا للتخطيط فقط — النص الكامل وصل إلى البحث والتحرير كاملًا]
  ```

The marker matters. Without it the planner will retype a partial article into
`planner_brief` as though it were the whole rule.

**Ordering constraint (the trap):** cap *after* `accumulate_fetched_article` has
taken `content`, never before. Capping at accumulate time silently truncates the
downstream block and the workspace item, which is the exact opposite of the ask.

### C. The structural forwarding channel — `statute_articles`

The change that makes B safe. A **fourth `ContextBlock` label**, built from
`deps._fetched_articles` (full text, uncapped), force-forwarded like the two
briefs.

- `shared/context.py:53` — add `"statute_articles"` to the frozen label
  vocabulary in the docstring.
- `orchestrator._build_candidate_context_blocks` — emit it when
  `deps._fetched_articles` is non-empty:

  ```python
  if getattr(deps, "_fetched_articles", None):
      candidates["statute_articles"] = ContextBlock(
          label="statute_articles",
          body=build_statute_package_md(deps._fetched_articles),  # reuse §3D's renderer
          persistence="turn",
      )
  ```

  Reuses `build_statute_package_md` — one renderer, so the block and the pinned
  workspace item can never drift.
- `orchestrator._CANONICAL_LABEL_ORDER` → `("case_brief", "statute_articles",
  "planner_brief", "prior_search_lessons")`. Statute text before the planner's
  prose: it is the primary source, the brief is commentary on it.
- `orchestrator._FORCE_FORWARD_LABELS` → add `"statute_articles"`. Same reasoning
  as the `ccd1afea` scar — a forgotten label must never drop it.

**Read-order trap.** `_fetched_articles` is *cleared* by `flush_statute_package`
(`:877`, snapshot-and-clear so a double flush can't double-write), and the runner
flushes at `runner.py:327` — **before** `run_retrieval` builds the blocks. Two
options; take the first:

1. Have `flush_statute_package` leave the snapshot on a sibling slot
   (`deps._flushed_articles`) and have the block builder read that. Preserves the
   double-write guard.
2. Move the flush after block construction. Rejected — it would put the pin
   behind the whole retrieval, and the pause path (`:317`) flushes too.

### D. The conversation append — mostly already built

`flush_statute_package` (`:863`) already writes one `kind='note'`,
`metadata.subtype='statute_package'` workspace item per search, with
`content_md = build_statute_package_md(...)` (full text) and an
`_emit_pin_chip`. **Verified live: 10 such rows exist in prod**, most recent
`03ff100b…` (2026-08-30, 5 articles). It is called on both the decided branch
(`runner.py:327`) and the pause branch (`:317`).

So "append to the convo" needs no new mechanism. Three adjustments:

1. It must keep writing **full** text — guaranteed by §3B's ordering constraint.
2. Its `metadata.articles[].confidence` currently records `high|medium`; with
   §3A's collapse it becomes `high` only. One-line.
3. Add the resolved `regulation` display title to the per-article metadata so a
   later turn can tell «نظام الايجار التمويلي» (what was fetched) from
   «نظام الإيجار التمويلي» (what was asked). Cheap, and it is the receipt for the
   alef fold.

---

## 4. Prompt changes — `planner/prompts.py`

Three edits to the `fetch_article` section (`:118–127`):

1. **Widen the trigger (Defect 1).** Replace *"When the **user** cites…"* with a
   rule covering citations the planner *finds*: in `<attached_items>`, in
   `<prior_searches>`, or in the user's own words. A ruling that reasons from a
   numbered article is the paradigm case — fetch it before deciding whether the
   reasoning holds.
2. **Stop asking for a verbatim copy (`:126`).** With §3C the text flows
   structurally. The instruction becomes: *state in `planner_brief` which
   articles you fetched and what the attachment does with them; the full texts
   reach the executors and the aggregator on their own.* This is a token saving
   and removes a fidelity risk — and it is what makes the cap coherent.
3. **Replace the `AMBIGUOUS:` / «ثقة متوسطة» guidance with the shortlist
   contract.** The returned table is picked from by re-calling with the corpus
   spelling, quoted verbatim — or not at all.

**The shortlist return string** (§3A Gate 2). "Nothing" must be written into it
as an explicit option; a model handed a numbered list picks from it, and an
implicit refusal rebuilds the silent auto-commit with extra steps:

```
لم يتطابق «<ما طُلب>» مع نظام واحد بعينه. أقرب المرشحين:
1. «<العنوان كما هو في المدونة>» — نظام
2. «<العنوان كما هو في المدونة>» — لائحة تنفيذية
3. «<العنوان كما هو في المدونة>» — ضوابط

إن كان أحدها هو المقصود فأعد النداء باسمه كما هو مكتوب أعلاه.
وإن لم يكن أيٌّ منها المقصود فلا تختر — اترك الأمر للبحث العادي.
```

Unchanged: `fetch_article` grounds the brief, it does **not** replace retrieval.
The normal `reg_compliance` search still runs and still supplies the citations.

---

## 5. Validation

**Fixture set** — no new harness; extend `agents/simple_search/eval/`'s pattern.

- The 7 calibration queries from `manual_search`'s inversion table (regression:
  the ported gates must reproduce its verdicts).
- The 6 statute↔decoy alef pairs, **both spellings each** — 12 cases. Every one
  must resolve to the statute or return a shortlist; none may return a decoy.
- «نظام الإيجار التمويلي» / 22 and «نظام المرافعات الشرعية» / 31 — the
  conversation's own failures. Expect Gate 1, `high`, the correct bodies (هلاك
  الأصل المؤجر, and الفقرة (أ) الدعاوى المتعلقة بالعقار).
- The 3 known must-refuse absent laws («نظام الفساد المالي والإداري», «نظام حماية
  الفضاء السيبراني الوطني», «تطبيقات نظام العمل») — must be `not_found` or an
  unpicked shortlist, never a resolve.
- The 244 KB article — assert `len(text) ≤ 4_000 + marker`, `len(content)` full,
  and the workspace item full.

**Report both thresholds.** Run the set at `_SHORTLIST_MIN_COVERAGE` 0.85 and
0.60 and print the difference in shown-candidate recall. That is the evidence for
which number ships.

**End-to-end:** replay conversation `631a69af` and assert the `statute_articles`
block reaches the aggregator's user message with both bodies intact while the
decider's context holds the capped `text`.

---

## 6. Files

| file | change |
|---|---|
| `agents/tool_repository/fetch_article.py` | §3A rewrite of `resolve_regulation_id` + rungs; §3B cap on `text`; delete `_MIN_MATCH_SCORE`, `_AMBIGUITY_MARGIN` use, «ثقة متوسطة»; §3D metadata |
| `agents/deep_search_v4/shared/context.py` | add `statute_articles` to the label vocabulary |
| `agents/deep_search_v4/orchestrator.py` | emit + force-forward + order the new block |
| `agents/deep_search_v4/planner/runner.py` | preserve the flushed snapshot for the block builder (§3C trap) |
| `agents/deep_search_v4/planner/deps.py` | `_flushed_articles` sibling slot |
| `agents/deep_search_v4/planner/prompts.py` | §4's three edits |
| `agents/tool_repository/tests/` | §5 fixtures |

`agents/simple_search/manual_search.py` — **read and import from, never edit.**

---

## 7. Traps

1. **Cap before accumulate** → truncates the downstream block and the pinned
   item. The whole point is that only `text` is capped.
2. **Forgetting `_FORCE_FORWARD_LABELS`** → re-runs the `ccd1afea` scar, this
   time with the planner no longer retyping the text as a backstop, so the loss
   would be total rather than partial.
3. **A score floor on BM25** → inverts (§2.2). Coverage only.
4. **`n == 1` treated as confident** → today's bug. A lone candidate makes any
   rank‑1/rank‑2 ratio undefined; both absent-law queries returned exactly one
   row. A singleton without a pin is a shortlist entry or nothing.
5. **Dropping the ILIKE rungs** → loses 57 % of the regulation corpus (§2.2).
6. **`p_candidates` too small** → the RPC narrows by `ts_rank_cd` *before*
   applying the 1000-point bonus, so a title can be dropped before its pin is
   computed. 100 measured safe across 8 queries; 500 costs 3.2× latency.
7. **Truncating mid-sentence with no marker** → the planner retypes a fragment as
   the whole rule.

---

## 8. Out of scope

`manual_search` itself; the `articles_v2` keyed lookup; merging the two resolvers
into one shared module (they stay separate, sharing pure helpers — the
established direction, `manual_search` already imports six names from
`fetch_article`); the executors and aggregator prompts; `simple_search`'s own
resolution path; and any cap on the user-facing workspace item, which by the
brief's instruction keeps the complete text.


---

## 9. Built — what shipped and what it measured

### 9.1 The failing conversation, replayed against prod

```
asked : نظام المرافعات الشرعية / الحادية والثلاثون
got   : نظام المرافعات الشرعية        [ok/high]   text 678   content 620
asked : نظام الإيجار التمويلي  / الثانية والعشرون
got   : نظام الايجار التمويلي         [ok/high]   text 893   content 837

forwarded labels: ['statute_articles']        body = 1,608 chars
  PASS  art 31(a) العقار clause present downstream
  PASS  art 22 هلاك جزئي clause present downstream
  PASS  resolved statute (bare alef), not its لائحة
  PASS  لائحة's مادة 22 (صك الملكية) NOT substituted
```

Note the forwarded labels: `planner_brief` was **empty** and the article text
still reached the executors. That is the decoupling working — under the old
design that same turn delivered nothing.

### 9.2 Resolver fixtures — 17 queries, both thresholds

| | floor 0.85 | floor 0.60 |
|---|---|---|
| pinned correctly | 13 | 13 |
| answer reachable (pin or on the shortlist) | 13 | **14** |
| unreachable | **1** | 0 |
| wrong resolve | 0 | 0 |
| must-refuse held | 3/3 | 3/3 |

All six statute↔decoy alef pairs pin correctly **in both spellings**. The three
absent laws are refused at both floors. Zero wrong resolves anywhere — the class
of failure that started this is gone.

### 9.3 The one open decision — the shortlist floor

The floor changes the outcome of exactly **one** fixture, «نظام العمل السعودي»:

* **at 0.85** — shortlist of 1, and it is **not** the right answer.
* **at 0.60** — shortlist of 3, **including** «نظام العمل».

So the cost of 0.85 is worse than §3A predicted. The prediction was that it
would show *nothing* and fall through to search, which is safe. What it actually
does on this query is show a *lone wrong candidate* — and a one-entry shortlist
is the most persuasive shape a shortlist can have. The "choose nothing" clause in
the payload is what stands between that and a bad pick.

`_SHORTLIST_MIN_COVERAGE = 0.85` ships as briefed. `0.60` is a one-line change
and, on this evidence, the better number. **Owner's call.**

### 9.4 One fix beyond the plan — ranking

Measured during the build: coverage alone ranked a 12-word title
(«قواعد وإجراءات عمل لجنة النظر في مخالفات نظام كود البناء السعودي…») **first**
at coverage 1.00, above «نظام العمل» at 0.67, because all three short query terms
occur scattered through the long title. Coverage is asymmetric — it asks only how
much of the *query* is in the title.

`title_precision` adds the mirror (how much of the *title* the query explains),
and `_relevance` is their harmonic mean. Ranking became
`(pin, relevance, coverage, score)`; the junk title fell from rank 1 to rank 4.
**Ranking only** — admission stays on raw coverage, whose calibration table would
not transfer to a quantity it was never measured against.

### 9.5 Where the shared code went

`coverage`, `_query_terms`, `_term_in_title`, `_strict_exact` and
`_MIN_TERM_CHARS` moved **down** from `manual_search` into `fetch_article`, and
`manual_search` re-exports them. `fetch_article` is the lower layer — the reverse
import would be a cycle. Every existing importer is untouched.

The one deliberate divergence: `_build_ambiguous`. The two were byte-identical
while both meant "these tie, ask the user". `fetch_article`'s now means "here is a
shortlist, pick one or take none", addressed to the planner rather than the user,
so identical bytes would be the wrong bytes. The shared contract is now the
`AMBIGUOUS:` prefix and the 3-candidate cap, pinned by a test.

### 9.6 Test coverage added

`test_fetch_article.py` — the fake Supabase gained a `bm25_search` RPC with the
1000-point bonus and a deliberately shallower `luna_normalize_ar` fold, so the SQL
pin and the strict Python pin are separable in tests. New: the orthography split
both ways, the Arabic-ordinal path a ruling actually writes, the cap seam
(`text` trimmed / `content` and the package whole / no-op below the cap), the
three must-refuse laws, the singleton guard, and the ranking inversion.
`test_confidence_medium_on_nonexact_match` was **inverted** into
`test_nonexact_match_never_auto_commits` — the behaviour it protected is the bug.

`test_context_block_forwarding.py` — `statute_articles` forwards with no label,
carries both bodies verbatim, is absent when nothing was fetched, survives an
empty `planner_brief`, precedes `planner_brief`, and reaches the sector picker.

### 9.7 Not done

Deployment. And the working tree carries unrelated in-flight editorial/blog-wing
work (`PinnedPlan` in `planner/models.py`, `planner/runner.py`,
`agents/orchestrator.py`, `planner/apply.py`) that is **not** part of this change
— worth separating before committing.
