# Gate Exposure Budget — re-basing every free preview on a *fraction of the document*

Status: **JUDGMENTS BUILT, NOT DEPLOYED** (steps 1–3, 6 of §4 · 2026-08-10).
Regulations / circulars / forms / مواد still on the old per-section budgets.
Measured live against prod (`dwgghvxogtwyaxmbgjod`). Supersedes the per-wing
character constants in `backend/app/services/library_service.py`.

⚠ **Deploying this requires an ISR purge of /judgments** — 10,000 pages are
24h-cached and would otherwise keep serving the old previews.

Trigger: `https://rayhanai.com/regulations/ضوابط-إسكان-الحجاج` serves ~48% of its
نص plus a complete LLM summary to an anonymous reader, while reporting
`gate: "gated"` and `hidden_section_count: 0`.

---

## 1. The one root cause

**Every gate in the library is an *absolute per-section* character budget. No
code path anywhere asks "what fraction of THIS document am I giving away?"**

So the gate scales the wrong way: the shorter the document, or the more sections
it has, the more of it is free — with no ceiling at 100%.

| Wing | Rule as shipped | Constant |
|---|---|---|
| Regulation doc | first **3** sections × 600 chars, + `llm_summary` **always free** | `get_regulation_doc` `free_chars=600` |
| Judgment | section 1 **whole**, then 1,200 chars **× every remaining section** | `JUDGMENT_FREE_CHARS`, `JUDGMENT_FREE_LEADING_SECTIONS` |
| Circular | ≤800 chars ⇒ **fully open**; else 400 chars | `CIRCULAR_FREE_LENGTH`, `GATE_FREE_CHARS_DEFAULT` |
| مادة page | 500 chars | `ARTICLE_FREE_CHARS` |
| Form | 300 chars | `FORM_BODY_FREE_CHARS` |

Four failure modes fall out of that one design:

1. **Per-section budgets multiply.** N sections × budget = free bytes grow
   linearly with section count. A 5-section judgment gets 4 × 1,200 = 4,800 chars
   *plus* all of section 1.
2. **`truncate_for_gate` has no floor.** It returns text unchanged when
   `len <= free_chars` — correct rendering, but nothing downstream ever checks
   *"did I actually withhold anything?"*. A short section, a short تعميم, a short
   مادة all ship 100% while the payload still says `gate: "gated"`.
3. **`free_leading` renders judgment section 1 untruncated.** The code comment
   says a single-section document is "the common case" — measured, **40% of
   judgments (3,994) have ≥2 sections**, and section 1 is the وقائع narrative,
   usually the bulk of the ruling.
4. **`hidden_section_count = max(0, n − 3)`** counts *sections*, not *bytes*.
   **877 regulations (25%) have ≤3 chunks**, so the page reports nothing hidden
   and prints the entire TOC — the gate is invisible because there is nothing
   left to gate.

Plus one asset that is simply ungated: **`llm_summary`, 100% coverage on all
3,446 أنظمة, 1,364 chars avg** — our own restatement of the whole نظام, free to
anon and to crawlers. On the 877 short أنظمة (5,183 chars avg) that alone is ~26%
of the document.

## 2. What is actually exposed today (measured, not estimated)

| Wing | Items | Avg % of body free | Worst class |
|---|---|---|---|
| **Judgments** | 10,000 | **42.0%** (46.1% incl. `short_summary`) | 846 items ≥90% free · 2,077 ≥70% · 3,012 ≥50% |
| **Circulars** | 1,843 | **45.6%** | 480 (26%) fully open by the ≤800 rule |
| **Regulations** | 3,446 | **28.7%** (11.5% body + `llm_summary`) | 877 short أنظمة avg **61.2%** free; 57 at ≥100% |
| **مواد pages** | **5 published** (50,924 rows exist) | — | **65.8% of rows are ≤500 chars ⇒ would ship whole**. Latent, not live. |

The `JUDGMENT_FREE_CHARS` comment claims the rule withholds "roughly 85–90% of a
typical judgment". Measured withholding is **58%**.

The trigger نظام, exactly: 3 chunks / 6,480 chars → 1,787 chars of نص (27.6%) +
1,302-char `llm_summary` = **47.7% free**, `hidden_section_count: 0`.

⚠ The 54 `seo_tier='open'` أنظمة are **intentionally** 100% open (decision
2026-08-01, `access_tiers_gating_DECISIONS.md`). Nothing here touches them.

## 3. The new strategy

### 3.1 One rule, document-wide, expressed as a ratio

Replace every per-wing constant with a single budget function:

```python
def free_budget(total_chars: int, *, ratio: float, floor: int, ceiling: int) -> int:
    """Document-wide free allowance, in characters."""
    return min(max(round(ratio * total_chars), floor), ceiling)
```

- **ratio** — the policy dial. What share of the document we are willing to give away.
- **floor** — thin content ranks badly; never serve less than this or we lose the SEO the wing exists for.
- **ceiling** — a 45k-char نظام must not leak 7k just because it is long.

The budget is computed **once per document from the document's own total length**,
then **spent across sections in reading order** until exhausted. It is *not*
granted per section. That single change kills failure modes 1 and 3.

Simulated against live data:

| Wing | Today | ratio 0.15 / floor 600 / ceil 2000 | ratio 0.20 / 800 / 2500 |
|---|---|---|---|
| Regulations (body only) | 11.5% | **11.8%** | — |
| Judgments | 42.0% | **16.8%** | 21.9% |

Judgments are where the win is. For regulations the body rule is already roughly
right — **the regulation problem is `llm_summary`, not the نص budget** (11.8%
body-only vs **25.4%** once the summary rides free on top).

### 3.2 A floor on what is WITHHELD — "gated" must mean something

New invariant, enforced in one place for all wings:

> If, after truncation, the withheld remainder is under `MIN_WITHHELD_RATIO`
> (proposed 0.5) of the document **or** under `MIN_WITHHELD_CHARS` (proposed
> 800), the item **cannot be served as gated**. Resolve it one of two ways —
> never both, never neither:
>
> - **(a) Cut deeper** to the floor, if the document is long enough to survive it; or
> - **(b) Mark it honestly `open`** — no CTA, no placeholder bars, full text,
>   `official_sources` published, full crawl value.

This generalises `effective_circular_gate` (today the only wing with such a rule,
hand-tuned at 800 chars) into the shared mechanism, and it retires the class of
page that shows a paywall over a document it is not withholding.

Consequence to accept up front: some short أنظمة and most short تعاميم become
**genuinely open**. That is the honest outcome — they are already open in
practice, and an open page ranks better and carries no false promise.

### 3.3 `llm_summary` — the open decision

For أنظمة this is the dominant term, and it is a **policy call, not a bug**:

- **Option A — count it against the budget.** Summary chars are spent first; the
  نص preview gets whatever is left. On a short نظام that means summary only, zero
  نص. Consistent, one dial.
- **Option B — gate it, like شرح.** The 2026-07-28 precedent already says شرح is
  Rayhan's value-add and stays gated even on an open نظام. `llm_summary` is the
  same class of asset — and prior audit called it "the most copyable asset you
  own". Ship a shortened lead (~300 chars) free, gate the rest.
- **Option C — keep it fully free** (status quo). Defensible purely on SEO: it is
  unique text, not public-domain نص, and it is what ranks. Then accept ~25%
  document-level exposure on أنظمة and stop calling it a leak.

**DECIDED 2026-08-10 — Option A.** `llm_summary` chars are spent from the
document budget before any نص. It keeps one dial for the whole wing, preserves a
real free lead for SEO, and it is the only option under which the ratio in §3.1
describes what a reader actually receives. Applies when step 4 lands.

`short_summary` on judgments (238 chars avg) is small enough to leave free under
any option.

### 3.4 Report the truth in the payload

`hidden_section_count` counts sections and lies on short documents. Add and use:

- `withheld_chars` — bytes actually dropped server-side.
- `withheld_pct` — of the document.
- Keep `hidden_section_count` for the placeholder bars, but derive `gated` on the
  frontend from `withheld_chars > 0`, never from a section count.

This is also what makes §5 auditable rather than re-eyeballed.

### 3.5 Wing dials (proposed starting values, to be tuned by §5)

| Wing | ratio | floor | ceiling | Notes |
|---|---|---|---|---|
| Judgment | 0.15 | 600 | 2,000 | 42.0% → 16.8%. Drop `JUDGMENT_FREE_LEADING_SECTIONS` entirely; front-load the budget instead. |
| Regulation | 0.15 | 600 | 2,000 | Body ≈ unchanged; the real move is §3.3. |
| Circular | 0.15 | 400 | 1,200 | Replaces the ≤800 auto-open with §3.2's general rule. |
| مادة | 0.15 | 250 | 800 | **Must land before the مادة wing publishes past 5 items.** Re-gating indexed pages is worse than gating them right the first time. |
| Form | 0.15 | 300 | 800 | Unchanged in spirit. |

## 4. Build order

1. ✅ **`GateBudget` + `free_budget` + `spend_budget_across_sections`** in
   `library_service.py`, pure, unit-tested.
2. ✅ **`gate_decision(total_chars, gate, budget)`** implementing §3.2.
   `effective_circular_gate` still stands until step 5 re-points circulars onto it.
3. ✅ **Judgments** — `JUDGMENT_BUDGET = GateBudget(0.15, 600, 2000)`;
   `JUDGMENT_FREE_CHARS` and `JUDGMENT_FREE_LEADING_SECTIONS` deleted.
4. ⬜ Re-point **regulation** doc builder + apply §3.3 (Option A).
5. ⬜ Re-point **circular**, **form**, **مادة**.
6. ✅ **`get_full_judgment` parity** — unaffected: it re-parses `content` with the
   same `_parse_judgment_body`, so ids/titles/order still match section-for-section
   and the client-side enhancer swaps in place. Steps 4–5 must re-verify this.
7. ⬜ **ISR purge** — every affected page is 24h-cached. `/api/revalidate` sweep is
   mandatory, per `project_isr_bake_docker_cache_trap`. **Not yet run — nothing
   here is deployed.**

### What step 3 measured, after the fact (10,000 published أحكام)

| | Before | After |
|---|---|---|
| Mean body exposure | **42.0%** | **17.2%** |
| Corpus-wide (bytes served ÷ bytes held) | 39.1% | **12.7%** |
| Gated rulings exposing >50% | **3,012** | **0** |
| Rulings exposing ≥90% | 846 | 0 |
| Downgraded to honest `open` | — | 184 (1.84%) |

The 184 downgrades give nothing away that was not already given: under the old
rule they averaged **99.3%** exposed, 166 of them exactly 100%. They now ship
without a dead CTA and publish their وزارة العدل source link.

Two decisions taken during the build, both worth knowing before steps 4–5 reuse
the primitives:

- **A truncated section EXHAUSTS the allowance.** `truncate_for_gate` cuts at the
  last whitespace inside the window, so a truncation leaves a few unspent chars;
  carrying them forward put a 3-character stub («قصي») under the next section's
  heading. Not a preview — corrupted text.
- **`is_free` was redefined** from "sits in the free layer" to "reached the reader
  whole". With one shared budget there are no layers, only where the allowance ran
  out. `is_truncated` still drives the render, so no component changed.

## 5. The audit that replaces guessing

✅ `scripts/gate_audit.sql` **§7** now carries the exposure measure — §7a the
shipped judgment rule, §7b the regulation before-picture. `gated_but_over_half`
in §7a must stay 0; a nonzero row means the withheld floor has been breached.
Re-run it before touching any dial rather than trusting a remembered number.

✅ `test_gated_judgment_withholds_the_majority_of_the_ruling` asserts the same
invariant per item (`withheld_pct >= MIN_WITHHELD_RATIO*100`,
`withheld_chars >= MIN_WITHHELD_CHARS`).

⬜ Extend both to the remaining wings as steps 4–5 land.

The constants were originally chosen by feel and drifted 25–30 pts from their
stated intent (§2, the judgment comment). A test is the only thing that keeps
them honest as the corpus grows.

The constants were originally chosen by feel and drifted 25–30 pts away from
their stated intent (§2, the judgment comment). A test is the only thing that
keeps them honest as the corpus grows.

## 6. Out of scope / already decided

- The 54 open-tier أنظمة stay 100% open — `OPEN MEANS OPEN`.
- Bulk-harvest defence (sitemap flattening, hub traversal, Cloudflare) is
  `project_cloudflare_navigation_hardening` + `project_scraping_assessment`, not
  this. This plan changes **what one page gives away**, not **how many pages a
  scraper can reach**.
- `services` (4,717 items) fall back to `open` by design in `resolve_gate`.

Related: `.claude/plans/access_tiers_gating_DECISIONS.md` (authority) ·
`.claude/plans/seo_public_library.md` · `.claude/plans/defence_in_depth.md`
