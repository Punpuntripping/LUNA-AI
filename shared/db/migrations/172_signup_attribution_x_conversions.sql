-- 172_signup_attribution_x_conversions.sql
--
-- Signup source (all channels) + X Conversions API delivery state.
-- Request: marketing/marketing_content/X/ads/request_to_luna_x_conversions.md (X4, X7).
--
-- ⚠ A SEPARATE 1:1 TABLE, not columns on public.users. The request asks for
-- "service-role only: no client read". `authenticated` keeps table-level SELECT
-- on users (166 §2 — 53 RLS policies resolve the caller through it), and a
-- column REVOKE cannot carve out of a table-level grant. A table with RLS on,
-- no policies and no client grants is the only clean way to make these columns
-- invisible to the browser. Join on user_id.
--
-- The ONLY value about a user that ever goes to X is x_twclid (X's own click
-- ID). Nothing here holds email/phone/IP/UA.
--
-- Idempotent: safe to re-run.

begin;

create table if not exists public.user_signup_attribution (
    user_id               uuid primary key references public.users(user_id) on delete cascade,
    -- channel at signup, every channel (not only X). For X ads utm_campaign is
    -- the per-ad id <flight>_<campaign>_<text>.
    signup_utm_source     text,
    signup_utm_campaign   text,
    -- X click id, only when they arrived from an X ad.
    x_twclid              text,
    x_signup_sent_at      timestamptz,
    -- The FIRST paid purchase, recorded whether or not the send succeeds, so
    -- the retry can re-send with the original id/time and a later purchase can
    -- never take its place (first purchase only).
    x_purchase_payment_id text,
    x_purchase_at         timestamptz,
    x_purchase_sent_at    timestamptz,
    created_at            timestamptz not null default now(),
    updated_at            timestamptz not null default now(),
    constraint usa_twclid_shape   check (x_twclid is null or x_twclid ~ '^[A-Za-z0-9_-]{1,200}$'),
    constraint usa_utm_source_len check (signup_utm_source is null or char_length(signup_utm_source) <= 120),
    constraint usa_utm_campaign_len check (signup_utm_campaign is null or char_length(signup_utm_campaign) <= 120)
);

comment on table public.user_signup_attribution is
  'Signup channel + X CAPI delivery state (172). Service-role only. Only x_twclid is ever sent to X.';

-- Retry scans (X7).
create index if not exists usa_x_signup_pending_idx
    on public.user_signup_attribution (created_at)
    where x_twclid is not null and x_signup_sent_at is null;
create index if not exists usa_x_purchase_pending_idx
    on public.user_signup_attribution (x_purchase_at)
    where x_twclid is not null and x_purchase_payment_id is not null and x_purchase_sent_at is null;

alter table public.user_signup_attribution enable row level security;
revoke all on public.user_signup_attribution from anon, authenticated;
grant select, insert, update, delete on public.user_signup_attribution to service_role;

-- ---------------------------------------------------------------------------
-- Write-once upsert: fills only columns that are still NULL. A second call is
-- a no-op that returns the current row.
-- ---------------------------------------------------------------------------
create or replace function public.record_signup_attribution(
    p_user_id      uuid,
    p_twclid       text,
    p_utm_source   text,
    p_utm_campaign text
) returns public.user_signup_attribution
language sql
set search_path = public
as $$
    insert into public.user_signup_attribution as t
        (user_id, x_twclid, signup_utm_source, signup_utm_campaign)
    values (p_user_id, p_twclid, p_utm_source, p_utm_campaign)
    on conflict (user_id) do update set
        x_twclid            = coalesce(t.x_twclid, excluded.x_twclid),
        signup_utm_source   = coalesce(t.signup_utm_source, excluded.signup_utm_source),
        signup_utm_campaign = coalesce(t.signup_utm_campaign, excluded.signup_utm_campaign),
        updated_at          = case
            when (t.x_twclid is null and excluded.x_twclid is not null)
              or (t.signup_utm_source is null and excluded.signup_utm_source is not null)
              or (t.signup_utm_campaign is null and excluded.signup_utm_campaign is not null)
            then now() else t.updated_at end
    returning t.*;
$$;

-- ---------------------------------------------------------------------------
-- Claim the FIRST purchase for an X-attributed user. Returns the row when this
-- payment is (or already was) the first purchase and is not yet delivered;
-- NULL otherwise (no twclid, a later purchase, or already sent).
-- ---------------------------------------------------------------------------
create or replace function public.claim_x_first_purchase(
    p_user_id    uuid,
    p_payment_id text,
    p_paid_at    timestamptz
) returns public.user_signup_attribution
language plpgsql
set search_path = public
as $$
declare
    r public.user_signup_attribution;
begin
    update public.user_signup_attribution
       set x_purchase_payment_id = p_payment_id,
           x_purchase_at         = coalesce(p_paid_at, now()),
           updated_at            = now()
     where user_id = p_user_id
       and x_twclid is not null
       and x_purchase_payment_id is null
    returning * into r;

    if found then
        return r;
    end if;

    select * into r
      from public.user_signup_attribution
     where user_id = p_user_id
       and x_twclid is not null
       and x_purchase_payment_id = p_payment_id
       and x_purchase_sent_at is null;
    return r;  -- NULL when no row matched
end;
$$;

revoke all on function public.record_signup_attribution(uuid, text, text, text) from public, anon, authenticated;
revoke all on function public.claim_x_first_purchase(uuid, text, timestamptz) from public, anon, authenticated;
grant execute on function public.record_signup_attribution(uuid, text, text, text) to service_role;
grant execute on function public.claim_x_first_purchase(uuid, text, timestamptz) to service_role;

commit;

-- X7 — the daily retry is an in-process APScheduler job (backend/app/main.py,
-- x_conversions_retry), like every other daily sweep; nothing scheduled here.

-- Verification (read-only):
--   select grantee, privilege_type from information_schema.role_table_grants
--    where table_name = 'user_signup_attribution';          -- no anon/authenticated
