-- Gate audit — what is open vs gated, as the code actually resolves it.
--
-- ⚠ THERE IS NO "gate" COLUMN. The gate is computed at request time by
-- library_service.resolve_gate() from three sources, in this order:
--
--   (a) seo_item_meta.gate_override        ('open' | 'gated' | NULL = inherit)
--   (b) articles only: the PARENT regulation's gate_override, else its seo_tier
--   (c) regulations only: its own seo_tier ('open' = flagship, NULL = inherit)
--   (d) seo_gate_defaults.default_gate     (the per-wing policy)
--   (e) fallback 'gated', EXCEPT content_type='service' which falls back 'open'
--
-- Two further code-side effects that no column records, both in §3 below:
--   * effective_circular_gate(): a gated تعميم <= 800 chars renders fully open.
--   * truncate_for_gate(): returns text UNCHANGED when it is already shorter
--     than the free budget. So a "gated" item whose body fits the budget is
--     served complete. Per-section budgets: article 500, circular 400, form 300,
--     regulation doc sections 600 (first 3 only).
--
-- ⚠ /judgments NO LONGER WORKS THAT WAY. It is the first wing on the exposure
-- budget (`.claude/plans/gate_exposure_budget.md`): ONE allowance per document,
-- clamp(15% of the ruling, 600, 2000), spent across sections in reading order,
-- plus a withheld floor that marks a too-short ruling honestly 'open' rather
-- than paywalling a document it is not withholding. §7 measures all of it.
-- The remaining wings are still on the per-section budgets above — which is why
-- §7b's regulation numbers are so much worse than §7a's.
--
-- Change a gate with scripts/set_gate.py (it also triggers ISR revalidation);
-- do not UPDATE seo_item_meta by hand or the published page keeps the old gate
-- until its 24h ISR window expires.
--
-- All queries below are READ-ONLY.


-- =====================================================================
-- 1. The policy layer — the per-wing defaults everything inherits from
-- =====================================================================
select content_type, default_gate, notes
from seo_gate_defaults
order by content_type;


-- =====================================================================
-- 2. Resolved gate per wing — the headline count
--    (mirrors resolve_gate steps a/c/d/e; articles handled in §4)
-- =====================================================================
with resolved as (
  select m.content_type, m.slug,
         case
           when m.gate_override in ('open','gated') then m.gate_override
           when m.content_type = 'regulation' and m.seo_tier in ('open','gated') then m.seo_tier
           else coalesce(d.default_gate, case when m.content_type = 'service' then 'open' else 'gated' end)
         end as gate
  from seo_item_meta m
  left join seo_gate_defaults d on d.content_type = m.content_type
)
select content_type, gate,
       count(*)                                as rows_total,
       count(*) filter (where slug is not null) as published   -- slug NOT NULL = live
from resolved
group by 1, 2
order by 1, 2;


-- =====================================================================
-- 3. Circulars — the EFFECTIVE gate, after the <=800-char auto-open
--    (a 'gated' row here can still render fully to an anonymous visitor)
-- =====================================================================
with c as (
  select m.slug, length(coalesce(ci.content, '')) as len,
         case when m.gate_override in ('open','gated') then m.gate_override else 'gated' end as gate
  from seo_item_meta m
  join circulars ci on ci.id = m.content_id::uuid
  where m.content_type = 'circular'
)
select case when gate = 'gated' and len <= 800 then 'open (auto: short body)' else gate end as effective_gate,
       count(*)                                 as circulars,
       count(*) filter (where slug is not null) as published
from c
group by 1
order by 1;


-- =====================================================================
-- 4. مواد — inherited gate AND what an anonymous visitor actually receives
--    'anon_gets_full_text' = open-tier parent, OR body already under the
--    500-char free budget. THIS is the number that matters.
-- =====================================================================
with art as (
  select length(coalesce(a.article_text, '')) as len,
         case
           when pm.gate_override in ('open','gated') then pm.gate_override
           when pm.seo_tier     in ('open','gated') then pm.seo_tier
           else 'gated'
         end as gate
  from seo_articles a
  left join seo_item_meta pm
    on pm.content_type = 'regulation' and pm.content_id = a.regulation_id::text
)
select gate,
       count(*)                                                            as articles,
       count(*) filter (where gate = 'open' or len <= 500)                 as anon_gets_full_text,
       round(100.0 * count(*) filter (where gate = 'open' or len <= 500)
             / nullif(count(*), 0), 1)                                     as pct_full,
       round(avg(len))                                                     as avg_len,
       percentile_disc(0.5) within group (order by len)                    as median_len
from art
group by gate
order by gate;


-- =====================================================================
-- 5. Look up specific items — change the ILIKE pattern / content_type
-- =====================================================================
select m.content_type, m.slug, m.seo_tier, m.gate_override, d.default_gate,
       case
         when m.gate_override in ('open','gated') then m.gate_override
         when m.content_type = 'regulation' and m.seo_tier in ('open','gated') then m.seo_tier
         else coalesce(d.default_gate, 'gated')
       end                                as resolved_gate,
       (m.slug is not null)               as is_published
from seo_item_meta m
left join seo_gate_defaults d on d.content_type = m.content_type
where m.content_type = 'regulation'
  and m.slug ilike '%العمل%'
order by resolved_gate, m.slug;


-- =====================================================================
-- 6. The 54 open-tier flagship statutes, and how much text they publish free
-- =====================================================================
select m.slug,
       count(a.id)                                as articles,
       sum(length(coalesce(a.article_text, '')))  as free_chars
from seo_item_meta m
join seo_articles a on a.regulation_id = m.content_id::uuid
where m.content_type = 'regulation' and m.seo_tier = 'open'
group by m.slug
order by free_chars desc;


-- =====================================================================
-- 7. EXPOSURE — how much of each document the gate actually gives away
--
-- §2 of `.claude/plans/gate_exposure_budget.md`. The counts in §2–§6 above say
-- WHICH items are gated; they cannot say whether "gated" withholds anything.
-- That gap is how the wing shipped at 42% exposure while its own code comment
-- claimed 85–90% withheld. These queries are the measure, and they are the
-- thing to re-run before changing any dial — never trust a remembered number.
--
-- 7a. /judgments under the SHIPPED rule (gate_decision + JUDGMENT_BUDGET =
--     0.15 / 600 / 2000). Approximates the word-boundary cut to the character,
--     which is within a few chars of what the service serves.
-- =====================================================================
with m as (select content_id from seo_item_meta where content_type = 'judgment'),
j as (select c.id, c.content from cases c join m on m.content_id = c.id::text),
-- The service measures the PARSED body (frontmatter already stripped), so raw
-- length(content) would overstate every document.
sec as (
  select j.id, btrim(s.txt) as txt
  from j, regexp_split_to_table(j.content, E'\n##\s+[^\n]*\n') with ordinality as s(txt, ord)
  where length(btrim(s.txt)) > 0
),
per as (select id, sum(length(txt))::int as total from sec group by id),
decided as (
  select id, total,
         least(greatest(round(0.15 * total), 600), 2000)::int as target,
         -- the deepest serve that still clears MIN_WITHHELD_CHARS / _RATIO
         least(total - 800, floor(total * 0.5))::int          as max_servable
  from per
)
select count(*)                                                as judgments,
       count(*) filter (where max_servable < 600)              as downgraded_to_open,
       round(avg(100.0 * least(case when max_servable >= 600
                                    then least(target, max_servable) else total end,
                               total) / total), 1)             as avg_pct_exposed,
       count(*) filter (where max_servable >= 600
                          and least(target, max_servable)::numeric / total > 0.5)
                                                               as gated_but_over_half
from decided;
-- Expected on the 2026-08-10 corpus: 10,000 · 184 open · 17.2% · 0 over half.
-- `gated_but_over_half` MUST stay 0 — a nonzero row means the withheld floor
-- in gate_decision has been breached and «gated» has stopped meaning anything.


-- =====================================================================
-- 7b. /regulations — NOT YET RE-GATED (plan step 4). Numbers here are the
--     BEFORE picture: first-3-sections × 600 chars, plus llm_summary free.
-- =====================================================================
with meta as (select content_id::uuid as rid from seo_item_meta where content_type = 'regulation'),
ch as (
  select c.regulation_id as rid, length(c.content) as len,
         row_number() over (partition by c.regulation_id order by c.position) as rn
  from chunks_v2 c where c.regulation_id in (select rid from meta)
),
agg as (
  select m.rid,
         count(ch.*)                                                          as n_chunks,
         coalesce(sum(ch.len), 0)                                             as total,
         coalesce(sum(case when ch.rn <= 3 then least(ch.len, 600) else 0 end), 0) as body_free,
         coalesce(length(r.llm_summary), 0)                                   as summary_free
  from meta m
  join regulations_v2 r on r.id = m.rid
  left join ch on ch.rid = m.rid
  group by 1, r.llm_summary
)
select count(*)                                                            as regs,
       count(*) filter (where n_chunks <= 3)                               as short_docs,
       round(avg(100.0 * body_free / nullif(total, 0)), 1)                  as avg_pct_body_free,
       round(avg(100.0 * (body_free + summary_free) / nullif(total, 0)), 1) as avg_pct_free_incl_summary,
       count(*) filter (where (body_free + summary_free) >= total)          as fully_exposed
from agg;
-- 2026-08-10: 3,446 · 877 short · 11.5% body · 28.7% incl. summary · 57 fully
-- exposed. `llm_summary` is the dominant term — fixing only the نص budget moves
-- the headline by 0.3 pt. Decision recorded in the plan §3.3: it is spent from
-- the document budget.
