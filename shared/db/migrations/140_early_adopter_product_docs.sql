-- ════════════════════════════════════════════════════════════════════════════
-- 140 — product_docs: the router may mention المشتركون الأوائل, qualitatively
-- ════════════════════════════════════════════════════════════════════════════
--
-- Spec: .claude/plans/early_adopters.md §7 ("Copy elsewhere" → product_docs.pricing).
-- Depends on: 126 (product_docs), 138 (the campaign itself — although this file
--             is independent of it and may be applied before, after, or without
--             138; it changes text only).
-- Idempotent: one guarded UPDATE that appends nothing if the paragraph is
--             already there. Re-runnable.
--
-- WHAT AND WHY ────────────────────────────────────────────────────────────────
-- `product_docs` is what the router reads before answering any question about
-- ريحان itself (agents/tool_repository/rayhan_docs.py → open_rayhan_page). The
-- `pricing` row deliberately states NO amounts — 126's header and the plan's §7
-- both spell out why: plans.price_sar is what checkout charges,
-- frontend/lib/pricing.ts is what the page displays, those two have already
-- drifted apart once and are pinned together by hand, and a third copy would be
-- the one nobody updates while the router quotes it to a paying customer with
-- total confidence.
--
-- The owner's decision for the campaign is the same one, applied to a new fact:
-- the router MAY say there is an offer for المشتركون الأوائل and that المقاعد
-- محدودة; it may NOT say the price, the seat count, how many are left, or when
-- it ends. §1 rule 10 of the plan is absolute — the remaining count is never
-- disclosed, not on the page, not in the API, not in an error message — and a
-- language model with a number in its context is a model that will eventually
-- say the number.
--
-- ⚠ THE APPENDED TEXT IS HEDGED ON PURPOSE ("من وقت لآخر", "لا تؤكّد أنّ العرض
--   ساري الآن"). This row is static and the campaign is a boolean in another
--   table that flips without a deploy. A doc that asserted a live offer would
--   become a false claim the moment seat 100 filled — made by the product's own
--   assistant, about its own prices, to somebody deciding whether to buy. So the
--   doc teaches that the offer EXISTS as a thing, and sends every question about
--   whether it is running, and at what price, to /pricing — which is generated
--   from the live campaign state.
--
-- ⚠ THIS FILE EDITS LIVE CONTENT, AND THE ROW IS THE ONLY COPY. 126 shipped
--   without a seeder by owner decision: the 15 launch rows were written straight
--   into the database and there is nothing in this repo to restore them from.
--   BEFORE RUNNING, SAVE THE CURRENT BODY:
--
--     SELECT content_md FROM public.product_docs WHERE doc_key = 'pricing';
--
--   The UPDATE below only APPENDS — it never rewrites what is there — but a copy
--   costs one query and an accidental edit is a content loss, not a re-run.
--
-- ⚠ NOT INSTANT, AND THAT IS FINE. rayhan_docs.py caches each doc in-process for
--   600 s, so the change reaches users within ten minutes of the UPDATE with no
--   redeploy. That TTL is the whole reason this content lives in a table.
--
-- ⚠ TOUCHES NOTHING ELSE. No schema change, no policy change, no other row.
--   `is_published`, `catalog`, `canonical_path`, `title` and `blurb` are left
--   exactly as they are; trg_product_docs_updated_at bumps updated_at, which is
--   what it is for.

BEGIN;

DO $$
DECLARE
    v_n      INTEGER;
    v_exists BOOLEAN;
    -- The marker is the Arabic campaign name itself: if the body already
    -- contains it, this migration (or a console edit that said the same thing)
    -- has already run. Cheaper and more honest than a version column on a table
    -- whose editing workflow is "a human in the Supabase console".
    v_marker TEXT := 'المشتركون الأوائل';
    -- Assembled with explicit || rather than adjacent literals: PostgreSQL only
    -- continues a string across lines when the next piece opens with a bare
    -- quote, so a stack of E'…' pieces is a syntax error, not a concatenation.
    -- Explicit operators also survive an editor that reflows RTL text.
    v_section TEXT :=
        '## عرض «المشتركون الأوائل»'
        || E'\n\n'
        || 'يُطلق ريحان من وقت لآخر عرضاً ترويجياً باسم «المشتركون الأوائل»: '
        || 'سعر تمهيدي لفترة محدودة في بداية الاشتراك، ثم يعود السعر إلى السعر '
        || 'المعتاد. والمقاعد في هذا العرض محدودة.'
        || E'\n\n'
        || 'عند أي سؤال عن هذا العرض:'
        || E'\n\n'
        || '- لا تؤكّد أنّ العرض ساري الآن ولا أنّه انتهى؛ فقد يكون مفتوحاً '
        || 'وقد يكون مغلقاً في أي لحظة.'
        || E'\n'
        || '- لا تذكر أي مبلغ، ولا عدد المقاعد ولا المتبقي منها، ولا تاريخ '
        || 'انتهاء العرض. هذه الأرقام لا تُذكر في هذا المستند إطلاقاً.'
        || E'\n'
        || '- أحِل المستخدم إلى صفحة الأسعار https://rayhanai.com/pricing '
        || 'لمعرفة السعر الحالي وما إذا كان العرض ما زال متاحاً — الصفحة وحدها '
        || 'تعرض الحالة الفعلية.'
        || E'\n';
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM public.product_docs d
         WHERE d.doc_key = 'pricing' AND d.catalog = 'about'
    ) INTO v_exists;

    IF NOT v_exists THEN
        -- A WARNING, not an exception: the row is authored by hand in the
        -- console (126), so "it is not there" is an operator fact to act on,
        -- not a reason to fail a migration that changes nothing else. If this
        -- fires, add the paragraph to the pricing doc by hand.
        RAISE WARNING
            '140: no product_docs row with doc_key=''pricing'' AND catalog=''about'' '
            '— NOTHING WAS UPDATED. The router will not mention المشتركون الأوائل. '
            'Check the row exists (126) and re-run, or paste the section by hand.';
        RETURN;
    END IF;

    UPDATE public.product_docs d
       SET content_md = rtrim(d.content_md, E' \t\r\n') || E'\n\n' || v_section
     WHERE d.doc_key = 'pricing'
       AND d.catalog = 'about'
       AND position(v_marker IN d.content_md) = 0;

    GET DIAGNOSTICS v_n = ROW_COUNT;

    IF v_n = 0 THEN
        RAISE NOTICE
            '140: the pricing doc already mentions %, nothing appended (re-run).',
            v_marker;
    ELSE
        RAISE NOTICE
            '140: appended the المشتركون الأوائل section to product_docs.pricing. '
            'Live for the router within the 600s cache TTL (rayhan_docs.py).';
    END IF;
END $$;

COMMIT;


-- ════════════════════════════════════════════════════════════════════════════
-- POST-APPLY VERIFICATION — run manually
-- ════════════════════════════════════════════════════════════════════════════
--
-- -- 1. The section landed exactly ONCE. The appended text uses the phrase twice
-- --    (the heading and the opening sentence), so EXPECT n_marker = 2. A 4 means
-- --    the section was appended twice — the guard failed and one copy must be
-- --    trimmed by hand.
-- SELECT (length(content_md) - length(replace(content_md, 'المشتركون الأوائل', '')))
--        / length('المشتركون الأوائل')  AS n_marker,
--        length(content_md)             AS chars
--   FROM public.product_docs WHERE doc_key = 'pricing';
--
-- -- 1b. ⚠ THE NO-NUMBERS CHECK, scoped to the APPENDED SECTION ONLY (the rest of
-- --     the body predates this migration and legitimately discusses the points
-- --     model). The section is written with NO digit in it at all — not a price,
-- --     not a seat count, not a day count, and even the URL is digit-free — so
-- --     the check is absolute: EXPECT has_any_digit = false, mentions_riyals =
-- --     false, mentions_seat_count = false.
-- WITH tail AS (
--     SELECT substr(content_md,
--                   position('## عرض «المشتركون الأوائل»' IN content_md)) AS t
--       FROM public.product_docs WHERE doc_key = 'pricing'
-- )
-- SELECT t ~ '[0-9٠-٩]'        AS has_any_digit,      -- Latin AND Arabic-Indic
--        t ILIKE '%ريال%'      AS mentions_riyals,
--        t ILIKE '%مقعد%'      AS mentions_seat_count,
--        length(t)             AS section_chars
--   FROM tail;
-- -- (The scarcity line uses the PLURAL «المقاعد … محدودة», which does not
-- --  contain the singular «مقعد» — so the tripwire is aimed squarely at the one
-- --  phrasing a count would take, «… مقعداً», and must read false.)
--
-- -- 2. Nothing else about the row moved. EXPECT: catalog='about',
-- --    is_published=true, canonical_path='/pricing' (whatever it was before).
-- SELECT doc_key, catalog, title, canonical_path, is_published, sort_order, updated_at
--   FROM public.product_docs WHERE doc_key = 'pricing';
--
-- -- 3. No other doc was touched. EXPECT: only the pricing row's updated_at is
-- --    recent.
-- SELECT doc_key, updated_at FROM public.product_docs ORDER BY updated_at DESC LIMIT 5;
--
-- -- 4. Live check, ≥10 minutes after applying (or after a backend restart, which
-- --    clears the in-process cache): ask ريحان «هل يوجد عرض على الاشتراك؟» and
-- --    confirm it opens the `pricing` doc, mentions المقاعد محدودة, quotes NO
-- --    number, and links https://rayhanai.com/pricing.
--
-- ── ROLLBACK, if the copy needs pulling ──────────────────────────────────────
-- -- The append is a suffix, so removing it is a suffix strip. Do it in the
-- -- console against the saved copy from the header, or:
-- -- UPDATE public.product_docs
-- --    SET content_md = rtrim(left(content_md,
-- --                           position('## عرض «المشتركون الأوائل»' IN content_md) - 1),
-- --                           E' \t\r\n')
-- --  WHERE doc_key = 'pricing'
-- --    AND position('## عرض «المشتركون الأوائل»' IN content_md) > 0;
-- -- Or, faster and blunter, retract the whole doc without a deploy:
-- --   UPDATE public.product_docs SET is_published = false WHERE doc_key='pricing';
-- -- (the router then answers "I don't have this" and points at the public pages
-- --  — see _NOT_FOUND in rayhan_docs.py — which is a worse answer than the doc
-- --  but never a wrong one.)
