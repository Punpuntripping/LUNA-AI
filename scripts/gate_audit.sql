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
--     served complete. Budgets: article 500, circular 400, form 300,
--     judgment 1200/section, regulation doc sections 600.
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
