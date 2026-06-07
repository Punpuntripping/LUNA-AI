-- 064_drop_system_templates.sql
-- Remove the system-wide `system_templates` feature entirely. The writer
-- pipeline now uses ONLY the per-user قوالبي library (`user_templates`,
-- migration 055).
--
-- Background: `system_templates` (046) + its semantic-search RPC (054) shipped
-- but were never ingested (0 rows in production) and the RPC is no longer
-- called — the `search_templates` planner tool + `distill/template_search.py`
-- helper were removed in the same change
-- (.claude/plans/writer_planner_user_templates.md § Wave A).
--
-- Idempotent: guarded DROPs; safe to re-run.

-- ============================================
-- 1. RPC (migration 054) — drop every overload regardless of signature drift.
-- ============================================
DO $$
DECLARE r record;
BEGIN
    FOR r IN
        SELECT oid::regprocedure AS sig
        FROM pg_proc
        WHERE proname = 'search_system_templates'
          AND pronamespace = 'public'::regnamespace
    LOOP
        EXECUTE 'DROP FUNCTION IF EXISTS ' || r.sig || ' CASCADE';
    END LOOP;
END $$;

-- ============================================
-- 2. Table (migration 046) — CASCADE drops its indexes, trigger, RLS policies.
-- ============================================
DROP TABLE IF EXISTS public.system_templates CASCADE;

-- ============================================
-- 3. Enum (migration 046) — only ever used by system_templates.type.
--    Guarded: if some other object still references it, leave it in place.
-- ============================================
DO $$
BEGIN
    DROP TYPE IF EXISTS template_type_enum;
EXCEPTION WHEN dependent_objects_still_exist THEN
    RAISE NOTICE 'template_type_enum still referenced — left in place';
END $$;
