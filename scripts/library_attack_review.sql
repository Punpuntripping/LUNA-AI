-- Monthly library attack review (library_guardrails_hardening.md Phase 5).
-- Read-only. Sections 1-4 run in Supabase; 5 is Cloudflare GraphQL; 6 is the SQL of
-- the Logfire alerts, kept here so they can be rebuilt.
--
-- Baselines at 2026-11-01, 30 days: busiest account 18 unlocks, most regulation
-- unlocks by one account in a day 7, largest session 56 library page views, most
-- carries by one account in an hour 1.


-- 1. Who unlocks the most. A ledger read that grows without use_count growing beside
--    it is the shape of farming.
SELECT user_id,
       count(*)                                         AS unlocks,
       count(*) FILTER (WHERE content_type = 'regulation') AS regulation_unlocks,
       sum(cost)                                        AS cost,
       count(DISTINCT date_trunc('day', unlocked_at))   AS active_days,
       max(unlocked_at)                                 AS last_at
FROM library_unlocks
WHERE unlocked_at >= now() - interval '30 days'
GROUP BY user_id
ORDER BY unlocks DESC
LIMIT 20;


-- 2. The busiest regulation day per account. Alert (b) fires above 50.
SELECT user_id,
       date_trunc('day', unlocked_at) AS day,
       count(*)                       AS regulation_unlocks
FROM library_unlocks
WHERE unlocked_at >= now() - interval '30 days'
  AND content_type = 'regulation'
GROUP BY user_id, date_trunc('day', unlocked_at)
ORDER BY regulation_unlocks DESC
LIMIT 20;


-- 3. The largest reading sessions. Human pace tops out around one page every 15 s.
SELECT session_key,
       count(*)                          AS library_page_views,
       min(occurred_at)                  AS started_at,
       max(occurred_at) - min(occurred_at) AS span
FROM analytics_events
WHERE occurred_at >= now() - interval '30 days'
  AND event_name = 'page_view'
  AND (path LIKE '/regulations%' OR path LIKE '/judgments%' OR path LIKE '/circulars%'
       OR path LIKE '/compliance%' OR path LIKE '/forms%' OR path LIKE '/library%')
GROUP BY session_key
ORDER BY library_page_views DESC
LIMIT 20;


-- 4. Carries per account per hour, from the rows themselves. M-6 is open by the
--    owner's decision, so this and alert (c) are the only watch on that path.
SELECT user_id,
       date_trunc('hour', created_at)            AS hour,
       count(*)                                  AS carries,
       count(DISTINCT metadata->>'source_page_key') AS distinct_pages
FROM workspace_items
WHERE created_at >= now() - interval '30 days'
  AND metadata->>'subtype' = 'library_page'
GROUP BY user_id, date_trunc('hour', created_at)
ORDER BY carries DESC
LIMIT 20;


-- 5. Cloudflare GraphQL: what the edge blocked (POST /client/v4/graphql).
--    Use firewallEventsAdaptive with action_neq "skip": the *Groups datasets are denied
--    on the Free plan, and looping over days drains the budget after about 15 calls.
--
--    query ($zone: String!, $since: Time!, $until: Time!) {
--      viewer { zones(filter: { zoneTag: $zone }) {
--        firewallEventsAdaptive(
--          limit: 10000
--          orderBy: [datetime_DESC]
--          filter: { datetime_geq: $since, datetime_lt: $until, action_neq: "skip" }
--        ) {
--          datetime action source ruleId
--          clientRequestHTTPHost clientRequestPath userAgent
--          clientAsn clientCountryName
--        }
--      } }
--    }


-- 6. The Logfire alerts. Project rihan, channel «Rayhan Alerts (Discord)», frequency
--    at least PT10M (our plan refuses less), notify_when starts_having_matches.
--    Create them after the code is deployed: the events do not exist before that.
--
--    rate_limit.refused and origin_lock.outcome fold, so always SUM count.

-- (a) More than 20 origin-lock refusals in an hour outside health: someone is probing.
--     time_window: 1 hour
SELECT attributes->>'host_class' AS host_class,
       attributes->>'outcome'    AS outcome,
       sum((attributes->>'count')::int) AS refused
FROM records
WHERE span_name = 'origin_lock.outcome'
  AND attributes->>'enforced' = 'true'
  AND attributes->>'outcome' <> 'match'
GROUP BY attributes->>'host_class', attributes->>'outcome'
HAVING sum((attributes->>'count')::int) > 20;

-- (b) Any account above 50 regulation-level unlocks in a day.
--     time_window: 1 day
SELECT attributes->>'library_user_id' AS library_user_id,
       count(*)                       AS regulation_unlocks
FROM records
WHERE span_name = 'library.unlock_written'
  AND attributes->>'content_type' = 'regulation'
GROUP BY attributes->>'library_user_id'
HAVING count(*) > 50;

-- (c) Any account above 30 carrier calls in an hour.
--     time_window: 1 hour
SELECT attributes->>'library_user_id' AS library_user_id,
       count(*)                       AS carrier_calls
FROM records
WHERE span_name = 'library.carrier_call'
GROUP BY attributes->>'library_user_id'
HAVING count(*) > 30;

-- (d) Readers refused while searching a hub, more than 5 in an hour. Grouped by limiter
--     on purpose: item_budget is the hourly meter, global is the per-minute bucket, and
--     they call for different fixes.
--     time_window: 1 hour
SELECT attributes->>'limiter' AS limiter,
       attributes->>'tier'    AS tier,
       sum((attributes->>'count')::int) AS refused
FROM records
WHERE span_name = 'rate_limit.refused'
  AND attributes->>'route' = 'hub_search'
  AND attributes->>'key_kind' = 'user'
GROUP BY attributes->>'limiter', attributes->>'tier'
HAVING sum((attributes->>'count')::int) > 5;
