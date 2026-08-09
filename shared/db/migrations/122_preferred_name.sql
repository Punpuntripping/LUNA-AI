-- ════════════════════════════════════════════════════════════════════════════
-- 122 — «بماذا تحب أن نناديك؟» : users.preferred_name + a real name for
--        Google sign-ins
-- ════════════════════════════════════════════════════════════════════════════
--
-- Depends on: 003 (users), 091 (the current handle_new_user body), 094 (the
--             marketing_opt_in column that body writes).
-- Idempotent: ADD COLUMN IF NOT EXISTS, CREATE OR REPLACE FUNCTION, and a
--             backfill whose WHERE clause is false on a second run.
--
-- ⚠ APPLY THIS BEFORE DEPLOYING THE BACKEND. `GET /auth/me` selects
--   `preferred_name` and `PATCH /auth/preferred-name` writes it; a backend
--   deployed first 42703s on every profile read (the 119/120 lesson).
--
-- WHAT ───────────────────────────────────────────────────────────────────────
--   1. `users.preferred_name` — what the user asked to be called, typed in
--      إعدادات الحساب. NULL = never answered → the app derives a default from
--      `full_name_ar` (see shared/identity.py). The router injects the result
--      so replies can address the user by name.
--   2. `handle_new_user` now reads Google's `name` / `full_name` metadata keys
--      before falling back to the email.
--   3. A backfill for the rows the old fallback already spoiled.
--
-- WHY THE TRIGGER CHANGE ─────────────────────────────────────────────────────
-- The old COALESCE was `full_name_ar → email`. Only OUR signup form writes
-- `full_name_ar` into user metadata; Google's OIDC profile writes `name` and
-- `full_name`. So every Google sign-in landed with `full_name_ar = email` —
-- 6 of 24 rows at the time of writing, every one of them a Google account.
-- Nothing crashed, because an email is a valid string; it just meant the app
-- had no name for those users, and the sidebar showed their email.
--
-- The email fallback is KEPT as the last resort: `full_name_ar` is NOT NULL,
-- so the insert needs something. `shared/identity.py` is what refuses to treat
-- an email as a name at render time — the column may hold one, no surface
-- will call the user by it.
--
-- ROLLBACK ───────────────────────────────────────────────────────────────────
--   ALTER TABLE public.users DROP COLUMN IF EXISTS preferred_name;
--   -- and restore the pre-122 COALESCE in handle_new_user (see 091).
--   -- The backfill is recoverable without an archive: the rows it touched are
--   -- exactly those where full_name_ar was the email, and email is still
--   -- there in its own column.
-- ════════════════════════════════════════════════════════════════════════════

-- ── 1. The column ───────────────────────────────────────────────────────────

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS preferred_name VARCHAR(60);

COMMENT ON COLUMN public.users.preferred_name IS
    'What the user asked to be called (إعدادات الحساب → «بماذا تحب أن نناديك؟»), '
    'migration 122. NULL = never answered; the app then derives a first name '
    'from full_name_ar via shared/identity.py:resolve_call_name. Rendered into '
    'the router''s instructions, so it is length-capped and stripped of control '
    'characters at the API boundary.';

-- ── 2. Signup trigger: pick up Google's name ────────────────────────────────
-- Body identical to 091's except for the full_name_ar COALESCE.

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
AS $function$
DECLARE
    v_user_id uuid;
BEGIN
    INSERT INTO public.users (auth_id, email, full_name_ar, terms_accepted_at, terms_version, marketing_opt_in)
    VALUES (
        NEW.id,
        NEW.email,
        -- NULLIF(btrim(...)) so a whitespace-only metadata value falls through
        -- instead of becoming the name.
        COALESCE(
            NULLIF(btrim(NEW.raw_user_meta_data->>'full_name_ar'), ''),  -- our signup form
            NULLIF(btrim(NEW.raw_user_meta_data->>'name'), ''),          -- Google OIDC
            NULLIF(btrim(NEW.raw_user_meta_data->>'full_name'), ''),     -- Google OIDC (alias)
            NEW.email                                                    -- last resort: NOT NULL
        ),
        now(),
        NEW.raw_user_meta_data->>'terms_version',
        COALESCE((NEW.raw_user_meta_data->>'marketing_opt_in')::boolean, true)
    )
    RETURNING user_id INTO v_user_id;

    BEGIN
        INSERT INTO public.user_subscriptions (user_id, plan_id, source)
        VALUES (v_user_id, 'free', 'signup')
        ON CONFLICT (user_id) DO NOTHING;
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING 'handle_new_user: user_subscriptions seed failed for %: %',
            v_user_id, SQLERRM;
    END;

    RETURN NEW;
END;
$function$;

-- ── 3. Backfill the rows the old fallback spoiled ───────────────────────────
-- Only touches rows whose full_name_ar IS the email (i.e. the fallback fired)
-- AND whose auth metadata actually carries a name. Re-running is a no-op: the
-- first run makes full_name_ar <> email.

UPDATE public.users u
SET full_name_ar = v.metadata_name
FROM (
    SELECT
        au.id,
        COALESCE(
            NULLIF(btrim(au.raw_user_meta_data->>'full_name_ar'), ''),
            NULLIF(btrim(au.raw_user_meta_data->>'name'), ''),
            NULLIF(btrim(au.raw_user_meta_data->>'full_name'), '')
        ) AS metadata_name
    FROM auth.users au
) v
WHERE v.id = u.auth_id
  AND v.metadata_name IS NOT NULL
  AND v.metadata_name <> u.email
  AND u.full_name_ar = u.email;
