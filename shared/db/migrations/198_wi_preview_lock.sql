-- ============================================================================
-- Migration 198 — workspace_items.created_while_paid: was the owner on a paid
--                 plan when this item was created? (WI preview lock)
--
-- Source: .claude/plans/wi_preview_lock.md §3 + §11 step 1 (owner decisions
--         2026-10-30). Live facts read from prod dwgghvxogtwyaxmbgjod on
--         2026-10-30 (SELECT only), immediately before this file:
--   * workspace_items has NO created_while_paid column; its BEFORE INSERT
--     triggers are assign_workspace_item_seq, set_workspace_item_word_count,
--     trg_workspace_items_cap (all SECURITY INVOKER) — none touches the new
--     column, and this trigger reads nothing they write.
--   * user_subscriptions: UNIQUE (user_id), plan_id → plans(plan_id) FK,
--     expires_at NULL = no expiry. Signup rows are plan 'free', no expiry.
--   * get_user_quota_state's effective plan (093 / 137, the definition
--     shared/quota.library_state().is_paid reads): plan_id NULL → locked;
--     expires_at <= now() → 'free'; else plan_id, joined to plans.
--   * 195 is applied: anon / authenticated hold no privilege on
--     workspace_items (re-checked in §3 — the lock depends on it).
--
-- WHY ────────────────────────────────────────────────────────────────────────
-- The preview lock is decided at READ time (shared/workspace/preview_lock.py):
--   locked = flag on AND kind lockable AND created_at >= WI_PREVIEW_LOCK_FROM
--            AND NOT created_while_paid AND NOT user_is_paid_now AND NOT demo
-- "Items made while paid stay open after the plan expires" (§9.2) needs the
-- plan AS IT WAS at creation, which no row records today. A BEFORE INSERT
-- trigger stamps it, so no publisher (deep search, simple search, writer,
-- artifact editor, blog import, …) changes: every insert path is covered,
-- including ones added later.
--
-- "PAID" — the SAME predicate as get_user_quota_state's effective plan, so the
-- stamp and the read-time check (library_state().is_paid) can never disagree:
--   the user's subscription row has a plan in the catalog, that plan is not
--   'free', and it has not expired (expires_at NULL or > now()).
-- STORE PURCHASES (194, not yet applied on 2026-10-30): an Apple / Google /
-- RevenueCat Test Store purchase is granted by grant_store_plan (Pro / Max)
-- or grant_plan with the store source (Basic) — both write the ordinary
-- user_subscriptions.plan_id + expires_at (the store's expiry). This function
-- reads ONLY those two columns and never `source` or any 194 column, so a
-- store-billed plan counts as paid exactly like a web one, and the file is
-- correct whether 194 is applied before it, after it, or never. §4 test D
-- proves it with source = 'apple'. (A sandbox / Test Store grant, livemode
-- false, grants a real plan row too — and counts as paid here, exactly as it
-- does for the quota gate.)
--
-- WHAT THIS FILE DOES ─────────────────────────────────────────────────────────
--   1. ADD COLUMN created_while_paid boolean NOT NULL DEFAULT false
--      (metadata-only: a constant default rewrites nothing). Existing rows read
--      false — harmless: they predate WI_PREVIEW_LOCK_FROM, so the date clause
--      keeps them open whatever this says. No backfill.
--   2. stamp_workspace_item_created_while_paid() + BEFORE INSERT trigger
--      trg_workspace_items_created_while_paid. It OVERWRITES whatever the
--      INSERT carried: no caller can claim "made while paid". SECURITY DEFINER
--      (pinned search_path) so the stamp does not depend on what the inserting
--      role may read of user_subscriptions — every insert today is
--      service_role or postgres, but a client role (none since 195) would
--      otherwise stamp false and lock its own paid-era items.
--   3. Self-tests: schema, trigger wiring, the 195 revoke still in force.
--   4. Behavioural self-tests on FAKE accounts, all rolled back (192's
--      pattern): free / paid / expired / store-billed / plan-less / spoofed
--      INSERT, each compared with get_user_quota_state itself.
--
-- SHIPS INERT: nothing reads the column until the backend with the lock is
-- deployed AND WI_PREVIEW_LOCK_ENABLED is turned on (default false). The only
-- runtime cost is one user_subscriptions lookup by its UNIQUE key per
-- workspace_items INSERT.
--
-- DEPLOY ORDER (§11): 198 → backend + agents (flag OFF) → web + mobile OTA →
-- 198b (product_docs) → flag ON with WI_PREVIEW_LOCK_FROM = that moment.
--
-- IDEMPOTENT: ADD COLUMN IF NOT EXISTS, CREATE OR REPLACE FUNCTION,
-- DROP TRIGGER IF EXISTS + CREATE. Safe to run twice. Run as postgres (SQL
-- editor / the pooler URL): §4 inserts fake auth.users rows, like 192.
--
-- LOCKS: ALTER TABLE takes ACCESS EXCLUSIVE on workspace_items until COMMIT
-- (every workspace read waits) — milliseconds at ~1,700 rows. lock_timeout
-- fails fast instead of queueing every request behind this transaction.
--
-- VERIFY AFTER APPLY:
--   SELECT column_name, data_type, is_nullable, column_default
--     FROM information_schema.columns
--    WHERE table_schema = 'public' AND table_name = 'workspace_items'
--      AND column_name = 'created_while_paid';
--   SELECT tgname, tgenabled, pg_get_triggerdef(oid) FROM pg_trigger
--    WHERE tgrelid = 'public.workspace_items'::regclass
--      AND tgname = 'trg_workspace_items_created_while_paid';
--   -- after the next agent item lands, the stamp vs the user's plan NOW
--   -- (equal unless the plan changed since):
--   SELECT w.created_at, w.kind, w.created_while_paid, s.plan_id, s.expires_at
--     FROM public.workspace_items w
--     JOIN public.user_subscriptions s ON s.user_id = w.user_id
--    ORDER BY w.created_at DESC LIMIT 20;
--
-- ROLLBACK (if ever needed — the backend treats a missing column as false):
--   DROP TRIGGER IF EXISTS trg_workspace_items_created_while_paid ON public.workspace_items;
--   DROP FUNCTION IF EXISTS public.stamp_workspace_item_created_while_paid();
--   ALTER TABLE public.workspace_items DROP COLUMN IF EXISTS created_while_paid;
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── 0. preconditions ─────────────────────────────────────────────────────────
DO $$
DECLARE
    v_row RECORD;
BEGIN
    FOR v_row IN
        SELECT * FROM (VALUES
            ('user_subscriptions', 'user_id'),
            ('user_subscriptions', 'plan_id'),
            ('user_subscriptions', 'expires_at'),
            ('plans',              'plan_id'),
            ('workspace_items',    'user_id'),
            ('workspace_items',    'created_at')
        ) c(tbl, col)
    LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_attribute a
                        WHERE a.attrelid = to_regclass('public.' || v_row.tbl)::oid
                          AND a.attname = v_row.col AND NOT a.attisdropped) THEN
            RAISE EXCEPTION '198: %.% is missing — nothing changed', v_row.tbl, v_row.col;
        END IF;
    END LOOP;

    -- The stamp reads ONE subscription row per user; the predicate below is
    -- only the quota RPC's if that holds.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint con
                    WHERE con.conrelid = 'public.user_subscriptions'::regclass
                      AND con.contype = 'u'
                      AND pg_get_constraintdef(con.oid) = 'UNIQUE (user_id)') THEN
        RAISE EXCEPTION '198: user_subscriptions is no longer UNIQUE (user_id) — re-read get_user_quota_state before applying';
    END IF;

    IF to_regprocedure('public.get_user_quota_state(uuid)') IS NULL THEN
        RAISE EXCEPTION '198: public.get_user_quota_state(uuid) is missing — nothing changed';
    END IF;
END $$;


-- ── 1. the column ────────────────────────────────────────────────────────────
ALTER TABLE public.workspace_items
    ADD COLUMN IF NOT EXISTS created_while_paid boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN public.workspace_items.created_while_paid IS
    'TRUE when the owner''s plan was paid at INSERT time (198, wi_preview_lock.md '
    '§3): a catalog plan other than ''free'' that had not expired — the same '
    'effective-plan rule as get_user_quota_state, store-billed plans (194: '
    'source apple / google / test_store) included. Stamped ONLY by '
    'trg_workspace_items_created_while_paid, which overwrites whatever the INSERT '
    'carried. The WI preview lock never locks such an item, even after the plan '
    'expires (§9.2). Pre-198 rows read FALSE; they predate WI_PREVIEW_LOCK_FROM, '
    'so the lock''s date clause keeps them open regardless.';


-- ── 2. the stamp ─────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.stamp_workspace_item_created_while_paid()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $function$
BEGIN
    -- get_user_quota_state's effective plan, inlined: NULL plan → not paid;
    -- expired → 'free' → not paid; the JOIN keeps the RPC's catalog lookup.
    -- Reads plan_id + expires_at only, never `source`: a store-billed plan
    -- (194) is paid exactly like a web one.
    NEW.created_while_paid := EXISTS (
        SELECT 1
          FROM public.user_subscriptions s
          JOIN public.plans p ON p.plan_id = s.plan_id
         WHERE s.user_id = NEW.user_id
           AND s.plan_id <> 'free'
           AND (s.expires_at IS NULL OR s.expires_at > now())
    );
    RETURN NEW;
END;
$function$;

COMMENT ON FUNCTION public.stamp_workspace_item_created_while_paid() IS
    'BEFORE INSERT on workspace_items (198): stamps created_while_paid from the '
    'owner''s user_subscriptions row — paid = a catalog plan other than ''free'', '
    'not expired (get_user_quota_state''s effective plan). Overwrites the INSERT''s '
    'value. SECURITY DEFINER so the stamp does not depend on the inserting role''s '
    'read access to user_subscriptions.';

-- Trigger functions cannot be called through PostgREST, but nobody needs
-- EXECUTE on this one directly either.
REVOKE EXECUTE ON FUNCTION public.stamp_workspace_item_created_while_paid()
    FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS trg_workspace_items_created_while_paid ON public.workspace_items;
CREATE TRIGGER trg_workspace_items_created_while_paid
    BEFORE INSERT ON public.workspace_items
    FOR EACH ROW EXECUTE FUNCTION public.stamp_workspace_item_created_while_paid();


-- ── 3. static self-tests ─────────────────────────────────────────────────────
DO $$
DECLARE
    v_n int;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns c
         WHERE c.table_schema = 'public' AND c.table_name = 'workspace_items'
           AND c.column_name = 'created_while_paid'
           AND c.data_type = 'boolean' AND c.is_nullable = 'NO'
           AND c.column_default = 'false') THEN
        RAISE EXCEPTION '198: workspace_items.created_while_paid is not boolean NOT NULL DEFAULT false';
    END IF;

    SELECT count(*) INTO v_n
      FROM pg_trigger tg
     WHERE tg.tgrelid = 'public.workspace_items'::regclass
       AND tg.tgname = 'trg_workspace_items_created_while_paid'
       AND NOT tg.tgisinternal
       AND tg.tgenabled = 'O'
       AND tg.tgfoid = to_regprocedure('public.stamp_workspace_item_created_while_paid()')::oid
       AND (tg.tgtype & 1) = 1      -- FOR EACH ROW
       AND (tg.tgtype & 2) = 2      -- BEFORE
       AND (tg.tgtype & 4) = 4      -- INSERT
       AND (tg.tgtype & 16) = 0;    -- not UPDATE
    IF v_n <> 1 THEN
        RAISE EXCEPTION '198: trg_workspace_items_created_while_paid is not a BEFORE INSERT row trigger on the stamp';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p
         WHERE p.oid = to_regprocedure('public.stamp_workspace_item_created_while_paid()')
           AND p.prosecdef
           AND p.proconfig @> ARRAY['search_path=public, pg_temp']) THEN
        RAISE EXCEPTION '198: the stamp must be SECURITY DEFINER with a pinned search_path';
    END IF;

    -- The lock is only as good as 195: a client that can UPDATE its own rows
    -- rewrites created_at / created_while_paid and walks round it.
    SELECT count(*) INTO v_n
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public' AND table_name = 'workspace_items'
       AND grantee IN ('anon', 'authenticated', 'PUBLIC');
    IF v_n <> 0 THEN
        RAISE EXCEPTION '198: % client-role privilege(s) on workspace_items — apply 195 first', v_n;
    END IF;

    RAISE NOTICE '198: column, trigger, SECURITY DEFINER stamp and the 195 revoke verified';
END $$;


-- ── 4. behavioural self-tests — fake accounts, ALL ROLLED BACK ───────────────
-- Inside one inner block that ends by raising a private SQLSTATE (LU198) and
-- catching it, so PostgreSQL rolls back every fake auth.users / users /
-- user_subscriptions / workspace_items row. A failed assertion raises P0001,
-- is NOT caught, and aborts the migration. Each case is also checked against
-- get_user_quota_state, so the stamp and the read-time check provably agree.
-- The test items carry a summary (the AFTER INSERT summarizer only fires on
-- summary IS NULL) and a short body — no webhook is ever queued.
--   A  free (signup default)                       → false
--   B  paid pro, not expired                       → true
--   C  the same plan, expired                      → false
--   D  store-billed pro (source 'apple', 194)      → true
--   E  plan-less (locked account)                  → false
--   F  free, INSERT claims created_while_paid=true → false (overwritten)
DO $$
DECLARE
    v_run   text := to_char(clock_timestamp(), 'YYYYMMDDHH24MISSUS');
    v_users bigint := (SELECT count(*) FROM public.users);
    v_items bigint := (SELECT count(*) FROM public.workspace_items);
    v_auth  uuid;
    v_uid   uuid;
    v_case  record;
    v_got   boolean;
    v_rpc   boolean;
BEGIN
    IF NOT has_table_privilege('auth.users', 'INSERT') THEN
        RAISE EXCEPTION
            '198 self-test: this role cannot INSERT into auth.users (fake accounts '
            'for the behavioural tests). Run the migration as postgres (SQL editor).';
    END IF;

    BEGIN
        v_auth := gen_random_uuid();
        INSERT INTO auth.users (id, instance_id, aud, role, email,
                                raw_app_meta_data, raw_user_meta_data,
                                created_at, updated_at)
        VALUES (v_auth, '00000000-0000-0000-0000-000000000000', 'authenticated',
                'authenticated', format('selftest-198-%s@selftest.invalid', v_run),
                '{}'::jsonb, '{}'::jsonb, now(), now());
        SELECT u.user_id INTO v_uid FROM public.users u WHERE u.auth_id = v_auth;
        IF v_uid IS NULL THEN
            RAISE EXCEPTION '198 self-test: on_auth_user_created built no public.users row';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.user_subscriptions s WHERE s.user_id = v_uid) THEN
            RAISE EXCEPTION '198 self-test: the fake account has no user_subscriptions row';
        END IF;

        FOR v_case IN
            SELECT * FROM (VALUES
                ('A free',          false, false),
                ('B paid pro',      true,  false),
                ('C expired pro',   false, false),
                ('D store pro',     true,  false),
                ('E plan-less',     false, false),
                ('F spoofed insert', false, true)
            ) c(label, want, spoof)
        LOOP
            -- Arrange the subscription for this case. A statement naming
            -- plan_id fires trg_user_subscriptions_assignment (expires_at =
            -- now() + duration); expiry is then written ALONE.
            CASE left(v_case.label, 1)
                WHEN 'A', 'F' THEN
                    UPDATE public.user_subscriptions s
                       SET plan_id = 'free', source = 'signup' WHERE s.user_id = v_uid;
                    UPDATE public.user_subscriptions s
                       SET expires_at = NULL WHERE s.user_id = v_uid;
                WHEN 'B' THEN
                    UPDATE public.user_subscriptions s
                       SET plan_id = 'pro', source = 'payment' WHERE s.user_id = v_uid;
                    UPDATE public.user_subscriptions s
                       SET expires_at = now() + interval '30 days' WHERE s.user_id = v_uid;
                WHEN 'C' THEN
                    UPDATE public.user_subscriptions s
                       SET expires_at = now() - interval '1 day' WHERE s.user_id = v_uid;
                WHEN 'D' THEN
                    UPDATE public.user_subscriptions s
                       SET source = 'apple', expires_at = now() + interval '30 days'
                     WHERE s.user_id = v_uid;
                WHEN 'E' THEN
                    UPDATE public.user_subscriptions s
                       SET plan_id = NULL, source = 'signup' WHERE s.user_id = v_uid;
            END CASE;

            INSERT INTO public.workspace_items
                (user_id, kind, created_by, title, content_md, summary, created_while_paid)
            VALUES
                (v_uid, 'agent_search', 'agent', '198 self-test', 'نص اختبار قصير.',
                 'ملخص اختبار', v_case.spoof)
            RETURNING created_while_paid INTO v_got;

            SELECT (q.effective_plan_id IS NOT NULL AND q.effective_plan_id <> 'free')
              INTO v_rpc
              FROM public.get_user_quota_state(v_uid) q;
            v_rpc := COALESCE(v_rpc, false);

            IF v_got IS DISTINCT FROM v_case.want THEN
                RAISE EXCEPTION '198 self-test [%]: created_while_paid = %, expected %',
                    v_case.label, v_got, v_case.want;
            END IF;
            IF v_got IS DISTINCT FROM v_rpc THEN
                RAISE EXCEPTION '198 self-test [%]: stamp % disagrees with get_user_quota_state (paid = %)',
                    v_case.label, v_got, v_rpc;
            END IF;
        END LOOP;

        RAISE EXCEPTION USING ERRCODE = 'LU198', MESSAGE = '198 self-test sentinel — rolling back';
    EXCEPTION WHEN SQLSTATE 'LU198' THEN
        RAISE NOTICE '198: behavioural self-tests A–F passed (stamp = quota RPC); every test write rolled back';
    END;

    IF (SELECT count(*) FROM public.users) <> v_users
       OR (SELECT count(*) FROM public.workspace_items) <> v_items THEN
        RAISE EXCEPTION '198 self-test left rows behind (users / workspace_items counts changed)';
    END IF;
    IF EXISTS (SELECT 1 FROM auth.users a WHERE a.email LIKE 'selftest-198-%') THEN
        RAISE EXCEPTION '198 self-test left fake auth.users rows behind';
    END IF;

    RAISE NOTICE '198 OK: created_while_paid stamped on insert, nothing left behind';
END $$;

COMMIT;
