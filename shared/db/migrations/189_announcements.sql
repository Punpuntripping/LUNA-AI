-- Migration 189: «رسائل ريحان» — product announcements shown in-app (web + app).
--
-- Plan: .claude/plans/announcements.md. Owner decisions 2026-10-26:
--   * a message targets everyone ('all') or the paying subscribers ('paid');
--   * 'paid' = an unexpired subscription on a PURCHASABLE plan (plans.price_sar
--     IS NOT NULL → basic/pro/max today). Promo (ترويجي), dev and free are out.
--     Evaluated when the user reads, not snapshotted at publish — someone who
--     subscribes next week still gets a 'paid' message that is still listed;
--   * every unread message pops up once; closing it marks it read; the bell
--     beside the settings gear carries the unread count; /messages lists all.
--
-- Authoring = INSERT a row (no admin UI). `published_at` in the future schedules
-- it. Deleting a row deletes its read receipts (CASCADE).
--
-- Access: backend service role only. Both tables have RLS ON with NO client
-- policies, and the RPC is EXECUTE-revoked from anon/authenticated — the
-- audience rule must not be bypassable by a direct PostgREST read (a free user
-- listing 'paid' rows, or anyone reading another user's receipts).

CREATE TABLE IF NOT EXISTS public.announcements (
    announcement_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title           text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 120),
    body_md         text NOT NULL CHECK (length(btrim(body_md)) BETWEEN 1 AND 8000),
    audience        text NOT NULL CHECK (audience IN ('all', 'paid')),
    -- Optional call-to-action button. Both or neither; the URL is an in-site
    -- path ("/learn/…", not "//host") or an absolute https URL.
    link_label      text CHECK (link_label IS NULL OR length(btrim(link_label)) BETWEEN 1 AND 40),
    link_url        text CHECK (link_url IS NULL OR link_url ~ '^(/([^/]|$)|https://)'),
    published_at    timestamptz NOT NULL DEFAULT now(),
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT announcements_link_pair CHECK ((link_label IS NULL) = (link_url IS NULL))
);

CREATE INDEX IF NOT EXISTS announcements_published_at_idx
    ON public.announcements (published_at DESC);

CREATE TABLE IF NOT EXISTS public.announcement_reads (
    user_id         uuid NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    announcement_id uuid NOT NULL REFERENCES public.announcements(announcement_id) ON DELETE CASCADE,
    read_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, announcement_id)
);

ALTER TABLE public.announcements      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.announcement_reads ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.announcements      FROM anon, authenticated;
REVOKE ALL ON public.announcement_reads FROM anon, authenticated;

-- The ONE definition of "what this user may see", newest first, with read state.
-- Called by backend/app/services/announcement_service.py for both the list and
-- the mark-read visibility check.
CREATE OR REPLACE FUNCTION public.get_user_announcements(p_user_id uuid)
RETURNS TABLE (
    announcement_id uuid,
    title           text,
    body_md         text,
    link_label      text,
    link_url        text,
    published_at    timestamptz,
    is_read         boolean
)
LANGUAGE sql
STABLE
SET search_path = public
AS $$
    WITH paid AS (
        SELECT EXISTS (
            SELECT 1
              FROM user_subscriptions s
              JOIN plans p ON p.plan_id = s.plan_id
             WHERE s.user_id = p_user_id
               AND p.price_sar IS NOT NULL
               AND (s.expires_at IS NULL OR s.expires_at > now())
        ) AS is_paid
    )
    SELECT a.announcement_id, a.title, a.body_md, a.link_label, a.link_url,
           a.published_at, (r.user_id IS NOT NULL) AS is_read
      FROM announcements a
      CROSS JOIN paid
      LEFT JOIN announcement_reads r
             ON r.announcement_id = a.announcement_id
            AND r.user_id = p_user_id
     WHERE a.published_at <= now()
       AND (a.audience = 'all' OR (a.audience = 'paid' AND paid.is_paid))
     ORDER BY a.published_at DESC
     LIMIT 50;
$$;

REVOKE EXECUTE ON FUNCTION public.get_user_announcements(uuid) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.get_user_announcements(uuid) TO service_role;

-- The first message (owner's text, 2026-10-26). Guarded on the title so a
-- re-run does not post it twice.
INSERT INTO public.announcements (title, body_md, audience, link_label, link_url)
SELECT 'طوّرنا ريحان: أسرع وأدق',
       E'طورنا ريحان من ناحية زمن الاستجابة والجودة.\n\n'
       'استعملنا نماذج أحسن وأسرع لخدمتكم مع تحسن معتبر بجودة الإجابات، والتحسن طال جميع الوكلاء الأساسيين: الباحث والكاتب.\n\n'
       'مثال: الباحث المعمق كان يخلص في 3-5 دقائق، صار يخلص في 1.5 إلى 3.5 دقيقة.\n\n'
       'كان يستهلك 3-7 نقاط، صار يستهلك من 5-10 نقاط.\n\n'
       'وعلى أثر هذا التغيير زاد استخدام البيانات، فقررنا نرفع جميع حدود الاستخدام على جميع الاشتراكات المدفوعة:\n\n'
       '- **الأساسية:** من 10 إلى 15 نقطة للجلسة، ومن 50 إلى 75 طوال الاشتراك\n'
       '- **الاحترافية:** من 15 إلى 20 نقطة للجلسة، ومن 75 إلى 100 أسبوعياً\n'
       '- **القصوى:** من 75 إلى 100 نقطة للجلسة، ومن 375 إلى 500 أسبوعياً',
       'paid',
       'سياسة حد الاستخدام',
       '/learn/usage-limits'
WHERE NOT EXISTS (
    SELECT 1 FROM public.announcements WHERE title = 'طوّرنا ريحان: أسرع وأدق'
);

-- Verification
--   SELECT relname, relrowsecurity FROM pg_class
--    WHERE relname IN ('announcements', 'announcement_reads');      -- both t
--   SELECT has_function_privilege('authenticated',
--          'public.get_user_announcements(uuid)', 'EXECUTE');         -- f
--   SELECT title, audience, published_at FROM public.announcements;  -- 1 row, paid
